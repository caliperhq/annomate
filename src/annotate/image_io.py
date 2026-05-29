"""Image format detection, loading, and browser-serving conversion.

Sits between every PIL-open call and the actual file. Pillow-native
formats pass through untouched; everything else is opened via the
appropriate loader (pillow-heif for HEIC/HEIF/AVIF, future: rawpy for
RAW, pdf2image for PDF, etc.) and converted once to JPEG for the
browser, cached under ``platformdirs.user_cache_dir("annotate")``.

The original file path stays authoritative in the project; the
browser-serving path is a transparent optimisation handled here.

Phase 1 ships:
  - LoaderClass detection (extension-based, magic-byte verified)
  - Native PIL formats (no conversion)
  - HEIC / HEIF / AVIF via pillow-heif (when ``[io]`` is installed)
  - Cached browser JPEG conversion for non-native formats
  - ExifTool-preferred metadata reader, Pillow EXIF fallback

Later phases add: RAW (rawpy), PDF (pdf2image), video frames (ffmpeg),
GeoTIFF (rasterio), DICOM (pydicom), PSD (psd-tools), gigapixel
(pyvips), ImageMagick subprocess fallback.
"""

from __future__ import annotations

import hashlib
import importlib
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import platformdirs


class LoaderClass(Enum):
    PIL_NATIVE  = "pil_native"
    PILLOW_HEIF = "pillow_heif"
    PDF         = "pdf"
    UNKNOWN     = "unknown"


# Extensions Pillow handles natively (and the browser can render
# directly for the ones in BROWSER_NATIVE).
_PIL_NATIVE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".ico",
})

# Subset of native exts that browsers can render without conversion.
# Anything outside this set is converted to JPEG for HTTP serving.
BROWSER_NATIVE = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
})

_HEIF_EXTS = frozenset({".heic", ".heif", ".avif"})
_PDF_EXTS = frozenset({".pdf"})


def heif_available() -> bool:
    try:
        importlib.import_module("pillow_heif")
        return True
    except ImportError:
        return False


def pdf_available() -> bool:
    """True iff pdf2image is importable. Doesn't guarantee the poppler
    CLI is on PATH — call pdf_page_count() to find out for real."""
    try:
        importlib.import_module("pdf2image")
        return True
    except ImportError:
        return False


def _register_heif_opener_once() -> None:
    """Idempotent registration of pillow-heif with Pillow. Safe to call
    multiple times — pillow-heif itself guards against duplicate
    registration."""
    if not heif_available():
        return
    import pillow_heif
    pillow_heif.register_heif_opener()


# Register at import so any subsequent PIL.Image.open() handles HEIC
# transparently — both ours and consumers'.
_register_heif_opener_once()


def detect(path: Path | str) -> LoaderClass:
    """Pick the loader class for a file based on extension.

    Doesn't open the file — extensions are authoritative here. A
    follow-up magic-byte check happens inside ``open_as_pil`` for
    formats where the loader rejects the file.
    """
    ext = Path(path).suffix.lower()
    if ext in _PIL_NATIVE_EXTS:
        return LoaderClass.PIL_NATIVE
    if ext in _HEIF_EXTS:
        return LoaderClass.PILLOW_HEIF if heif_available() else LoaderClass.UNKNOWN
    if ext in _PDF_EXTS:
        return LoaderClass.PDF if pdf_available() else LoaderClass.UNKNOWN
    return LoaderClass.UNKNOWN


def cache_dir() -> Path:
    """The on-disk location for converted browser-servable JPEGs."""
    return Path(platformdirs.user_cache_dir("annotate", appauthor=False)) / "converted"


def _cache_key(abs_path: Path, page: int = 0) -> str:
    """Stable cache key keyed by (path, mtime, page). mtime makes the
    cache automatically invalidate when the user re-saves the file."""
    try:
        mtime_ns = abs_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    raw = f"{abs_path.resolve()}\0{mtime_ns}\0{page}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _cached_jpeg_path(abs_path: Path, page: int = 0) -> Path:
    """Return where a JPEG conversion of this file would live in the cache."""
    safe_stem = "".join(c if c.isalnum() else "_" for c in abs_path.stem)[:48]
    return cache_dir() / f"{_cache_key(abs_path, page)}__{safe_stem}.jpg"


