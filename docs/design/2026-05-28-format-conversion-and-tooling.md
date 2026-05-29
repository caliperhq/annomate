# Format conversion + local-tooling layer

**Status:** Design proposal — not yet approved for implementation.
**Branch (when started):** `feat/io-layer` (sibling to `feat/local-model-assistance`).
**Date:** 2026-05-28.

---

## Goal

Add an optional **IO layer** that lets the `annotate` server accept the
formats real users actually have on disk — HEIC from iPhones, RAW from
cameras, PDF pages, video frames, GeoTIFF from drones, gigapixel TIFF —
plus a small set of local-tool MCP wrappers (OCR, metadata, perceptual
hash) that complement the AI assistance layer without needing GPUs.

The IO layer is orthogonal to the AI layer: it could ship without the
AI work, the AI work could (and does) ship without it, and a user can
install either, both, or neither.

## Non-goals

- Become a general image-processing toolkit. We're only adding what
  unblocks annotation workflows.
- Ship a full DAM. No library management, no tagging, no album
  semantics — the project is the unit of work.
- Replace the user's existing pipeline. If they already convert RAW in
  Lightroom, fine — we just accept what they hand us.

---

## What's broken today

`via_add_file` has a hard-coded extension allowlist:
``{".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}``.
Anything outside that — every iPhone photo, every drone-camera RAW, every
PDF, every video — bounces with `"Unsupported image format"`. The user
has to convert in another tool, lose metadata, and re-load.

The five most-asked-for formats based on what's commonly on a user's
disk (no formal survey — extrapolating from the
training-session corpus and common annotation use cases):

1. HEIC / HEIF (iOS default since 2017)
2. PDF (scanned documents, papers)
3. Video frames (sports, security, action recognition)
4. RAW (CR2, NEF, ARW, DNG — photographer workflow)
5. Gigapixel TIFF (art, pathology, satellite mosaics)

---

## New optional extras

Two new extras, distinct from the existing `[ai]`:

```toml
[project.optional-dependencies]
io  = [
    "pillow-heif>=1.3",       # HEIC / AVIF
    "rawpy>=0.24",            # camera RAW
    "raw-pillow-opener>=0.4", # rawpy → Pillow plugin glue
    "pdf2image>=1.17",        # PDF → PIL (system: poppler-utils)
    "pyvips>=2.2",            # gigapixel TIFF (system: libvips)
    "ffmpeg-python>=0.2",     # video frame extraction (system: ffmpeg)
    "rasterio>=1.4",          # GeoTIFF (system: GDAL)
    "pydicom>=3.0",           # medical (DICOM)
    "psd-tools>=1.10",        # Photoshop PSD
    "imagehash>=4.3",         # perceptual hash dedup
    "PyExifTool>=0.5",        # metadata (system: exiftool)
]

ocr = [
    "pytesseract>=0.3",       # system: tesseract-ocr
]
```

System packages (the user installs these with `apt` / `brew` /
`emerge` / etc.):

- `poppler-utils` — needed by `pdf2image`
- `libvips` — needed by `pyvips`
- `ffmpeg` — needed by `ffmpeg-python` and for system fallback
- `gdal` — needed by `rasterio`
- `tesseract-ocr` — needed by `pytesseract`
- `exiftool` — preferred over Pillow's EXIF for full IPTC/XMP
- `imagemagick` (optional) — used as a universal fallback (`magick convert anything → png`)

We do **not** auto-install system packages. The IO loader checks at
import time and surfaces a useful hint per format.

---

## New module: `annotate.image_io`

A thin format-detection + loading layer that sits between every PIL
open call and the actual file. Returns a PIL Image regardless of the
input format. Caches converted output so we don't redo HEIC → JPEG on
every `via_get_image`.

```python
# src/annotate/image_io.py

from PIL import Image as PILImage
from pathlib import Path
from enum import Enum

class LoaderClass(Enum):
    PIL_NATIVE  = "pil_native"       # jpg, png, gif, bmp, webp, tiff
    PILLOW_HEIF = "pillow_heif"      # heic, heif, avif
    RAWPY       = "rawpy"            # raw camera formats
    PDF         = "pdf2image"        # single-page or pick page
    PYVIPS      = "pyvips"           # huge images
    RASTERIO    = "rasterio"         # geotiff
    DICOM       = "pydicom"          # medical
    PSD         = "psd_tools"        # flattened
    IMAGEMAGICK = "imagemagick"      # universal fallback via subprocess
    UNKNOWN     = "unknown"

def detect(path: Path) -> LoaderClass: ...

def open_as_pil(
    path: Path,
    *,
    page: int = 0,           # for PDF / multi-page TIFF / PSD layers
    cache_dir: Path | None = None,
) -> PILImage.Image: ...

def open_metadata(path: Path) -> dict:
    """ExifTool if available; falls back to Pillow EXIF."""
    ...
```

### Cache behaviour

For any format that's not natively browser-renderable (HEIC, RAW, PDF
page, DICOM, PSD flatten), the loader writes a converted JPEG to
`platformdirs.user_cache_dir("annotate") / "converted"` keyed by
`(absolute_path, mtime, page)`. The HTTP handler serves that cached
JPEG when the browser requests the image. The original path stays
authoritative in the project.

