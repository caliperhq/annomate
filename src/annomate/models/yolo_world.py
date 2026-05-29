"""YOLO-World adapter — open-vocabulary YOLO for fast text-prompt detection.

Wraps Ultralytics' shipped YOLO-World weights (Apache 2.0 in that distro).
Order-of-magnitude faster than GroundingDINO on the same hardware —
suitable as a default detector when speed matters, especially on CPU.

Pipeline IDs accepted:
  - ``ultralytics:yolov8{s,m,l,x}-world``         (v1 — no ONNX export)
  - ``ultralytics:yolov8{s,m,l,x}-worldv2``       (v2 — exports to ONNX/TensorRT)

The ``.pt`` checkpoint is auto-downloaded by Ultralytics on first use
from its release artifacts; no separate HuggingFace fetch needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annomate.models.base import Adapter, Detection
from annomate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class YoloWorldAdapter(Adapter):
    capabilities = ("detect",)

    _SIZE_MB = {
        "yolov8s-world": 95, "yolov8s-worldv2": 95,
        "yolov8m-world": 220, "yolov8m-worldv2": 220,
        "yolov8l-world": 380, "yolov8l-worldv2": 380,
        "yolov8x-world": 580, "yolov8x-worldv2": 580,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        # model_id is "ultralytics:yolov8s-worldv2" — extract the weights name
        self._weights = model_id.split(":", 1)[1] if ":" in model_id else model_id
        if not self._weights.endswith(".pt"):
            self._weights_file = self._weights + ".pt"
        else:
            self._weights_file = self._weights
            self._weights = self._weights.removesuffix(".pt")
        self.weights_mb_estimate = self._SIZE_MB.get(self._weights, 200)
        self._model = None
        self._current_classes: list[str] | None = None
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
        from ultralytics import YOLO
        self._resolved_device = self._resolve_device(self.device)
        # Ultralytics auto-downloads the .pt from its release assets.
        self._model = YOLO(self._weights_file)
        self._loaded = True

    def unload(self) -> None:
        if not self._loaded:
            return
        del self._model
        self._model = None
        self._current_classes = None
        self._loaded = False

    def detect(
        self,
        image: "PILImage",
        prompts: list[str],
        *,
        min_confidence: float = 0.3,
        max_per_prompt: int = 100,
    ) -> list[Detection]:
        if not self._loaded:
            self.load()
        if not prompts:
            return []
        # Re-set classes only when they change (avoids redundant text encoding)
        if self._current_classes != prompts:
            self._model.set_classes(prompts)
            self._current_classes = list(prompts)

        W, H = image.size
        results = self._model.predict(
            source=image,
            conf=min_confidence,
            device=self._resolved_device,
            verbose=False,
        )
        if not results:
            return []
        r = results[0]
        boxes = r.boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().tolist()
        scores = boxes.conf.cpu().tolist()
        cls_indices = boxes.cls.cpu().tolist()
        names = r.names  # dict[int, str] mapping class id → name

        detections: list[Detection] = []
        for (x0, y0, x1, y1), score, cls_idx in zip(xyxy, scores, cls_indices):
            label = names.get(int(cls_idx), str(int(cls_idx)))
            detections.append(Detection(
                xy=[2,
                    max(0.0, x0 / W),
                    max(0.0, y0 / H),
                    max(0.0, min(1.0, (x1 - x0) / W)),
                    max(0.0, min(1.0, (y1 - y0) / H))],
                label=str(label),
                score=float(score),
            ))
        detections.sort(key=lambda d: d.score, reverse=True)
        return detections[:max_per_prompt * max(1, len(prompts))]


register_adapter("ultralytics:yolov8",
                 lambda model_id, device="auto", **kw: YoloWorldAdapter(model_id, device, **kw))
