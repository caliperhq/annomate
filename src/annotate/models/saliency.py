"""OpenCV-based saliency adapter for adaptive tiling.

Uses ``cv2.saliency.StaticSaliencySpectralResidual_create()`` —
classical (no weights to download), order-of-microseconds per megapixel,
and good enough to pick out "interesting" regions in dense images. The
output is a grayscale saliency map; the registry helper
``cluster_to_tiles`` thresholds and clusters it into tile bboxes.

Capability: ``saliency``. The whole adapter is gated behind the ``[ai]``
extra because OpenCV (``opencv-python-headless``) is in there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annotate.models.base import Adapter
from annotate.models.registry import register_adapter
from annotate.models.tiling import Tile

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class OpenCVSpectralResidualAdapter(Adapter):
    capabilities = ("saliency",)
    weights_mb_estimate = 0   # no weights — algorithm is parameter-free

    def __init__(self, model_id: str, device: str = "cpu", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self._impl = None

    def load(self) -> None:
        if self._loaded:
            return
        import cv2
        self._impl = cv2.saliency.StaticSaliencySpectralResidual_create()
        self._loaded = True

    def unload(self) -> None:
        self._impl = None
        self._loaded = False

    def saliency(self, image: "PILImage"):
        """Return a saliency map as a PIL.Image grayscale (mode 'L')."""
        if not self._loaded:
            self.load()
        import cv2
        import numpy as np
        from PIL import Image as PILImage

        rgb = np.array(image.convert("RGB"))
        bgr = rgb[:, :, ::-1]
        success, sal_map = self._impl.computeSaliency(bgr)
        if not success:
            return PILImage.new("L", image.size, color=0)
        sal_uint8 = (sal_map * 255).astype("uint8")
        return PILImage.fromarray(sal_uint8, mode="L")


def cluster_to_tiles(
    saliency_map,
    *,
    threshold: float = 0.4,
    max_tiles: int = 8,
    min_area_fraction: float = 0.005,
    pad_fraction: float = 0.15,
) -> list[Tile]:
    """Threshold a saliency map and return up to ``max_tiles`` bboxes
    around the largest connected components.

    The map can be a PIL grayscale image or any 2D array-likes ``cv2`` accepts.
    """
    import cv2
    import numpy as np

    arr = np.asarray(saliency_map)
    if arr.ndim != 2:
        raise ValueError("saliency map must be 2D")
    H, W = arr.shape
    thresh = int(threshold * 255)
    _, binary = cv2.threshold(arr, thresh, 255, cv2.THRESH_BINARY)
    # Connected components on the binary image
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    # stats: [N, 5] with [x, y, w, h, area]
    components = []
    image_area = max(1, W * H)
    for i in range(1, num_labels):    # skip background label 0
        x, y, w, h, area = stats[i]
        if area / image_area < min_area_fraction:
            continue
        components.append((area, int(x), int(y), int(w), int(h)))
    components.sort(reverse=True)     # biggest first

    tiles: list[Tile] = []
    for col, (_area, x, y, w, h) in enumerate(components[:max_tiles]):
        pad_x = int(w * pad_fraction)
        pad_y = int(h * pad_fraction)
        tx = max(0, x - pad_x)
        ty = max(0, y - pad_y)
        tw = min(W - tx, w + 2 * pad_x)
        th = min(H - ty, h + 2 * pad_y)
        tiles.append(Tile(col=col, row=0, x=tx, y=ty, w=tw, h=th))
    return tiles


register_adapter("opencv:spectral_residual",
                 lambda model_id, device="cpu", **kw: OpenCVSpectralResidualAdapter(model_id, device, **kw))
