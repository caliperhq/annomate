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

from annotate.models.base import Adapter, Verdict
from annotate.models.registry import register_adapter

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

    def load(self) -> None:
        if self._loaded:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        device = self._resolve_device(self.device)
        # Florence-2 requires trust_remote_code=True (custom modeling files).
        self._processor = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id, trust_remote_code=True,
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
