"""Phase 1 IO layer tests: detect, cached_browser_path, open_metadata.

HEIC-specific tests skip when pillow-heif isn't installed. ExifTool-
specific paths skip when neither the package nor the binary are
present. Everything else runs against Pillow-native test fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from annotate import image_io
from annotate.image_io import (
    BROWSER_NATIVE,
    LoaderClass,
    _cache_key,
    cache_dir,
    cached_browser_path,
    detect,
    heif_available,
)


# --- detect() ---

@pytest.mark.parametrize("name,expected", [
    ("cat.jpg",  LoaderClass.PIL_NATIVE),
    ("cat.JPEG", LoaderClass.PIL_NATIVE),  # case-insensitive
    ("cat.png",  LoaderClass.PIL_NATIVE),
    ("cat.gif",  LoaderClass.PIL_NATIVE),
    ("cat.bmp",  LoaderClass.PIL_NATIVE),
    ("cat.webp", LoaderClass.PIL_NATIVE),
    ("cat.tif",  LoaderClass.PIL_NATIVE),
    ("cat.tiff", LoaderClass.PIL_NATIVE),
    ("cat.cr2",  LoaderClass.UNKNOWN),
    ("cat.mp4",  LoaderClass.UNKNOWN),
])
def test_detect_extension_buckets(name, expected):
    assert detect(name) == expected


def test_detect_heif_when_pillow_heif_installed():
    """Whether pillow-heif is installed flips HEIC between PIL_NATIVE-grade
    and UNKNOWN. Verify the right outcome per the local install state."""
    expected = LoaderClass.PILLOW_HEIF if heif_available() else LoaderClass.UNKNOWN
    assert detect("cat.heic") == expected
    assert detect("cat.avif") == expected


# --- BROWSER_NATIVE set ---

def test_browser_native_set_excludes_tiff_and_bmp():
    """Browsers can't render TIFF/BMP/ICO natively — they need conversion
    even though Pillow opens them fine."""
    assert ".tif" not in BROWSER_NATIVE
    assert ".bmp" not in BROWSER_NATIVE
    assert ".jpg" in BROWSER_NATIVE
    assert ".png" in BROWSER_NATIVE


# --- cached_browser_path ---

def test_cached_browser_path_native_jpg_returns_original(tmp_path):
    """No conversion or cache write for browser-native formats."""
    from PIL import Image as PILImage
    src = tmp_path / "photo.jpg"
    PILImage.new("RGB", (100, 100), color=(80, 80, 80)).save(src, "JPEG")
    result = cached_browser_path(src)
    assert result == src


def test_cached_browser_path_tiff_converts_to_jpeg(tmp_path, monkeypatch):
    """TIFF is PIL-native (so detection passes) but not browser-native,
    so a JPEG cache should be written."""
    from PIL import Image as PILImage
    # Pin the cache dir under tmp_path so we don't pollute the user cache
    monkeypatch.setattr(image_io, "cache_dir",
                        lambda: tmp_path / "_cache")
    src = tmp_path / "photo.tiff"
    PILImage.new("RGB", (40, 40), color=(120, 120, 120)).save(src, "TIFF")

    cached = cached_browser_path(src)
    assert cached != src
    assert cached.suffix == ".jpg"
    assert cached.exists()
    # Verify it's a real JPEG that PIL can open
    PILImage.open(cached).load()


def test_cached_browser_path_reuses_cache_on_second_call(tmp_path, monkeypatch):
    """Same file + unchanged mtime = same cache hit, no re-conversion."""
    from PIL import Image as PILImage
    monkeypatch.setattr(image_io, "cache_dir",
                        lambda: tmp_path / "_cache")
    src = tmp_path / "x.tiff"
    PILImage.new("RGB", (40, 40), color=(0, 0, 0)).save(src, "TIFF")

    first = cached_browser_path(src)
    mtime_first = first.stat().st_mtime_ns
    second = cached_browser_path(src)
    assert second == first
    assert second.stat().st_mtime_ns == mtime_first   # not re-written


def test_cached_browser_path_unknown_format_raises_lookup(tmp_path):
    src = tmp_path / "x.cr2"
    src.write_bytes(b"\x00" * 16)
    with pytest.raises(LookupError) as exc:
        cached_browser_path(src)
    assert "annotate[io]" in str(exc.value) or ".cr2" in str(exc.value)


def test_cache_key_changes_with_mtime(tmp_path):
    from PIL import Image as PILImage
    src = tmp_path / "x.jpg"
    PILImage.new("RGB", (10, 10)).save(src, "JPEG")
    k1 = _cache_key(src)
    # Bump mtime
    import os, time
    new_mtime = src.stat().st_mtime + 10
    os.utime(src, (new_mtime, new_mtime))
    k2 = _cache_key(src)
    assert k1 != k2


# --- open_metadata via Pillow fallback ---

def test_open_metadata_jpeg_without_exif_returns_dims_only(tmp_path):
    from PIL import Image as PILImage
    src = tmp_path / "plain.jpg"
    PILImage.new("RGB", (200, 150), color=(40, 40, 40)).save(src, "JPEG")
    meta = image_io.open_metadata(src)
    # ExifTool may or may not be installed; either way dims should populate
    assert meta.width == 200
    assert meta.height == 150
    assert meta.source in ("exiftool", "pillow")


def test_open_metadata_jpeg_with_exif_extracts_camera(tmp_path):
    """Write a JPEG with synthetic EXIF and verify the camera field
    comes back."""
    from PIL import Image as PILImage
    src = tmp_path / "with_exif.jpg"
    img = PILImage.new("RGB", (100, 100), color=(0, 0, 0))
    exif = img.getexif()
    # 0x0110 = Model (per EXIF spec)
    exif[0x0110] = "TestCam-9000"
    img.save(src, "JPEG", exif=exif)

    meta = image_io.open_metadata(src)
    assert meta.camera == "TestCam-9000"


# --- via_read_metadata handler integration ---

def test_handle_read_metadata_unknown_fid(tmp_path):
    from annotate.store import ProjectStore
    from annotate.server import handle_read_metadata, handle_add_file
    from PIL import Image as PILImage

    state = tmp_path / "state.json"
    store = ProjectStore(state_file=state)
    img_path = tmp_path / "x.jpg"
    PILImage.new("RGB", (50, 50)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))

    result = handle_read_metadata(store, registry, fid="99")
    assert "not found" in result[0].text.lower()


def test_handle_read_metadata_returns_dims(tmp_path):
    import json as _json
    from annotate.store import ProjectStore
    from annotate.server import handle_read_metadata, handle_add_file
    from PIL import Image as PILImage

    state = tmp_path / "state.json"
    store = ProjectStore(state_file=state)
    img_path = tmp_path / "x.jpg"
    PILImage.new("RGB", (300, 200)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))

    result = handle_read_metadata(store, registry, fid="1")
    data = _json.loads(result[0].text)
    assert data["fname"] == "x.jpg"
    assert data["dims"] == [300, 200]
    assert data["source"] in ("exiftool", "pillow")


# --- PDF support (skips when pdf2image / poppler not installed) ---

def _write_multipage_pdf(path: Path, page_count: int) -> Path:
    """Use Pillow's PDF writer to build a fixture without an extra dep."""
    from PIL import Image as PILImage
    pages = [
        PILImage.new("RGB", (300, 200), color=(i * 30 % 256, 80, 80))
        for i in range(page_count)
    ]
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    return path


