"""YOLOE adapter — open-vocab YOLO with text, visual, and prompt-free modes.

Wraps Ultralytics' YOLOE distribution (THU-MIG, ICCV 2025).

LICENSE NOTICE
==============
YOLOE upstream is **AGPL-3.0**. Bundling it inside a service that other
people connect to over a network — including hosting an MCP server for
remote users — may obligate you to share your server's source code under
AGPL terms.

For local single-user MCP use on your own machine, AGPL imposes no
practical restriction. But anyone deploying ``annomate`` as a hosted
service should think carefully before enabling the ``[yolo]`` extra
*and* configuring a YOLOE pipeline.

The adapter logs a one-line reminder of the license at first load.

CAPABILITIES
============
- ``detect``         — text-prompt detection (same shape as YOLO-World)
- ``segment``        — instance segmentation (native, no separate SAM pass)
- ``find_similar``   — visual-prompt one-shot: "find more things like this
                       reference region"

Pipeline IDs accepted: ``ultralytics:yoloe-{11,26}{n,s,m,l,x}-seg``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from annomate.models.base import Adapter, Detection, Mask
from annomate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


_LICENSE_WARNED = False


def _warn_license_once() -> None:
    global _LICENSE_WARNED
    if _LICENSE_WARNED:
        return
    _LICENSE_WARNED = True
    logging.getLogger("annomate").info(
        "YOLOE is AGPL-3.0. Local single-user use is fine; hosted/network "
        "deployments should review the AGPL terms."
    )


class YoloeAdapter(Adapter):
    capabilities = ("detect", "segment", "find_similar")

    _SIZE_MB = {
        "yoloe-11s-seg": 130, "yoloe-11m-seg": 290, "yoloe-11l-seg": 430,
        "yoloe-26n-seg": 50, "yoloe-26s-seg": 140, "yoloe-26m-seg": 310,
        "yoloe-26l-seg": 460, "yoloe-26x-seg": 700,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self._weights = model_id.split(":", 1)[1] if ":" in model_id else model_id
        self._weights_file = self._weights if self._weights.endswith(".pt") else self._weights + ".pt"
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
        _warn_license_once()
        from ultralytics import YOLOE
        self._resolved_device = self._resolve_device(self.device)
        self._model = YOLOE(self._weights_file)
        self._loaded = True

    def unload(self) -> None:
        if not self._loaded:
            return
        del self._model
        self._model = None
        self._current_classes = None
        self._loaded = False

    # ----- detect (text prompts) -----

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
        if self._current_classes != prompts:
            self._model.set_classes(prompts, self._model.get_text_pe(prompts))
            self._current_classes = list(prompts)

        W, H = image.size
        results = self._model.predict(
            source=image, conf=min_confidence, device=self._resolved_device, verbose=False,
        )
        return _results_to_detections(results, W, H, prompts, max_per_prompt)

    # ----- segment (uses detect path and pulls masks) -----

    def segment(
        self,
        image: "PILImage",
        *,
        box: tuple[float, float, float, float] | None = None,
        point: tuple[float, float] | None = None,
        text: str | None = None,
    ) -> Mask:
        """YOLOE's mask head runs alongside detection. For a single-region
        segment call we look for the highest-scoring detection inside (or
        near) the provided box prompt and return its mask-derived bbox."""
        if not self._loaded:
            self.load()
        if text is None and box is None and point is None:
            raise ValueError("YOLOE segment needs a box, point, or text prompt.")

        prompts = [text] if text else ["object"]
        if self._current_classes != prompts:
            self._model.set_classes(prompts, self._model.get_text_pe(prompts))
            self._current_classes = list(prompts)

        W, H = image.size
        # Crop a tight context window around the box prompt so segmentation
        # focuses on the right area. If no box given, run on the whole image.
        if box is not None:
            x0, y0, x1, y1 = box
            pad_x = (x1 - x0) * 0.15
            pad_y = (y1 - y0) * 0.15
            cx0 = max(0, int(x0 - pad_x))
            cy0 = max(0, int(y0 - pad_y))
            cx1 = min(W, int(x1 + pad_x))
            cy1 = min(H, int(y1 + pad_y))
            ref_image = image.crop((cx0, cy0, cx1, cy1))
            offset_x, offset_y = cx0, cy0
        else:
            ref_image = image
            offset_x = offset_y = 0

        results = self._model.predict(source=ref_image, device=self._resolved_device, verbose=False)
        if not results or results[0].masks is None or len(results[0].masks) == 0:
            # Empty result — fall back to whatever bbox the caller provided
            if box is not None:
                return Mask(
                    xy=[2, box[0] / W, box[1] / H, (box[2] - box[0]) / W, (box[3] - box[1]) / H],
                    iou_with_input=1.0,
                    area_fraction=0.0,
                )
            return Mask(xy=[2, 0.0, 0.0, 0.0, 0.0], area_fraction=0.0)

        r = results[0]
        masks_xy = r.masks.xy        # list of contour polygons
        confs = r.boxes.conf.cpu().tolist()
        best_idx = max(range(len(confs)), key=lambda i: confs[i])
        contour = masks_xy[best_idx]    # [(x, y), ...]

        import numpy as np
        pts = np.asarray(contour, dtype=float)
        if pts.size == 0:
            return Mask(xy=[2, 0.0, 0.0, 0.0, 0.0], area_fraction=0.0)
        x0_m = pts[:, 0].min() + offset_x
        y0_m = pts[:, 1].min() + offset_y
        x1_m = pts[:, 0].max() + offset_x
        y1_m = pts[:, 1].max() + offset_y
        area_px = float(0.5 * abs(np.dot(pts[:, 0], np.roll(pts[:, 1], 1))
                                  - np.dot(pts[:, 1], np.roll(pts[:, 0], 1))))
        iou_val = None
        if box is not None:
            ix0 = max(box[0], x0_m); iy0 = max(box[1], y0_m)
            ix1 = min(box[2], x1_m); iy1 = min(box[3], y1_m)
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                a = (box[2] - box[0]) * (box[3] - box[1])
                b = (x1_m - x0_m) * (y1_m - y0_m)
                u = a + b - inter
                iou_val = float(inter / u) if u > 0 else 0.0
            else:
                iou_val = 0.0
        return Mask(
            xy=[2, x0_m / W, y0_m / H, (x1_m - x0_m) / W, (y1_m - y0_m) / H],
            iou_with_input=iou_val,
            area_fraction=area_px / float(W * H),
        )

    # ----- find_similar (visual prompts) -----

    def find_similar(
        self,
        target_image: "PILImage",
        reference_image: "PILImage",
        reference_box_pixel: tuple[float, float, float, float],
        *,
        min_confidence: float = 0.3,
    ) -> list[Detection]:
        """Visual-prompt mode: find regions in ``target_image`` that look
        similar to the area inside ``reference_box_pixel`` on
        ``reference_image``. Returns Detection objects in fraction-of-
        target-image space."""
        if not self._loaded:
            self.load()
        import numpy as np

        W, H = target_image.size
        rx0, ry0, rx1, ry1 = reference_box_pixel
        visual_prompts = {
            "bboxes": np.array([[rx0, ry0, rx1, ry1]]),
            "cls": np.array([0]),
        }
        from ultralytics.models.yolo.yoloe import YOLOEVPSegPredictor
        results = self._model.predict(
            source=target_image,
            refer_image=reference_image,
            visual_prompts=visual_prompts,
            predictor=YOLOEVPSegPredictor,
            conf=min_confidence,
            device=self._resolved_device,
            verbose=False,
        )
        if not results:
            return []
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return []
        xyxy = r.boxes.xyxy.cpu().tolist()
        scores = r.boxes.conf.cpu().tolist()
        out: list[Detection] = []
        for (x0, y0, x1, y1), score in zip(xyxy, scores):
            out.append(Detection(
                xy=[2,
                    max(0.0, x0 / W),
                    max(0.0, y0 / H),
                    max(0.0, min(1.0, (x1 - x0) / W)),
                    max(0.0, min(1.0, (y1 - y0) / H))],
                label="similar",
                score=float(score),
            ))
        out.sort(key=lambda d: d.score, reverse=True)
        return out


def _results_to_detections(results, W: int, H: int, prompts: list[str], max_per_prompt: int) -> list[Detection]:
    if not results:
        return []
    r = results[0]
    boxes = r.boxes
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().tolist()
    scores = boxes.conf.cpu().tolist()
    cls_indices = boxes.cls.cpu().tolist()
    names = r.names
    out: list[Detection] = []
    for (x0, y0, x1, y1), score, cls_idx in zip(xyxy, scores, cls_indices):
        out.append(Detection(
            xy=[2,
                max(0.0, x0 / W),
                max(0.0, y0 / H),
                max(0.0, min(1.0, (x1 - x0) / W)),
                max(0.0, min(1.0, (y1 - y0) / H))],
            label=str(names.get(int(cls_idx), str(int(cls_idx)))),
            score=float(score),
        ))
    out.sort(key=lambda d: d.score, reverse=True)
    return out[:max_per_prompt * max(1, len(prompts))]


register_adapter("ultralytics:yoloe",
                 lambda model_id, device="auto", **kw: YoloeAdapter(model_id, device, **kw))
