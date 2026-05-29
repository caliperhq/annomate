"""Generic chat-VLM adapter — free-form Q&A about images.

Wraps any HuggingFace ``transformers`` model that follows the
``AutoModelForImageTextToText`` interface and supports
``apply_chat_template`` (Qwen-VL family, Llava-Next, SmolVLM,
Phi-3.5-Vision, Idefics3, etc.).

Capability: ``ask``. Takes an image (typically a region crop) plus a
free-form question and returns the model's text response.

The default config picks ``Qwen/Qwen2.5-VL-3B-Instruct`` (Apache 2.0,
~6 GB on disk). Users can swap to a smaller / different model by
editing ``[ask.default]`` in models.toml. This adapter handles any
model whose ID matches the registered prefixes below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import re

from annomate.models.base import Adapter, Answer, Verdict
from annomate.models.registry import register_adapter

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
    label_toks = _tokens(label)
    caption_toks = _tokens(caption)
    if not label_toks:
        return (False, 0.0)
    matched = label_toks & caption_toks
    if not matched:
        return (False, 0.2)
    coverage = len(matched) / len(label_toks)
    return (True, 0.5 + 0.5 * coverage)


def _extract_suggested_label(caption: str) -> str | None:
    text = caption.strip().rstrip(".")
    # Strip generic VLM opener ("The image shows a ...", "This photo depicts ...").
    text = re.sub(
        r"(?i)^(?:the\s+)?(?:image|photo|photograph|picture)\s+"
        r"(?:shows?|depicts?|contains?|features?|displays?)\s+",
        "", text,
    ).strip()
    m = re.match(r"^\s*(?:an?|the)\s+([a-z]+(?:\s+[a-z]+){0,2})", text, re.I)
    if m:
        return m.group(1).strip()
    parts = re.findall(r"[A-Za-z']+", text)
    return " ".join(parts[:3]) if parts else None

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class ChatVlmAdapter(Adapter):
    capabilities = ("ask", "verify")

    # Rough on-disk weights for budget estimation. Anything not listed
    # falls back to 2000 — better to over-estimate than under.
    _SIZE_MB = {
        "Qwen/Qwen2.5-VL-3B-Instruct": 6400,
        "Qwen/Qwen2.5-VL-7B-Instruct": 14600,
        "HuggingFaceTB/SmolVLM-Instruct": 2200,
        "HuggingFaceTB/SmolVLM-256M-Instruct": 500,
        "microsoft/Phi-3.5-vision-instruct": 8000,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self.weights_mb_estimate = self._SIZE_MB.get(model_id, 2000)
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

    def load(self) -> None:
        if self._loaded:
            return
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        device = self._resolve_device(self.device)
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        self._processor = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True)
        self._model = AutoModelForImageTextToText.from_pretrained(
            self.model_id,
            torch_dtype=dtype,
            trust_remote_code=True,
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

    def ask(
        self,
        image: "PILImage",
        question: str,
        *,
        max_new_tokens: int = 256,
        system_prompt: str | None = None,
    ) -> Answer:
        if not self._loaded:
            self.load()
        torch = self._torch

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": question},
            ],
        })

        # apply_chat_template produces the text with image-token placeholders.
        # Qwen2.5-VL needs process_vision_info to extract PIL images from the
        # messages dict before passing them to the processor; other VLMs are
        # fine with [image] directly.
        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        try:
            from qwen_vl_utils import process_vision_info
            image_inputs, video_inputs = process_vision_info(messages)
        except ImportError:
            image_inputs, video_inputs = [image], None
        inputs = self._processor(
            text=[text], images=image_inputs,
            videos=video_inputs or None,
            padding=True, return_tensors="pt",
        )
        inputs = {k: (v.to(self._resolved_device) if hasattr(v, "to") else v)
                  for k, v in inputs.items()}

        with torch.no_grad():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        # Slice off the prompt tokens so we decode only the response.
        input_len = inputs["input_ids"].shape[1]
        new_tokens = generated[:, input_len:]
        text_out = self._processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        return Answer(
            question=question,
            text=text_out,
            finish_reason="length" if new_tokens.shape[1] >= max_new_tokens else "stop",
            tokens_generated=int(new_tokens.shape[1]),
        )


    def verify(self, image_crop: "PILImage", label: str) -> Verdict:
        """Crop-and-verify using a plain description prompt.

        Asks the model to describe the crop, then checks whether the claimed
        label appears in the response using the same token-overlap heuristic
        as the Florence-2 adapter it replaces.
        """
        answer = self.ask(
            image_crop,
            "What do you see?",
            max_new_tokens=200,
        )
        caption = answer.text
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


# Register for known chat-VLM prefixes. Users can add their own by
# editing the config; if a new model_id matches none of these prefixes,
# acquire() will raise NotInstalledError with the missing-factory hint.
for _prefix in (
    "Qwen/Qwen2.5-VL-",
    "Qwen/Qwen2-VL-",
    "HuggingFaceTB/SmolVLM",
    "microsoft/Phi-3.5-vision",
    "llava-hf/llava-",
    "llava-hf/llama3-llava-",
    "HuggingFaceM4/Idefics3-",
):
    register_adapter(_prefix,
                     lambda model_id, device="auto", **kw: ChatVlmAdapter(model_id, device, **kw))