def _poppler_available() -> bool:
    """Check if poppler-utils' pdftoppm is on PATH."""
    import shutil
    return shutil.which("pdftoppm") is not None


_NEEDS_PDF = pytest.mark.skipif(
    not (image_io.pdf_available() and _poppler_available()),
    reason="pdf2image and/or poppler-utils not installed",
)


def test_detect_pdf_depends_on_pdf2image_availability(tmp_path):
    """Whether pdf2image is installed flips detect() between PDF and UNKNOWN."""
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4\n...")
    expected = LoaderClass.PDF if image_io.pdf_available() else LoaderClass.UNKNOWN
    assert detect(p) == expected


@_NEEDS_PDF
def test_pdf_page_count(tmp_path):
    p = _write_multipage_pdf(tmp_path / "three.pdf", 3)
    assert image_io.pdf_page_count(p) == 3


@_NEEDS_PDF
def test_cached_browser_path_pdf_caches_per_page(tmp_path, monkeypatch):
    monkeypatch.setattr(image_io, "cache_dir", lambda: tmp_path / "_cache")
    p = _write_multipage_pdf(tmp_path / "two.pdf", 2)
    p0 = image_io.cached_browser_path(p, page=0)
    p1 = image_io.cached_browser_path(p, page=1)
    assert p0 != p1
    assert p0.exists() and p1.exists()
    # And same call returns same cached file (no re-render)
    p0_again = image_io.cached_browser_path(p, page=0)
    assert p0_again == p0
    assert p0.stat().st_mtime_ns == p0_again.stat().st_mtime_ns