This makes the server idempotent — re-opening the same HEIC won't
re-convert if mtime hasn't changed. And lets the browser load instantly
on every page reload.

### Refactor of existing code

The existing `_open_image` helper in `server.py` becomes a thin wrapper
around `image_io.open_as_pil`. The `via_add_file` extension allowlist
is replaced by `image_io.detect(path) != LoaderClass.UNKNOWN`. The HTTP
handler's `_serve_image` checks for a cached JPEG before serving.

---

## New MCP tools

### `via_load_document(path, pages="all")`

Turn a multi-page file (PDF, multi-page TIFF, PSD with layers) into
one VIA file entry per page/layer. Returns the list of fids.

```python
via_load_document("/home/alice/papers/survey.pdf", pages="all")
→ {"fids": ["1", "2", "3", "4", "5"], "page_count": 5, "loader": "pdf2image"}

via_load_document("/.../scan.pdf", pages=[0, 2, 4])
→ {"fids": ["1", "2", "3"], "loader": "pdf2image"}
```

### `via_extract_video_frames(path, every_seconds=1.0, start=None, end=None, max_frames=None)`

Run `ffmpeg` to extract frames at a sampling rate. Each frame becomes
a file entry.

```python
via_extract_video_frames("/.../game.mp4", every_seconds=2, start=300, end=420)
→ {"fids": ["12","13",...,"42"], "frame_count": 31,
   "frame_times": [302.0, 304.0, ...]}
```

Frame files land in the same cache dir as converted images.

### `via_run_ocr(fid, lang="eng", min_confidence=60)`

Tesseract over the image. Returns word-level bounding boxes as
detection candidates (same shape as `via_suggest_regions`) so the LLM
can review them with the same review flow.

```python
via_run_ocr(fid="3", lang="eng+spa")
→ {
    "fid": "3",
    "engine": "tesseract-5.4.1",
    "candidate_count": 24,
    "candidates": [
      {"xy": [2, 0.21, 0.34, 0.08, 0.02], "label": "Departure",
       "score": 0.94, "ocr_text": "Departure"},
      ...
    ],
}
```

### `via_read_metadata(fid)`

ExifTool if installed (full XMP/IPTC/EXIF), else Pillow EXIF.

```python
via_read_metadata(fid="1")
→ {
    "fid": "1",
    "source": "exiftool",
    "capture_time": "2018-05-22T05:48:00-07:00",
    "gps": {"lat": 34.6328, "lon": -120.6107, "alt": 87.4},
    "camera": "Canon EOS 5D Mark IV",
    "lens": "EF24-105mm f/4L IS II USM",
    "exposure": {"iso": 400, "fnumber": 5.6, "shutter": "1/250"},
    "orientation": "horizontal",
}
```