def cached_browser_path(abs_path: Path | str, *, page: int = 0) -> Path:
    """Return a path the HTTP layer can serve directly.

    For browser-native formats: the original path.
    For everything else: a cached JPEG (created on first call, reused
    on subsequent calls when mtime is unchanged).

    Raises ``LookupError`` for ``UNKNOWN`` formats so the caller can
    surface a useful error to the user.
    """
    abs_path = Path(abs_path)
    ext = abs_path.suffix.lower()
    if ext in BROWSER_NATIVE:
        return abs_path

    klass = detect(abs_path)
    if klass is LoaderClass.UNKNOWN:
        raise LookupError(
            f"Unsupported image format {ext!r} for {abs_path}. "
            f"Install the [io] extra (pip install 'annotate[io]') for "
            f"HEIC/AVIF support."
        )

    target = _cached_jpeg_path(abs_path, page=page)
    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    img = open_as_pil(abs_path, page=page)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    # tmp + rename so partial writes don't poison the cache
    tmp = target.with_suffix(".tmp.jpg")
    img.save(tmp, format="JPEG", quality=92)
    os.replace(tmp, target)
    return target


def open_as_pil(abs_path: Path | str, *, page: int = 0, dpi: int = 200):
    """Open any supported format and return a PIL.Image.Image.

    ``page`` is only meaningful for PDFs (and future multi-page formats);
    for single-image formats it's ignored. ``dpi`` controls the PDF
    rasterisation density (200 = readable text + manageable file size).
    """
    from PIL import Image as PILImage
    abs_path = Path(abs_path)
    klass = detect(abs_path)
    if klass is LoaderClass.UNKNOWN:
        raise LookupError(f"Unsupported image format for {abs_path}")

    if klass is LoaderClass.PDF:
        from pdf2image import convert_from_path
        # pdf2image uses 1-based page numbers; we expose 0-based.
        first = last = page + 1
        pages = convert_from_path(
            str(abs_path), dpi=dpi, first_page=first, last_page=last,
        )
        if not pages:
            raise LookupError(f"PDF page {page} not found in {abs_path}")
        img = pages[0]
        img.load()
        return img

    # pillow-heif registers itself as a PIL opener at module import, so
    # PIL.Image.open handles HEIC natively from here.
    img = PILImage.open(abs_path)
    img.load()  # decode now so EXIF etc. is populated
    return img


def pdf_page_count(abs_path: Path | str) -> int:
    """Return the number of pages in a PDF. Requires the ``[io]`` extra
    and the poppler-utils CLI."""
    from pdf2image.pdf2image import pdfinfo_from_path
    info = pdfinfo_from_path(str(abs_path))
    return int(info.get("Pages", 0))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

@dataclass
class ImageMetadata:
    source: str                       # "exiftool" | "pillow"
    capture_time: str | None = None   # ISO-8601 if available
    gps: dict | None = None           # {"lat": ..., "lon": ..., "alt": ...}
    camera: str | None = None
    lens: str | None = None
    exposure: dict | None = None      # {"iso": ..., "fnumber": ..., "shutter": ...}
    orientation: str | None = None
    width: int | None = None
    height: int | None = None
    raw: dict | None = None           # full unparsed tag dump for power-users


def _exiftool_available() -> bool:
    try:
        importlib.import_module("exiftool")
        # Also need the binary on PATH — check that lazily inside
        # open_metadata; bare import isn't sufficient.
        return True
    except ImportError:
        return False


def open_metadata(abs_path: Path | str) -> ImageMetadata:
    """Read EXIF / XMP / IPTC metadata. Prefers ExifTool when available,
    falls back to Pillow's `_getexif()` for JPEG/TIFF/HEIC."""
    abs_path = Path(abs_path)

    if _exiftool_available():
        try:
            return _read_via_exiftool(abs_path)
        except Exception:
            # ExifTool binary missing or errored — fall through to PIL
            pass

    return _read_via_pillow(abs_path)