@_NEEDS_PDF
def test_handle_load_document_all_pages(tmp_path, monkeypatch):
    import json as _json
    from annotate.store import ProjectStore
    from annotate.server import handle_load_document
    monkeypatch.setattr(image_io, "cache_dir", lambda: tmp_path / "_cache")

    pdf_path = _write_multipage_pdf(tmp_path / "doc.pdf", 4)
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    result = handle_load_document(store, registry, 9669, str(pdf_path), "all")
    data = _json.loads(result[0].text)
    assert len(data["fids"]) == 4
    assert data["page_count"] == 4
    assert data["pages_loaded"] == [1, 2, 3, 4]

    # Verify each file entry carries the source PDF link
    project = store.get()
    for fid in data["fids"]:
        entry = project["file"][fid]
        assert entry["source_pdf"] == str(pdf_path)
        assert "source_pdf_page" in entry
        assert entry["abs_path"].endswith(".jpg")


@_NEEDS_PDF
def test_handle_load_document_specific_pages(tmp_path, monkeypatch):
    import json as _json
    from annotate.store import ProjectStore
    from annotate.server import handle_load_document
    monkeypatch.setattr(image_io, "cache_dir", lambda: tmp_path / "_cache")

    pdf_path = _write_multipage_pdf(tmp_path / "doc.pdf", 5)
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    result = handle_load_document(store, registry, 9669, str(pdf_path),
                                   pages=[0, 2, 4])
    data = _json.loads(result[0].text)
    assert len(data["fids"]) == 3
    assert data["pages_loaded"] == [1, 3, 5]


@_NEEDS_PDF
def test_handle_load_document_filters_out_of_range_pages(tmp_path, monkeypatch):
    import json as _json
    from annotate.store import ProjectStore
    from annotate.server import handle_load_document
    monkeypatch.setattr(image_io, "cache_dir", lambda: tmp_path / "_cache")

    pdf_path = _write_multipage_pdf(tmp_path / "doc.pdf", 2)
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    # Asks for pages 0 (valid), 5 (out of range), and 1 (valid)
    result = handle_load_document(store, registry, 9669, str(pdf_path),
                                   pages=[0, 5, 1])
    data = _json.loads(result[0].text)
    assert data["pages_loaded"] == [1, 2]


def test_handle_load_document_rejects_non_pdf(tmp_path):
    from annotate.store import ProjectStore
    from annotate.server import handle_load_document
    from PIL import Image as PILImage

    img_path = tmp_path / "not_a_pdf.jpg"
    PILImage.new("RGB", (50, 50)).save(img_path, "JPEG")
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    result = handle_load_document(store, registry, 9669, str(img_path), "all")
    assert "PDF" in result[0].text


# --- OCR (skips when pytesseract / tesseract binary not installed) ---

def _tesseract_available() -> bool:
    if not image_io.ocr_available():
        return False
    import shutil
    return shutil.which("tesseract") is not None


