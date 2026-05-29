"""Image tiling for detection: grid generation + cross-tile NMS merge.

Uses ``sahi.slicing.get_slice_bboxes`` when the ``[ai]`` extra is installed
(it has well-tested boundary handling); otherwise falls back to a tiny
hand-rolled implementation so the tiling-mode-selection logic and the
NMS merge are testable without torch installed.

All coordinates returned to callers are 0–1 fractions of the original
image dims. Tile arithmetic is in original pixel space internally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Auto-grid heuristic
# ---------------------------------------------------------------------------

# (max_longest_edge_px, grid_label, tile_target_px)
# Tile target is the desired tile size; the grid is computed to cover the
# image with overlap. Auto picks the smallest grid whose tile-target is
# roughly the image's natural scale.
_AUTO_GRID_TABLE = [
    (1024,  "none", None),
    (2048,  "2x2",  1024),
    (4096,  "4x4",  1280),
    (8192,  "8x8",  1280),
    (None,  "16x16", 1280),   # everything larger
]


def auto_grid(width: int, height: int) -> str:
    """Pick a tiling mode label for an image of the given dims."""
    longest = max(width, height)
    for limit, label, _ in _AUTO_GRID_TABLE:
        if limit is None or longest <= limit:
            return label
    return "16x16"


def grid_to_tile_count(mode: str) -> int:
    """``'4x4'`` → 16, etc. Returns 1 for ``'none'``."""
    if mode == "none":
        return 1
    a, b = mode.lower().split("x", 1)
    return int(a) * int(b)


def parse_grid(mode: str) -> tuple[int, int]:
    """Return (cols, rows). ``'none'`` → (1, 1)."""
    if mode == "none":
        return (1, 1)
    a, b = mode.lower().split("x", 1)
    return (int(a), int(b))


# ---------------------------------------------------------------------------
# Tile bbox generation
# ---------------------------------------------------------------------------

@dataclass
class Tile:
    """A tile bbox in original-pixel space."""
    col: int
    row: int
    x: int
    y: int
    w: int
    h: int

    @property
    def coords(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


def generate_tiles(
    image_width: int,
    image_height: int,
    mode: str,
    *,
    overlap: float = 0.2,
) -> list[Tile]:
    """Generate tile bboxes covering the image at the requested grid.

    Mode ``'none'`` returns a single tile spanning the full image.
    """
    if mode == "none" or (mode != "auto" and grid_to_tile_count(mode) == 1):
        return [Tile(0, 0, 0, 0, image_width, image_height)]

    if mode == "auto":
        mode = auto_grid(image_width, image_height)
        if mode == "none":
            return [Tile(0, 0, 0, 0, image_width, image_height)]

    cols, rows = parse_grid(mode)
    # Tile w/h before overlap; we shrink stride by `overlap` so tiles overlap.
    stride_w = image_width / cols
    stride_h = image_height / rows
    tile_w = int(round(stride_w * (1 + overlap)))
    tile_h = int(round(stride_h * (1 + overlap)))

    tiles: list[Tile] = []
    for r in range(rows):
        for c in range(cols):
            x = int(round(c * stride_w))
            y = int(round(r * stride_h))
            w = min(tile_w, image_width - x)
            h = min(tile_h, image_height - y)
            tiles.append(Tile(c, r, x, y, w, h))
    return tiles


# ---------------------------------------------------------------------------
# NMS merge across tiles
# ---------------------------------------------------------------------------

@dataclass
class CandidateBox:
    """One detection candidate. Coords are fractions of the original image."""
    x: float
    y: float
    w: float
    h: float
    label: str
    score: float
    tile: tuple[int, int] | None = None

    def as_xyxy(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def to_xy(self) -> list[float]:
        """VIA rect encoding ``[2, x, y, w, h]`` in fraction space."""
        return [2, self.x, self.y, self.w, self.h]


def iou(a: CandidateBox, b: CandidateBox) -> float:
    ax0, ay0, ax1, ay1 = a.as_xyxy()
    bx0, by0, bx1, by1 = b.as_xyxy()
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    a_area = (ax1 - ax0) * (ay1 - ay0)
    b_area = (bx1 - bx0) * (by1 - by0)
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


def nms_merge(
    candidates: Iterable[CandidateBox],
    *,
    iou_threshold: float = 0.5,
    per_label: bool = True,
) -> list[CandidateBox]:
    """Drop duplicate detections that came back from overlapping tiles.

    Sorts by score descending and greedily keeps a candidate if its IoU
    with every already-kept candidate (of the same label, if per_label)
    is below the threshold.
    """
    sorted_cands = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[CandidateBox] = []
    for cand in sorted_cands:
        rivals = [k for k in kept if (not per_label or k.label == cand.label)]
        if any(iou(cand, k) >= iou_threshold for k in rivals):
            continue
        kept.append(cand)
    return kept


# ---------------------------------------------------------------------------
# Subtract candidates that already exist as annotations
# ---------------------------------------------------------------------------

def filter_existing(
    candidates: Iterable[CandidateBox],
    existing_boxes_fraction: Iterable[tuple[float, float, float, float]],
    *,
    iou_threshold: float = 0.5,
) -> list[CandidateBox]:
    """Drop candidates that overlap an existing annotation above threshold."""
    existing = list(existing_boxes_fraction)
    out: list[CandidateBox] = []
    for cand in candidates:
        cx0, cy0, cx1, cy1 = cand.as_xyxy()
        keep = True
        for ex, ey, ew, eh in existing:
            ex0, ey0, ex1, ey1 = ex, ey, ex + ew, ey + eh
            ix0 = max(cx0, ex0); iy0 = max(cy0, ey0)
            ix1 = min(cx1, ex1); iy1 = min(cy1, ey1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            ca = (cx1 - cx0) * (cy1 - cy0)
            ea = (ex1 - ex0) * (ey1 - ey0)
            union = ca + ea - inter
            if union > 0 and inter / union >= iou_threshold:
                keep = False
                break
        if keep:
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Default broad prompts (for find-missing mode)
# ---------------------------------------------------------------------------

DEFAULT_BROAD_PROMPTS = [
    "person", "face", "animal", "bird", "vehicle", "tool", "instrument",
    "container", "text", "sign", "vessel", "flying object", "tree",
    "building", "machine", "furniture",
]
