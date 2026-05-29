"""Adapter base class and structured result types for the model layer.

Adapters wrap a single HF model (or local checkpoint) and expose one or
more *capabilities*. Capabilities are duck-typed methods on the adapter
class itself — there is no separate protocol per capability, just a
declared set of `capabilities` strings and the corresponding methods.

Capability → required method signature:
  - "detect":   detect(image, prompts, **kwargs) -> list[Detection]
  - "segment":  segment(image, box=None, point=None, text=None) -> Mask
  - "verify":   verify(image_crop, label) -> Verdict
  - "grade":    grade(image, region, label) -> Grade
  - "classify": classify(image, candidate_labels) -> dict[str, float]
  - "saliency": saliency(image) -> "PIL.Image.Image"  (grayscale map)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


Capability = Literal[
    "detect", "segment", "verify", "grade", "classify", "saliency",
    "ask",             # free-form Q&A (chat VLM)
    "find_similar",    # one-shot visual-prompt detection (YOLOE)
]


class NotInstalledError(RuntimeError):
    """Raised when an adapter needs the [ai] extra but it isn't installed."""


@dataclass
class Detection:
    """One detection from an open-vocab detector. Coords are 0–1 fractions."""
    xy: list[float]          # [shape_id, x, y, w, h] or other shape encoding
    label: str
    score: float
    tile: tuple[int, int] | None = None   # (row, col) when from a tiled pass


@dataclass
class Mask:
    """A segmentation result. Bbox is derived; mask is optional raster output."""
    xy: list[float]          # bbox in fraction space [2, x, y, w, h]
    iou_with_input: float | None = None
    area_fraction: float | None = None   # mask area / image area
    raster: "PILImage | None" = None     # binary mask if caller wants it


@dataclass
class Verdict:
    """VLM verification result for a single labelled region."""
    label_claimed: str
    verdict: Literal["yes", "no", "unsure"]
    confidence: float
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    suggested_label: str | None = None


@dataclass
class Grade:
    """Per-region quality scores. All scores in [0, 1]; higher is better."""
    mid: str
    label: str
    position: float
    size: float
    label_match: float
    shape_encoding_fit: Literal["good", "marginal", "wrong"]
    issues: list[str] = field(default_factory=list)


@dataclass
class Answer:
    """Free-form VLM answer to a question about an image (or region of one)."""
    question: str
    text: str
    finish_reason: str = "stop"
    tokens_generated: int | None = None


class Adapter(ABC):
    """Subclass per model family. Adapters are *not* loaded at construction;
    `load()` is called lazily by the registry on first use."""

    name: str = ""
    capabilities: tuple[Capability, ...] = ()
    weights_mb_estimate: int = 0   # for memory budgeting; rough is fine

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        self.model_id = model_id
        self.device = device
        self.extra = kwargs
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @abstractmethod
    def load(self) -> None:
        """Bring weights into memory. Idempotent — calling twice is a no-op."""

    @abstractmethod
    def unload(self) -> None:
        """Release weights. Adapter can be reloaded later via load()."""

    def memory_mb(self) -> int:
        """Best-effort current footprint. Default falls back to the estimate."""
        return self.weights_mb_estimate if self._loaded else 0