_NEEDS_OCR = pytest.mark.skipif(
    not _tesseract_available(),
    reason="pytesseract and/or tesseract binary not installed",
)


def _render_text_image(path: Path, text: str = "HELLO ANNOTATE",
                       size=(400, 120)) -> Path:
    """Render plain text onto a white image so Tesseract has something
    high-contrast to read. PIL ships a default bitmap font that
    Tesseract handles well even at small sizes."""
    from PIL import Image as PILImage, ImageDraw, ImageFont
    img = PILImage.new("RGB", size, color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
    except (OSError, IOError):
        font = ImageFont.load_default()
    draw.text((20, 30), text, fill=(0, 0, 0), font=font)
    img.save(path, "PNG")
    return path


@_NEEDS_OCR
def test_run_ocr_finds_rendered_words(tmp_path):
    p = _render_text_image(tmp_path / "hello.png", "HELLO WORLD")
    from PIL import Image as PILImage
    img = PILImage.open(p)
    words = image_io.run_ocr(img, lang="eng", min_confidence=20)
    texts = [w.text.upper() for w in words]
    assert any("HELLO" in t for t in texts)
    assert any("WORLD" in t for t in texts)
    # Bboxes should be inside the unit square
    for w in words:
        assert 0 <= w.x <= 1 and 0 <= w.y <= 1
        assert 0 < w.w <= 1 and 0 < w.h <= 1


@_NEEDS_OCR
def test_run_ocr_filters_by_confidence(tmp_path):
    p = _render_text_image(tmp_path / "hi.png", "HI")
    from PIL import Image as PILImage
    img = PILImage.open(p)
    low = image_io.run_ocr(img, min_confidence=0)
    high = image_io.run_ocr(img, min_confidence=99)
    assert len(low) >= len(high)


@_NEEDS_OCR
def test_handle_run_ocr_returns_candidates(tmp_path):
    import json as _json
    from annotate.store import ProjectStore
    from annotate.server import handle_add_file, handle_run_ocr

    p = _render_text_image(tmp_path / "hello.png", "HELLO ANNOTATE")
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(p))
    result = handle_run_ocr(store, registry, fid="1", min_confidence=20)
    data = _json.loads(result[0].text)
    assert data["engine"].startswith("tesseract-")
    assert data["candidate_count"] > 0
    # At least one candidate should have a matching token
    texts = [c["label"].upper() for c in data["candidates"]]
    assert any("HELLO" in t for t in texts)


@_NEEDS_OCR
def test_handle_run_ocr_region_bbox_remaps_coords(tmp_path):
    """OCR over a sub-region — output coords should be fractions of the
    *whole* image, not the crop."""
    import json as _json
    from annotate.store import ProjectStore
    from annotate.server import handle_add_file, handle_run_ocr

    p = _render_text_image(tmp_path / "hello.png", "BIG TEXT",
                            size=(800, 200))
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(p))
    # OCR only the left half
    result = handle_run_ocr(store, registry, fid="1",
                            region_bbox=[0, 0, 0.5, 1.0],
                            min_confidence=20)
    data = _json.loads(result[0].text)
    # Every returned candidate must sit in the left half of the image
    for c in data["candidates"]:
        assert c["xy"][1] + c["xy"][3] <= 0.55  # x + w within the queried region (+ tolerance)


def test_handle_run_ocr_without_extra_returns_install_hint(tmp_path, monkeypatch):
    """When pytesseract isn't importable, the handler returns the install
    hint without trying anything else."""
    from annotate.store import ProjectStore
    from annotate.server import handle_add_file, handle_run_ocr
    from PIL import Image as PILImage
    monkeypatch.setattr(image_io, "ocr_available", lambda: False)

    p = tmp_path / "x.png"
    PILImage.new("RGB", (50, 50), color=(255, 255, 255)).save(p, "PNG")
    store = ProjectStore(state_file=tmp_path / "state.json")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(p))
    result = handle_run_ocr(store, registry, fid="1")
    assert "annotate[ocr]" in result[0].text
