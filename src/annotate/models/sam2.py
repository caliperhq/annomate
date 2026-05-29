"""SAM 2 adapter — promptable segmentation, used to tighten loose boxes.

Wraps the HuggingFace ``transformers`` SAM 2 implementation
(``facebook/sam2-hiera-tiny`` by default; same code handles -small,
-base-plus, -large). Lazy-imports torch + transformers in ``load()``.

Capability: ``segment``. Accepts a box prompt (in original-pixel
coordinates of the input image), returns a tight bbox derived from the
best mask, plus IoU between the input and tightened boxes and the
mask area as a fraction of the image.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annotate.models.base import Adapter, Mask
from annotate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class Sam2Adapter(Adapter):
    """Box-prompted segmenter."""

    capabilities = ("segment",)

    _SIZE_MB = {
        "facebook/sam2-hiera-tiny": 160,
        "facebook/sam2-hiera-small": 230,
        "facebook/sam2-hiera-base-plus": 360,
        "facebook/sam2-hiera-large": 900,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self.weights_mb_estimate = self._SIZE_MB.get(model_id, 200)
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
        from transformers import Sam2Model, Sam2Processor

        device = self._resolve_device(self.device)
        self._processor = Sam2Processor.from_pretrained(self.model_id)
        self._model = Sam2Model.from_pretrained(self.model_id).to(device)
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

    def segment(
        self,
        image: "PILImage",
        *,
        box: tuple[float, float, float, float] | None = None,
        point: tuple[float, float] | None = None,
        text: str | None = None,
    ) -> Mask:
        """Segment with a box (preferred) or point prompt. Coordinates are in
        the image's own pixel space. Returns a Mask whose ``xy`` is the
        tightened bbox in fraction-of-image space."""
        if not self._loaded:
            self.load()
        if text is not None:
            raise NotImplementedError("Text prompts require SAM 3 — see facebook/sam3.")
        if box is None and point is None:
            raise ValueError("Provide either a box (x1,y1,x2,y2) or a point (x,y).")

        torch = self._torch
        W, H = image.size

        if box is not None:
            x1, y1, x2, y2 = box
            inputs = self._processor(
                images=image,
                input_boxes=[[[x1, y1, x2, y2]]],
                return_tensors="pt",
            )
        else:
            px, py = point
            inputs = self._processor(
                images=image,
                input_points=[[[[px, py]]]],
                input_labels=[[[1]]],
                return_tensors="pt",
            )

        inputs = {
            k: (v.to(self._resolved_device) if hasattr(v, "to") else v)
            for k, v in inputs.items()
        }
        with torch.no_grad():
            outputs = self._model(**inputs, multimask_output=True)

        masks = self._processor.post_process_masks(
            outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu(),
        )[0]                                  # [N_objects, N_pred, H, W] for batch 0
        iou_scores = outputs.iou_scores.cpu().squeeze(0).squeeze(0)   # [N_pred]
        best = int(iou_scores.argmax())
        mask = masks[0][best]                 # [H, W] bool tensor

        ys, xs = mask.nonzero(as_tuple=True)
        if len(xs) == 0:
            # Degenerate: model returned an empty mask. Keep the input box.
            x_frac = (box[0] / W) if box else 0.0
            y_frac = (box[1] / H) if box else 0.0
            w_frac = ((box[2] - box[0]) / W) if box else 0.0
            h_frac = ((box[3] - box[1]) / H) if box else 0.0
            return Mask(
                xy=[2, x_frac, y_frac, w_frac, h_frac],
                iou_with_input=1.0 if box else None,
                area_fraction=0.0,
                raster=None,
            )

        tx1 = int(xs.min())
        ty1 = int(ys.min())
        tx2 = int(xs.max()) + 1
        ty2 = int(ys.max()) + 1
        x_frac = tx1 / W
        y_frac = ty1 / H
        w_frac = (tx2 - tx1) / W
        h_frac = (ty2 - ty1) / H

        # IoU between input box and tightened box
        iou_val = None
        if box is not None:
            ix0 = max(box[0], tx1); iy0 = max(box[1], ty1)
            ix1 = min(box[2], tx2); iy1 = min(box[3], ty2)
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                a = (box[2] - box[0]) * (box[3] - box[1])
                b = (tx2 - tx1) * (ty2 - ty1)
                union = a + b - inter
                iou_val = float(inter / union) if union > 0 else 0.0
            else:
                iou_val = 0.0

        area_fraction = float(mask.sum().item()) / float(W * H)

        return Mask(
            xy=[2, x_frac, y_frac, w_frac, h_frac],
            iou_with_input=iou_val,
            area_fraction=area_fraction,
            raster=None,
        )


register_adapter("facebook/sam2-",
                 lambda model_id, device="auto", **kw: Sam2Adapter(model_id, device, **kw))
