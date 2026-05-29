"""GroundingDINO adapter — open-vocabulary detection from text prompts.

Wraps the HuggingFace ``transformers`` implementation
(``IDEA-Research/grounding-dino-tiny`` by default; the same code handles
``-base`` since the API is identical). Lazy-imports torch + transformers
in ``load()`` so the base package stays torch-free.

This file *registers itself* on import: importing
``annomate.models.grounding_dino`` calls ``register_adapter`` for the
``IDEA-Research/grounding-dino-`` prefix. The server lazily imports this
module the first time ``acquire("detect", "detect")`` is called for a
matching pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annomate.models.base import Adapter, Detection
from annomate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class GroundingDinoAdapter(Adapter):
    """Open-vocab detector. ``capabilities = ('detect',)``."""

    capabilities = ("detect",)

    # Rough weight footprints; for memory budgeting only.
    _SIZE_MB = {
        "IDEA-Research/grounding-dino-tiny": 750,
        "IDEA-Research/grounding-dino-base": 1500,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self.weights_mb_estimate = self._SIZE_MB.get(model_id, 750)
        self._model = None
        self._processor = None
        self._torch = None
        self._resolved_device = None

    @staticmethod
    def _resolve_device(requested: str) -> str:
        """Map ``'auto'`` to the best available device for this host."""
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
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        device = self._resolve_device(self.device)
        processor = AutoProcessor.from_pretrained(self.model_id)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(self.model_id)
        model.to(device)
        model.eval()
        self._torch = torch
        self._processor = processor
        self._model = model
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

    def detect(
        self,
        image: "PILImage",
        prompts: list[str],
        *,
        min_confidence: float = 0.3,
        max_per_prompt: int = 100,
    ) -> list[Detection]:
        """Run open-vocab detection. Returns Detection objects with bbox in
        **fraction space** of the input image's own dims (callers map back
        to the original image when they tiled)."""
        if not self._loaded:
            self.load()
        if not prompts:
            return []
        torch = self._torch
        # GroundingDINO expects ". "-separated prompts in a single string,
        # lower-cased, trailing period.
        text = " . ".join(p.strip().lower().rstrip(".") for p in prompts) + " ."
        inputs = self._processor(images=image, text=text, return_tensors="pt")
        inputs = {k: v.to(self._resolved_device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self._model(**inputs)
        results = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            box_threshold=min_confidence,
            text_threshold=min_confidence,
            target_sizes=[image.size[::-1]],   # (H, W)
        )[0]
        boxes = results["boxes"].detach().cpu().tolist()
        scores = results["scores"].detach().cpu().tolist()
        labels = results.get("text_labels") or results.get("labels") or []

        W, H = image.size
        detections: list[Detection] = []
        for (x0, y0, x1, y1), score, label in zip(boxes, scores, labels):
            x_frac = max(0.0, x0 / W)
            y_frac = max(0.0, y0 / H)
            w_frac = max(0.0, min(1.0, (x1 - x0) / W))
            h_frac = max(0.0, min(1.0, (y1 - y0) / H))
            detections.append(Detection(
                xy=[2, x_frac, y_frac, w_frac, h_frac],
                label=str(label),
                score=float(score),
            ))
        # cap to avoid pathological prompt sets returning thousands
        detections.sort(key=lambda d: d.score, reverse=True)
        return detections[:max_per_prompt * max(1, len(prompts))]


# Register on import. The server imports this module from the AI dispatch
# path; if [ai] isn't installed the import will fail at `from transformers`
# inside load(), not here, so the registry stays usable for status queries.
register_adapter("IDEA-Research/grounding-dino-",
                 lambda model_id, device="auto", **kw: GroundingDinoAdapter(model_id, device, **kw))
