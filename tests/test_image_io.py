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
    ("cat.pdf",  LoaderClass.UNKNOWN),
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