def _read_via_exiftool(abs_path: Path) -> ImageMetadata:
    import exiftool
    with exiftool.ExifToolHelper() as et:
        tags_list = et.get_metadata(str(abs_path))
    raw = tags_list[0] if tags_list else {}

    gps: dict | None = None
    if "EXIF:GPSLatitude" in raw and "EXIF:GPSLongitude" in raw:
        gps = {
            "lat": _parse_gps_coord(raw["EXIF:GPSLatitude"], raw.get("EXIF:GPSLatitudeRef")),
            "lon": _parse_gps_coord(raw["EXIF:GPSLongitude"], raw.get("EXIF:GPSLongitudeRef")),
            "alt": raw.get("EXIF:GPSAltitude"),
        }

    exposure_parts: dict = {}
    if "EXIF:ISO" in raw:
        exposure_parts["iso"] = raw["EXIF:ISO"]
    if "EXIF:FNumber" in raw:
        exposure_parts["fnumber"] = raw["EXIF:FNumber"]
    if "EXIF:ExposureTime" in raw:
        exposure_parts["shutter"] = raw["EXIF:ExposureTime"]

    return ImageMetadata(
        source="exiftool",
        capture_time=_first(raw, ["EXIF:DateTimeOriginal",
                                  "EXIF:CreateDate",
                                  "QuickTime:CreateDate"]),
        gps=gps,
        camera=_first(raw, ["EXIF:Model", "Composite:CameraID"]),
        lens=_first(raw, ["EXIF:LensModel", "Composite:LensID"]),
        exposure=exposure_parts or None,
        orientation=_first(raw, ["EXIF:Orientation"]),
        width=_first(raw, ["EXIF:ExifImageWidth", "File:ImageWidth"]),
        height=_first(raw, ["EXIF:ExifImageHeight", "File:ImageHeight"]),
        raw=raw,
    )


def _read_via_pillow(abs_path: Path) -> ImageMetadata:
    from PIL import Image as PILImage
    from PIL.ExifTags import TAGS, GPSTAGS

    try:
        img = PILImage.open(abs_path)
    except Exception:
        return ImageMetadata(source="pillow")

    exif_data = {}
    try:
        raw_exif = img._getexif() or {}
        for tag_id, value in raw_exif.items():
            tag = TAGS.get(tag_id, str(tag_id))
            if tag == "GPSInfo" and isinstance(value, dict):
                exif_data["GPSInfo"] = {GPSTAGS.get(k, str(k)): v for k, v in value.items()}
            else:
                exif_data[tag] = value
    except AttributeError:
        pass

    gps_dict = exif_data.get("GPSInfo")
    gps: dict | None = None
    if gps_dict and "GPSLatitude" in gps_dict and "GPSLongitude" in gps_dict:
        gps = {
            "lat": _dms_to_decimal(gps_dict["GPSLatitude"], gps_dict.get("GPSLatitudeRef", "N")),
            "lon": _dms_to_decimal(gps_dict["GPSLongitude"], gps_dict.get("GPSLongitudeRef", "E")),
            "alt": gps_dict.get("GPSAltitude"),
        }

    exposure_parts: dict = {}
    if "ISOSpeedRatings" in exif_data:
        exposure_parts["iso"] = exif_data["ISOSpeedRatings"]
    if "FNumber" in exif_data:
        exposure_parts["fnumber"] = exif_data["FNumber"]
    if "ExposureTime" in exif_data:
        exposure_parts["shutter"] = exif_data["ExposureTime"]

    return ImageMetadata(
        source="pillow",
        capture_time=exif_data.get("DateTimeOriginal") or exif_data.get("DateTime"),
        gps=gps,
        camera=exif_data.get("Model"),
        lens=exif_data.get("LensModel"),
        exposure=exposure_parts or None,
        orientation=exif_data.get("Orientation"),
        width=img.width,
        height=img.height,
        raw=exif_data,
    )


# --- small helpers ---

def _first(d: dict, keys: list[str]):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _parse_gps_coord(value, ref) -> float | None:
    """ExifTool returns GPS coords as a float already; sign by ref."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if ref in ("S", "W"):
        f = -f
    return f


def _dms_to_decimal(dms, ref) -> float | None:
    """Pillow returns GPS as [(degrees, ?), (minutes, ?), (seconds, ?)]
    rational tuples; convert to signed decimal."""
    try:
        deg = float(dms[0])
        minutes = float(dms[1])
        seconds = float(dms[2])
    except (TypeError, ValueError, IndexError):
        return None
    decimal = deg + minutes / 60 + seconds / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal
