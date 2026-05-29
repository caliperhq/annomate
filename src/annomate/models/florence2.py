"""Florence-2 adapter — crop-and-verify a labelled region.

Wraps ``microsoft/Florence-2-{base,large}`` from HuggingFace ``transformers``.
Florence-2 is a unified VLM that takes task-tag prompts (``<CAPTION>``,
``<MORE_DETAILED_CAPTION>``, ``<OD>``, etc.) and returns structured
output for that task.

Verification strategy: ask Florence for a detailed caption of the
cropped region, then compare keyword overlap against the claimed label.
This catches the corpus's most common failure mode — the
label-swap (04-haefeli pilot↔windscreen) — because the caption will
describe a *helmet* even if the region is labelled *windscreen*.

The verdict is heuristic; the full caption is returned in ``supporting``
so the LLM can make the final judgment.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from annomate.models.base import Adapter, Verdict
from annomate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


# Tiny stop-list for label-token matching; not a full stemmer.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "in", "on", "with", "at", "to",
    "for", "by", "is", "are", "was", "were", "be", "being", "been",
    "this", "that", "these", "those", "it", "its",
})


def _tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9']+", text.lower())
        if w not in _STOPWORDS and len(w) > 1
    }


def _label_match(caption: str, label: str) -> tuple[bool, float]:
    """Heuristic: is the label present in the caption? Returns (yes, confidence)."""
    label_tokens = _tokens(label)
    caption_tokens = _tokens(caption)
    if not label_tokens:
        return (False, 0.0)
    matched = label_tokens & caption_tokens
    if not matched:
        return (False, 0.2)
    coverage = len(matched) / len(label_tokens)
    return (True, 0.5 + 0.5 * coverage)


def _extract_suggested_label(caption: str) -> str | None:
    """Pull the first plausible noun phrase from the caption (first 1–3
    tokens after an opening article)."""
    text = caption.strip().rstrip(".")
    m = re.match(r"^\s*(?:an?|the)\s+([a-z]+(?:\s+[a-z]+){0,2})", text, re.I)
    if m:
        return m.group(1).strip()
    # Fall back to the first three words
    parts = re.findall(r"[A-Za-z']+", text)
    return " ".join(parts[:3]) if parts else None


class Florence2Adapter(Adapter):
    capabilities = ("verify",)

    _SIZE_MB = {
        "microsoft/Florence-2-base": 470,
        "microsoft/Florence-2-large": 1600,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self.weights_mb_estimate = self._SIZE_MB.get(model_id, 500)
        self._model = None
        self._processor = None
        self._torch = None
        self._resolved_device = None

    @staticmethod
    def _resolve_device(requested: str) -> str:
        import torch
        if requested == "auto":
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
            return "cpu"
        return requested

    def _patch_florence2_config_compat(self) -> None:
        """Patch Florence2LanguageConfig for transformers >= 5.x compatibility.

        Florence-2's __init__ checks self.forced_bos_token_id after super().__init__(),
        but transformers 5.x PretrainedConfig.__post_init__ pops all GenerationConfig
        parameters (including forced_bos_token_id) before the kwargs->setattr loop, so
        the attribute is never stored on the instance. Setting it as a class attribute
        provides the fallback the MRO lookup needs.

        Idempotent -- guarded by _annotate_f2_compat flag on the class.
        """
        try:
            from transformers.dynamic_module_utils import get_class_from_dynamic_module
            cls = get_class_from_dynamic_module(
                "configuration_florence2.Florence2LanguageConfig",
                self.model_id,
                trust_remote_code=True,
            )
        except Exception:
            return

        if getattr(cls, "_annotate_f2_compat", False):
            return

        # transformers 5.x PretrainedConfig.__post_init__ pops all GenerationConfig
        # parameters (including forced_bos_token_id) from kwargs before the setattr
        # loop, so kwargs injection never works. Setting it as a class attribute
        # provides the MRO fallback that Florence-2's __init__ needs.
        try:
            cls.forced_bos_token_id = None
        except (AttributeError, TypeError):
            object.__setattr__(cls, "forced_bos_token_id", None)
        # tie_word_embeddings was removed from PretrainedConfig in transformers 5.x.
        # Florence-2's checkpoint never stored it, so it must be set on the class
        # so _tie_weights() correctly ties lm_head / embed_tokens to model.shared.
        if not hasattr(cls, "tie_word_embeddings"):
            try:
                cls.tie_word_embeddings = True
            except (AttributeError, TypeError):
                object.__setattr__(cls, "tie_word_embeddings", True)
        cls._annotate_f2_compat = True

    def _patch_florence2_tokenizer_compat(self) -> None:
        """Patch PreTrainedTokenizerBase for transformers >= 5.x compatibility.

        Florence-2's processor reads tokenizer.additional_special_tokens, but
        transformers 5.x renamed this attribute to extra_special_tokens. Adding
        a read-only property alias makes old remote code work without modification.

        Idempotent -- guarded by an existence check.
        """
        try:
            from transformers import PreTrainedTokenizerBase
        except Exception:
            return
        if hasattr(PreTrainedTokenizerBase, "additional_special_tokens"):
            return
        PreTrainedTokenizerBase.additional_special_tokens = property(
            lambda self: self.extra_special_tokens
        )

    def _patch_florence2_model_compat(self) -> None:
        """Patch Florence2ForConditionalGeneration for transformers >= 5.x compatibility.

        prepare_inputs_for_generation accesses past_key_values[0][0].shape[2] using
        the old tuple-of-tuples KV cache format. transformers 5.x returns an
        EncoderDecoderCache object with a get_seq_length() API instead.

        Strategy: pre-slice decoder_input_ids using the new API, then call the original
        with past_key_values=None (skipping its broken indexing), then restore the real
        cache into the return dict.

        Idempotent -- guarded by _annotate_f2_model_compat flag on the class.
        """
        try:
            from transformers.dynamic_module_utils import get_class_from_dynamic_module
            # generate() delegates to self.language_model.generate(), whose
            # prepare_inputs_for_generation is Florence2LanguageForConditionalGeneration.
            cls = get_class_from_dynamic_module(
                "modeling_florence2.Florence2LanguageForConditionalGeneration",
                self.model_id,
                trust_remote_code=True,
            )
        except Exception:
            return

        if getattr(cls, "_annotate_f2_model_compat", False):
            return

        orig_prepare = cls.prepare_inputs_for_generation

        def _compat_prepare(model_self, decoder_input_ids, past_key_values=None, **kwargs):
            if past_key_values is not None and not isinstance(past_key_values, tuple):
                # New Cache object: compute past_length via its API, pre-slice, then
                # call orig without the cache so it doesn't hit the broken tuple indexing.
                past_length = past_key_values.get_seq_length()
                if decoder_input_ids.shape[1] > past_length:
                    decoder_input_ids = decoder_input_ids[:, past_length:]
                else:
                    decoder_input_ids = decoder_input_ids[:, -1:]
                result = orig_prepare(model_self, decoder_input_ids, past_key_values=None, **kwargs)
                result["past_key_values"] = past_key_values
                return result
            return orig_prepare(model_self, decoder_input_ids, past_key_values=past_key_values, **kwargs)

        cls.prepare_inputs_for_generation = _compat_prepare
        cls._annotate_f2_model_compat = True

    def load(self) -> None:
        if self._loaded:
            return
        self._patch_florence2_config_compat()
        self._patch_florence2_tokenizer_compat()
        self._patch_florence2_model_compat()
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        device = self._resolve_device(self.device)
        # Florence-2 requires trust_remote_code=True (custom modeling files).
        self._processor = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True)
        # attn_implementation="eager" bypasses the _supports_sdpa check that
        # transformers 5.x added but Florence-2's remote model code doesn't implement.
        # torch_dtype=float32 ensures consistent dtype on CPU (model loads some
        # weights as float16 by default, which causes conv forward-pass type errors).
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id, trust_remote_code=True,
            attn_implementation="eager",
            torch_dtype=torch.float32,
        ).to(device)
        self._model.eval()
        self._torch = torch
        self._resolved_device = device
        self._loaded = True

    def unload(self) -> None:
        if not self._loaded:
            return
        del self._model
        del self._processor
        self._model = None
        self._processor = None
        if self._torch is not None and self._resolved_device == "cuda":
            self._torch.cuda.empty_cache()
        self._loaded = False

    def verify(self, image_crop: "PILImage", label: str) -> Verdict:
        if not self._loaded:
            self.load()
        torch = self._torch

        task_prompt = "<MORE_DETAILED_CAPTION>"
        inputs = self._processor(text=task_prompt, images=image_crop, return_tensors="pt")
        inputs = {k: (v.to(self._resolved_device) if hasattr(v, "to") else v)
                  for k, v in inputs.items()}

        with torch.no_grad():
            generated_ids = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=256,
                num_beams=3,
                do_sample=False,
                use_cache=False,
            )
        generated_text = self._processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = self._processor.post_process_generation(
            generated_text,
            task=task_prompt,
            image_size=(image_crop.width, image_crop.height),
        )
        caption = (parsed.get(task_prompt) or "").strip()

        matches, confidence = _label_match(caption, label)
        suggested = _extract_suggested_label(caption) if not matches else None

        return Verdict(
            label_claimed=label,
            verdict="yes" if matches else "no" if caption else "unsure",
            confidence=confidence,
            supporting=[caption] if matches else [],
            contradicting=[caption] if not matches and caption else [],
            suggested_label=suggested,
        )


register_adapter("microsoft/Florence-2-",
                 lambda model_id, device="auto", **kw: Florence2Adapter(model_id, device, **kw))
