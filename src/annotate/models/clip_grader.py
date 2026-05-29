"""CLIP-cosine grading rubric — inspired by ClipGrader (Pham et al., 2025).

The ClipGrader paper's actual checkpoint isn't public, so we ship a simpler
rubric that uses an off-the-shelf CLIP model:

  - **label_match** — CLIP cosine similarity between the cropped region and
    the label text. High = the visible content matches the label.

  - **position** — CLIP cosine of a centre-shrunk crop vs the label. If the
    object is well-centred this stays high; if the box is shifted off the
    object, the centre crop drops (because the centre is background).

  - **size** — CLIP cosine of a centre-shrunk crop AND an expanded crop vs
    the label. If the box is too loose, the shrunk crop scores *higher*.
    If the box is too tight (missing extent), the expanded crop scores
    higher.

  - **shape_encoding_fit** — pure aspect-ratio heuristic (no model call).
    Very long rectangles (aspect > 4:1) probably want a polyline; near-
    circular content in a square box probably wants a circle.

This is weaker than a purpose-built grader but uses only CLIP, runs in
under a second per region on CPU, and surfaces the same *categories* of
error that the training-session corpus revealed. If a real grading
checkpoint becomes available, register it under ``[grade.default]`` to
override this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from annotate.models.base import Adapter, Grade
from annotate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class ClipGraderAdapter(Adapter):
    """Multi-purpose CLIP adapter: grading rubric + zero-shot classification."""

    capabilities = ("grade", "classify")
    weights_mb_estimate = 350

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
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
        from transformers import CLIPModel, CLIPProcessor

        device = self._resolve_device(self.device)
        self._processor = CLIPProcessor.from_pretrained(self.model_id)
        self._model = CLIPModel.from_pretrained(self.model_id).to(device)
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

    # ----- public capability method -----

    def grade(
        self,
        image: "PILImage",
        region: dict,
        label: str,
    ) -> Grade:
        """Score a single region. ``region`` is the VIA metadata entry
        (must include ``xy`` and ``av``). The image is the full original."""
        if not self._loaded:
            self.load()
        W, H = image.size
        xy = region.get("xy") or []
        if not xy:
            return Grade(
                mid=region.get("_mid", ""),
                label=label,
                position=0.0,
                size=0.0,
                label_match=0.0,
                shape_encoding_fit="wrong",
                issues=["region has no xy"],
            )

        # Derive an axis-aligned bbox in original pixel space for all shapes.
        bbox = _shape_bbox(xy, W, H)
        if bbox is None:
            return Grade(
                mid=region.get("_mid", ""),
                label=label,
                position=0.5,
                size=0.5,
                label_match=0.5,
                shape_encoding_fit="marginal",
                issues=["unknown shape encoding"],
            )
        x0, y0, x1, y1 = bbox

        # Original crop, centre-shrunk crop, and expanded crop.
        crops = {
            "as_is": image.crop((x0, y0, x1, y1)),
            "shrunk": _centre_crop(image, bbox, scale=0.6),
            "expanded": _expanded_crop(image, bbox, scale=1.4),
        }
        scores = self._batch_similarity(list(crops.values()), label)
        as_is_score = scores[0]
        shrunk_score = scores[1]
        expanded_score = scores[2]

        # Heuristic mappings of cosine deltas → 0-1 quality scores.
        issues: list[str] = []
        position = float(_quality_from_delta(as_is_score, shrunk_score))
        if shrunk_score > as_is_score + 0.03:
            issues.append("centre-shrunk crop scores higher than current box — "
                          "feature may be offset or box may be too loose")
        if shrunk_score < as_is_score - 0.03:
            issues.append("centre-shrunk crop scores worse — box is reasonably centred")

        if expanded_score > as_is_score + 0.03:
            issues.append("expanding the box raises the match — extent may be too small")
        size = float(_quality_from_delta(as_is_score, max(shrunk_score, expanded_score)))

        label_match = float(_normalize_clip_cosine(as_is_score))
        if label_match < 0.5:
            issues.append(f"low label-content match ({label_match:.2f})")

        shape_fit, shape_note = _shape_encoding_fit(xy, bbox)
        if shape_note:
            issues.append(shape_note)

        return Grade(
            mid=region.get("_mid", ""),
            label=label,
            position=position,
            size=size,
            label_match=label_match,
            shape_encoding_fit=shape_fit,
            issues=issues,
        )

    def classify(self, image: "PILImage", candidate_labels: list[str]) -> dict[str, float]:
        """Zero-shot scene classification. Returns label → softmax probability."""
        if not self._loaded:
            self.load()
        if not candidate_labels:
            return {}
        torch = self._torch
        inputs = self._processor(
            text=candidate_labels, images=image, return_tensors="pt", padding=True,
        )
        inputs = {k: (v.to(self._resolved_device) if hasattr(v, "to") else v)
                  for k, v in inputs.items()}
        with torch.no_grad():
            out = self._model(**inputs)
        # logits_per_image: [1, N_labels]
        probs = out.logits_per_image.softmax(dim=-1).cpu().squeeze(0).tolist()
        return {label: float(p) for label, p in zip(candidate_labels, probs)}

    # ----- internals -----

    def _batch_similarity(self, images: list["PILImage"], text: str) -> list[float]:
        """Cosine similarity between each image and a single text. CLIP-norm."""
        torch = self._torch
        inputs = self._processor(text=[text], images=images, return_tensors="pt", padding=True)
        inputs = {k: (v.to(self._resolved_device) if hasattr(v, "to") else v)
                  for k, v in inputs.items()}
        with torch.no_grad():
            out = self._model(**inputs)
        # text_embeds: [1, D], image_embeds: [N, D]
        text_emb = out.text_embeds / out.text_embeds.norm(dim=-1, keepdim=True)
        img_emb = out.image_embeds / out.image_embeds.norm(dim=-1, keepdim=True)
        cos = (img_emb @ text_emb.T).squeeze(-1)
        return [float(v) for v in cos.cpu().tolist()]


# ---------------------------------------------------------------------------
# Helpers (testable without torch)
# ---------------------------------------------------------------------------

def _shape_bbox(xy: list, W: int, H: int) -> tuple[int, int, int, int] | None:
    """Return (x0, y0, x1, y1) axis-aligned bbox in original-pixel space.

    Coords in xy are already in original pixel space (the project stores
    them that way after server-side fraction → pixel conversion).
    """
    if not xy:
        return None
    head = xy[0]
    coords = xy[1:]
    try:
        if head == 1 and len(coords) >= 2:
            return (int(coords[0]), int(coords[1]), int(coords[0]) + 1, int(coords[1]) + 1)
        if head == 2 and len(coords) >= 4:
            x, y, w, h = coords[0], coords[1], coords[2], coords[3]
            return (int(x), int(y), int(x + w), int(y + h))
        if head == 3 and len(coords) >= 3:
            cx, cy, r = coords[0], coords[1], coords[2]
            return (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
        if head == 4 and len(coords) >= 4:
            cx, cy, rx, ry = coords[0], coords[1], coords[2], coords[3]
            return (int(cx - rx), int(cy - ry), int(cx + rx), int(cy + ry))
        if head in (6, 7) and len(coords) >= 4:
            xs = [coords[i] for i in range(0, len(coords) - 1, 2)]
            ys = [coords[i + 1] for i in range(0, len(coords) - 1, 2)]
            return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
    except (TypeError, IndexError, ValueError):
        return None
    return None


def _centre_crop(image, bbox: tuple[int, int, int, int], scale: float):
    """Return a crop scaled around the bbox centre by ``scale`` (e.g. 0.6 = 60% of original)."""
    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    nw = (x1 - x0) * scale
    nh = (y1 - y0) * scale
    return image.crop((
        max(0, int(cx - nw / 2)),
        max(0, int(cy - nh / 2)),
        min(image.size[0], int(cx + nw / 2)),
        min(image.size[1], int(cy + nh / 2)),
    ))


def _expanded_crop(image, bbox: tuple[int, int, int, int], scale: float):
    """Crop expanded around the bbox centre by ``scale`` (e.g. 1.4 = 140%)."""
    return _centre_crop(image, bbox, scale)


def _normalize_clip_cosine(cos: float) -> float:
    """Map a raw CLIP cosine (typically 0.1–0.35 for matches) onto [0, 1]
    where ~0.30 is a strong match and ~0.10 is poor."""
    return max(0.0, min(1.0, (cos - 0.10) / 0.20))


def _quality_from_delta(as_is: float, alternative_best: float) -> float:
    """If an alternative crop scores noticeably better, the current box is
    suboptimal. Map the delta onto a [0, 1] quality where 1 = no better
    alternative and 0 = alternative is 0.10+ better."""
    delta = max(0.0, alternative_best - as_is)
    return max(0.0, min(1.0, 1.0 - delta / 0.10))


def _shape_encoding_fit(xy: list, bbox: tuple[int, int, int, int]) -> tuple[Literal["good", "marginal", "wrong"], str | None]:
    """Pure heuristic — no model call. Catches obvious encoding mismatches."""
    head = xy[0]
    x0, y0, x1, y1 = bbox
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    aspect = max(w, h) / min(w, h)

    if head == 2:  # rectangle
        if aspect > 5:
            return ("marginal", "very long rectangle — polyline may read better than a bounding rect")
        return ("good", None)
    if head == 6:  # polyline
        if aspect < 2:
            return ("marginal", "polyline with near-square bbox — a polygon footprint may be more readable")
        return ("good", None)
    if head == 7:  # polygon
        # Polygons are flexible; assume good unless the bbox is degenerate.
        if min(w, h) < 4:
            return ("marginal", "polygon bbox is very thin — verify the points trace the feature")
        return ("good", None)
    if head in (3, 4):  # circle / ellipse
        if head == 3 and abs(w - h) > min(w, h) * 0.2:
            return ("marginal", "circle in a non-square bbox — consider ellipse")
        return ("good", None)
    return ("good", None)


register_adapter("openai/clip-vit-",
                 lambda model_id, device="auto", **kw: ClipGraderAdapter(model_id, device, **kw))