The 08-grace-fo-launch session's pre-dawn-vs-bright-morning prior
failure would've been caught instantly by surfacing `capture_time +
gps` at the start of the session. Phase 1 of this design should run
`via_read_metadata` automatically on first contact with each file and
include the result in `via_get_image`'s header text so the LLM sees
it without a separate tool call.

### `via_find_duplicates(fids=None, threshold=5)`

`imagehash` (pHash) across the listed files (or all files in the
project). Returns groups of near-duplicate images so the user can
prune a phone-roll dump.

```python
via_find_duplicates(threshold=5)
→ {
    "groups": [
      {"hamming_max": 2, "fids": ["3", "4", "5"]},
      {"hamming_max": 4, "fids": ["12", "13"]},
    ],
    "engine": "imagehash-phash",
}
```

---

## Implementation phases

**Phase 1 — IO layer foundations**
- `image_io.py` with detection + dispatch
- `pillow-heif` integration (highest ROI single format)
- ExifTool/EXIF metadata + `via_read_metadata`
- Refactor `_open_image`, `via_add_file`, HTTP `_serve_image` to use the loader
- Cache directory + JPEG conversion path

**Phase 2 — PDF + multi-page**
- `pdf2image` integration
- `via_load_document` tool

**Phase 3 — Video + OCR**
- `ffmpeg-python` integration
- `via_extract_video_frames`
- Tesseract OCR + `via_run_ocr`

**Phase 4 — Specialist formats**
- RAW (`rawpy`)
- GeoTIFF (`rasterio`)
- DICOM (`pydicom`)
- PSD (`psd-tools`)
- Gigapixel (`pyvips`)
- ImageMagick subprocess fallback

**Phase 5 — Dedup + niceties**
- `imagehash` and `via_find_duplicates`
- Optional: auto-rotate from EXIF orientation
- Optional: warn on color-profile mismatches

Phase 1 is the high-value MVP. Phases 2–3 round out the most-asked-for
formats. Phases 4–5 are specialist features that land on demand.

---

## Open questions

1. **Cache eviction policy** — converted JPEGs accumulate. Bound the
   cache dir to e.g. 5 GB with LRU eviction? Or trust the user to
   prune?
2. **Subprocess timeouts** — ImageMagick / ffmpeg / pdftoppm can hang
   on malformed input. Need a per-loader timeout (proposal: 30 s
   default, configurable in models.toml or a new io.toml).
3. **GeoTIFF coordinates** — annotations are currently in pixel
   fractions. For GeoTIFF, users will sometimes want lat/lon. Out of
   scope for v1 (`via_read_metadata` can return the affine transform
   and the LLM can compute), but worth knowing this is a likely
   request.
4. **Multi-page PSD layers** — PSDs can have hundreds of layers.
   Treat as "one image per layer" by default, or flatten? Proposal:
   default flatten; `via_load_document(pages="layers")` to split.
5. **OCR languages** — Tesseract needs per-language data packs
   (`tesseract-ocr-eng` etc.). Detect installed packs and surface
   them in `via_model_status`.
6. **Verify-region uses crop** — the existing AI verify-region tool
   crops with PIL; the new IO layer should make that crop call
   format-agnostic. Quick wire-up; no design change needed.

---

## What this is *not* solving

- File-format detection in the browser. VIA upstream draws on whatever
  the browser can render — our HTTP handler always serves a converted
  JPEG, so the browser doesn't have to support HEIC etc.
- Cloud storage. If a file's on S3/GCS, the user mounts it locally
  (s3fs, rclone, etc.) and passes a local path. Adding cloud loaders
  is a separate project.
- Annotation interchange with other tools (COCO, YOLO, Label Studio).
  That's an export concern, addressed by `via_save_project` plus a
  future converter library.

---

## Sources

- pillow-heif: https://github.com/bigcat88/pillow_heif (BSD, active 2026)
- rawpy: https://github.com/letmaik/rawpy
- raw-pillow-opener: https://github.com/Maddoxium/raw-pillow-opener
- pdf2image: https://github.com/Belval/pdf2image (BSD)
- pyvips: https://github.com/libvips/pyvips
- rasterio: https://github.com/rasterio/rasterio
- pytesseract: https://github.com/madmaze/pytesseract
- PyExifTool: https://github.com/sylikc/pyexiftool
- imagehash: https://github.com/JohannesBuchner/imagehash
- psd-tools: https://github.com/psd-tools/psd-tools
- pydicom: https://github.com/pydicom/pydicom
- ffmpeg-python: https://github.com/kkroening/ffmpeg-python
