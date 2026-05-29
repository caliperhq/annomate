"""
annotate server — HTTP (VIA push/pull) + MCP stdio in one process.
"""

import argparse
import asyncio
import base64
import io
import json
import mimetypes
import os
import random
import string
import sys
import threading
import webbrowser
from http.server import HTTPServer
from importlib.resources import files
from pathlib import Path

import mcp.server
import mcp.server.stdio
import mcp.types as types
import platformdirs

from annotate.store import ProjectStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(s: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=s)]


def _gen_metadata_id(existing: dict) -> str:
    chars = string.ascii_letters + string.digits + "-_"
    while True:
        mid = "".join(random.choices(chars, k=8))
        if mid not in existing:
            return mid


def _normalize_av(project: dict, av: dict) -> tuple[dict | None, str | None]:
    """Map aname keys to attribute IDs and validate against the schema.

    Returns (fixed_av, error). If the schema is empty, accept keys as-is
    (back-compat with bare/hand-rolled projects).
    """
    attributes = project.get("attribute", {})
    if not attributes:
        return {str(k): str(v) for k, v in av.items()}, None
    aname_to_id = {
        a.get("aname"): aid
        for aid, a in attributes.items()
        if a.get("aname")
    }
    fixed: dict = {}
    unknown: list[str] = []
    for k, v in av.items():
        k = str(k)
        if k in attributes:
            fixed[k] = str(v)
        elif k in aname_to_id:
            fixed[aname_to_id[k]] = str(v)
        else:
            unknown.append(k)
    if unknown:
        valid = ", ".join(
            f"'{aid}' (aname='{a.get('aname', '')}')"
            for aid, a in attributes.items()
        )
        return None, (
            f"Unknown av key(s): {unknown}. Valid keys are attribute IDs "
            f"or anames. Schema has: {valid}"
        )
    return fixed, None


def _resolve_fid_for_vid(project: dict, vid: str) -> str | None:
    view = project.get("view", {}).get(str(vid))
    if not view:
        return None
    fid_list = view.get("fid_list") or []
    if not fid_list:
        return None
    return str(fid_list[0])


def _image_dims(project: dict, image_registry: dict, fid: str) -> tuple[int, int] | None:
    entry = project.get("file", {}).get(str(fid))
    if not entry:
        return None
    abs_path = entry.get("abs_path") or image_registry.get(entry.get("fname", ""))
    if not abs_path:
        return None
    from pathlib import Path as _Path
    p = _Path(abs_path)
    if not p.exists():
        return None
    from PIL import Image as PILImage
    with PILImage.open(p) as img:
        return img.size  # (w, h)


def _scale_xy(xy: list, w: int, h: int) -> list:
    """Scale fractional xy (0.0–1.0) into original pixel space.

    Lengths (rectangle w/h, ellipse rx/ry) scale per-axis; circle radius
    scales by the geometric mean to preserve aspect.
    """
    if not xy:
        return xy
    shape = xy[0]
    coords = xy[1:]
    if shape == 2 and len(coords) == 4:  # rect [x, y, w, h]
        x, y, rw, rh = coords
        return [shape, x * w, y * h, rw * w, rh * h]
    if shape == 3 and len(coords) == 3:  # circle [cx, cy, r]
        cx, cy, r = coords
        return [shape, cx * w, cy * h, r * ((w + h) / 2)]
    if shape == 4 and len(coords) == 4:  # ellipse [cx, cy, rx, ry]
        cx, cy, rx, ry = coords
        return [shape, cx * w, cy * h, rx * w, ry * h]
    out: list = [shape]
    for i, c in enumerate(coords):
        out.append(c * (w if i % 2 == 0 else h))
    return out


# ---------------------------------------------------------------------------
# Helpers for project structure
# ---------------------------------------------------------------------------

def _minimal_project() -> dict:
    import time
    ts = str(int(time.time()))
    return {
        "project": {
            "pid": "__VIA_PROJECT_ID__",
            "rev": "1",
            "rev_timestamp": ts,
            "pname": "Untitled",
            "data_format_version": "3.1.1",
            "creator": "annotate (https://github.com/caliperhq/annotate)",
            "created": int(time.time() * 1000),
            "vid_list": [],
        },
        "config": {
            "file": {"loc_prefix": {"1": "", "2": "", "3": "", "4": ""}},
            "ui": {
                "file_content_align": "center",
                "file_metadata_editor_visible": True,
                "spatial_metadata_editor_visible": True,
                "temporal_segment_metadata_editor_visible": True,
                "spatial_region_label_attribute_id": "1",
                "gtimeline_visible_row_count": "4",
            },
        },
        # Default schema: label (on-canvas) + description (longer notes).
        # anchor_id "FILE1_Z0_XY1" = spatial region on an image file.
        # type 1 = TEXT. VIA renders attribute "1" as the on-canvas label.
        "attribute": {
            "1": {
                "aname": "label",
                "anchor_id": "FILE1_Z0_XY1",
                "type": 1,
                "desc": "Short region label shown on the canvas",
                "options": {},
                "default_option_id": "",
            },
            "2": {
                "aname": "description",
                "anchor_id": "FILE1_Z0_XY1",
                "type": 1,
                "desc": "Longer annotation notes",
                "options": {},
                "default_option_id": "",
            },
        },
        "file": {},
        "view": {},
        "metadata": {},
    }


def _next_id(mapping: dict) -> int:
    """Return the next integer key (as int) for a VIA file/view dict."""
    return max((int(k) for k in mapping if k.isdigit()), default=0) + 1


# ---------------------------------------------------------------------------
# Tool handlers — take store as first arg for testability
# ---------------------------------------------------------------------------

def handle_get_project(store: ProjectStore) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded. Ask the user to open the VIA annotator and push a project.")
    return _text(json.dumps(project, indent=2))


def handle_list_files(store: ProjectStore) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    file_list = [
        {"id": fid, "name": f["fname"], "type": f["type"]}
        for fid, f in project.get("file", {}).items()
    ]
    return _text(json.dumps(file_list, indent=2))


def _xy_to_fraction(xy: list, w: int, h: int) -> list:
    """Inverse of _scale_xy: return original-space xy as 0–1 fractions of (w, h)."""
    if not xy or w <= 0 or h <= 0:
        return xy
    shape = xy[0]
    coords = xy[1:]
    if shape == 2 and len(coords) == 4:  # rect [x, y, w, h]
        x, y, rw, rh = coords
        return [shape, x / w, y / h, rw / w, rh / h]
    if shape == 3 and len(coords) == 3:  # circle [cx, cy, r]
        cx, cy, r = coords
        return [shape, cx / w, cy / h, r / ((w + h) / 2)]
    if shape == 4 and len(coords) == 4:  # ellipse [cx, cy, rx, ry]
        cx, cy, rx, ry = coords
        return [shape, cx / w, cy / h, rx / w, ry / h]
    out: list = [shape]
    for i, c in enumerate(coords):
        out.append(c / (w if i % 2 == 0 else h))
    return out


def handle_get_annotations(
    store: ProjectStore,
    image_registry: dict,
    vid: str | None,
    format: str = "pixel",
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if format not in ("pixel", "fraction", "both"):
        return _text(f"format must be 'pixel', 'fraction', or 'both', got {format!r}")
    metadata = project.get("metadata", {})
    if vid is not None:
        metadata = {mid: m for mid, m in metadata.items() if m.get("vid") == vid}
    if format == "pixel":
        return _text(json.dumps(metadata, indent=2))
    # 'fraction' or 'both' — resolve dims per region via its vid's fid_list
    dims_cache: dict[str, tuple[int, int] | None] = {}

    def dims_for(v: str) -> tuple[int, int] | None:
        if v in dims_cache:
            return dims_cache[v]
        fid = _resolve_fid_for_vid(project, v)
        d = _image_dims(project, image_registry, fid) if fid else None
        dims_cache[v] = d
        return d

    out: dict = {}
    for mid, m in metadata.items():
        v = m.get("vid", "")
        d = dims_for(v)
        if d is None:
            # dims unavailable — keep pixel coords and flag it
            entry = {**m, "_dims_unavailable": True}
            out[mid] = entry
            continue
        w, h = d
        frac_xy = _xy_to_fraction(m.get("xy") or [], w, h)
        if format == "fraction":
            out[mid] = {**m, "xy": frac_xy}
        else:  # both
            out[mid] = {**m, "xy_fraction": frac_xy, "dims": [w, h]}
    return _text(json.dumps(out, indent=2))


def handle_add_region(
    store: ProjectStore,
    image_registry: dict,
    vid: str,
    z: list,
    xy: list,
    av: dict,
    xy_space: str = "original",
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if not isinstance(av, dict):
        return _text(f"'av' must be a dict, got {type(av).__name__}")
    if vid not in project.get("view", {}):
        return _text(f"View '{vid}' not found. Use via_list_files to see available views.")
    fixed_av, err = _normalize_av(project, av)
    if err:
        return _text(err)
    if xy_space == "fraction":
        fid = _resolve_fid_for_vid(project, vid)
        dims = _image_dims(project, image_registry, fid) if fid else None
        if dims is None:
            return _text(
                f"xy_space='fraction' requires image dims for vid={vid}; "
                "image path is unavailable. Use xy_space='original' or load the file via via_add_file."
            )
        xy = _scale_xy(xy, dims[0], dims[1])
    elif xy_space != "original":
        return _text(f"xy_space must be 'original' or 'fraction', got {xy_space!r}")
    mid = _gen_metadata_id(project["metadata"])
    project["metadata"][mid] = {
        "vid": vid,
        "flg": 0,
        "z": z,
        "xy": xy,
        "av": fixed_av,
    }
    # set_project() is a last-write-wins overwrite; concurrent browser pushes will
    # see ProjectConflictError on next sync and re-pull.
    store.set_project(project)
    label = fixed_av.get("1", "")
    return _text(json.dumps({"metadata_id": mid, "label": label}))


def handle_update_region(
    store: ProjectStore,
    image_registry: dict,
    metadata_id: str,
    z: list,
    xy: list,
    av: dict,
    xy_space: str = "original",
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if not isinstance(av, dict):
        return _text(f"'av' must be a dict, got {type(av).__name__}")
    if metadata_id not in project.get("metadata", {}):
        return _text(f"Metadata ID '{metadata_id}' not found.")
    fixed_av, err = _normalize_av(project, av)
    if err:
        return _text(err)
    if xy_space == "fraction":
        vid = project["metadata"][metadata_id].get("vid")
        fid = _resolve_fid_for_vid(project, vid) if vid else None
        dims = _image_dims(project, image_registry, fid) if fid else None
        if dims is None:
            return _text(
                f"xy_space='fraction' requires image dims; could not resolve for metadata_id={metadata_id}."
            )
        xy = _scale_xy(xy, dims[0], dims[1])
    elif xy_space != "original":
        return _text(f"xy_space must be 'original' or 'fraction', got {xy_space!r}")
    project["metadata"][metadata_id]["z"] = z
    project["metadata"][metadata_id]["xy"] = xy
    project["metadata"][metadata_id]["av"] = fixed_av
    store.set_project(project)
    return _text("Updated.")


def handle_delete_region(
    store: ProjectStore, metadata_id: str
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if metadata_id not in project.get("metadata", {}):
        return _text(f"Metadata ID '{metadata_id}' not found.")
    del project["metadata"][metadata_id]
    store.set_project(project)
    return _text("Deleted.")


def handle_add_file(
    store: ProjectStore,
    image_registry: dict,
    port: int,
    path: str,
) -> list[types.TextContent]:
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        return _text(f"File not found: {abs_path}")
    suffix = abs_path.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
        return _text(f"Unsupported image format: {suffix}")

    project = store.get()
    if project is None:
        project = _minimal_project()

    # Resolve basename conflicts: if another file already occupies this basename
    # with a different path, prefix with a counter.
    fname = abs_path.name
    if fname in image_registry and image_registry[fname] != str(abs_path):
        stem, ext = abs_path.stem, abs_path.suffix
        counter = 2
        while f"{stem}_{counter}{ext}" in image_registry:
            counter += 1
        fname = f"{stem}_{counter}{ext}"

    image_registry[fname] = str(abs_path)
    src = f"http://localhost:{port}/img/{fname}"

    fid = _next_id(project["file"])
    vid = _next_id(project["view"])
    # abs_path is a annotate extension field; VIA ignores unknown keys
    project["file"][str(fid)] = {"fid": fid, "fname": fname, "type": 2, "loc": 2, "src": src, "abs_path": str(abs_path)}
    project["view"][str(vid)] = {"fid_list": [fid]}
    project["project"].setdefault("vid_list", []).append(str(vid))

    store.set_project(project)
    return _text(json.dumps({"fid": str(fid), "vid": str(vid), "url": src}))


def handle_update_project(
    store: ProjectStore, project_json: str
) -> list[types.TextContent]:
    try:
        project = json.loads(project_json)
    except json.JSONDecodeError as e:
        return _text(f"Invalid JSON: {e}")
    required = {"project", "file", "view", "metadata", "attribute"}
    missing = required - set(project.keys())
    if missing:
        return _text(f"Missing required keys: {sorted(missing)}")
    store.set_project(project)
    return _text("Project updated.")


_OVERLAY_PALETTE = [
    (255, 60, 60), (60, 200, 60), (60, 140, 255), (255, 180, 40),
    (220, 60, 220), (40, 220, 220), (255, 120, 40), (160, 100, 255),
]


def _region_bbox_orig(shape: int, coords: list) -> tuple[float, float, float, float] | None:
    """Return (x0, y0, x1, y1) of the region in original-image space, or None."""
    try:
        if shape == 1 and len(coords) >= 2:
            x, y = coords[0], coords[1]
            return (x, y, x, y)
        if shape == 2 and len(coords) >= 4:
            x, y, w, h = coords[0], coords[1], coords[2], coords[3]
            return (x, y, x + w, y + h)
        if shape == 3 and len(coords) >= 3:
            cx, cy, r = coords[0], coords[1], coords[2]
            return (cx - r, cy - r, cx + r, cy + r)
        if shape == 4 and len(coords) >= 4:
            cx, cy, rx, ry = coords[0], coords[1], coords[2], coords[3]
            return (cx - rx, cy - ry, cx + rx, cy + ry)
        if shape in (6, 7) and len(coords) >= 4:
            xs = [coords[i] for i in range(0, len(coords) - 1, 2)]
            ys = [coords[i + 1] for i in range(0, len(coords) - 1, 2)]
            return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, IndexError):
        return None
    return None


def _draw_overlay(img, project: dict, fid: str) -> None:
    """Draw all regions belonging to views referencing `fid` onto img (in-place).
    Coordinates in project metadata are original-space; img may be downscaled,
    so we scale per-axis. When drawing onto a crop, regions whose bounding box
    lies entirely outside the crop viewport are skipped to avoid phantom labels
    rendered at clamped edges.
    """
    from PIL import ImageDraw, ImageFont
    view_to_fids = {
        vid: [str(x) for x in v.get("fid_list", [])]
        for vid, v in project.get("view", {}).items()
    }
    relevant = [
        (mid, m) for mid, m in project.get("metadata", {}).items()
        if str(fid) in view_to_fids.get(m.get("vid", ""), [])
    ]
    if not relevant:
        return
    # The viewport spans (ox, oy) → (ox + view_w, oy + view_h) in original
    # pixel space. For full-image overlays this is the whole image; for crops
    # the caller sets _overlay_orig_w/h to the crop's original-space size.
    view_w = getattr(img, "_overlay_orig_w", img.size[0])
    view_h = getattr(img, "_overlay_orig_h", img.size[1])
    ox = getattr(img, "_overlay_offset_x", 0)
    oy = getattr(img, "_overlay_offset_y", 0)
    sx = img.size[0] / view_w
    sy = img.size[1] / view_h
    vp_x0, vp_y0 = ox, oy
    vp_x1, vp_y1 = ox + view_w, oy + view_h
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for i, (mid, m) in enumerate(relevant):
        xy = m.get("xy") or []
        if not xy or not isinstance(xy[0], (int, float)):
            continue
        shape = int(xy[0]) if xy[0] == int(xy[0]) else 0
        coords = xy[1:]
        bbox = _region_bbox_orig(shape, coords)
        if bbox is None:
            continue
        bx0, by0, bx1, by1 = bbox
        # skip regions that don't intersect the viewport at all
        if bx1 < vp_x0 or bx0 > vp_x1 or by1 < vp_y0 or by0 > vp_y1:
            continue
        color = _OVERLAY_PALETTE[i % len(_OVERLAY_PALETTE)]
        label = (m.get("av") or {}).get("1", "")
        anchor = None
        try:
            if shape == 1 and len(coords) >= 2:  # point
                x, y = (coords[0] - ox) * sx, (coords[1] - oy) * sy
                r = 4
                draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=2)
                anchor = (x, y)
            elif shape == 2 and len(coords) >= 4:  # rect [x, y, w, h]
                x, y = (coords[0] - ox) * sx, (coords[1] - oy) * sy
                w, h = coords[2] * sx, coords[3] * sy
                draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
                anchor = (x, y)
            elif shape == 3 and len(coords) >= 3:  # circle [cx, cy, r]
                cx, cy = (coords[0] - ox) * sx, (coords[1] - oy) * sy
                r = coords[2] * ((sx + sy) / 2)
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=2)
                anchor = (cx - r, cy - r)
            elif shape == 4 and len(coords) >= 4:  # ellipse [cx, cy, rx, ry]
                cx, cy = (coords[0] - ox) * sx, (coords[1] - oy) * sy
                rx, ry = coords[2] * sx, coords[3] * sy
                draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=color, width=2)
                anchor = (cx - rx, cy - ry)
            elif shape in (6, 7) and len(coords) >= 4:  # polyline / polygon
                pts = [((coords[i] - ox) * sx, (coords[i + 1] - oy) * sy)
                       for i in range(0, len(coords) - 1, 2)]
                if shape == 7 and len(pts) >= 3:
                    draw.polygon(pts, outline=color, width=2)
                else:
                    draw.line(pts, fill=color, width=2)
                anchor = pts[0]
        except (TypeError, IndexError):
            continue
        if label and anchor is not None:
            # Anchor the label at the top-left of the visible-in-viewport
            # portion of the region's bbox. Without this, regions whose top-
            # left is above/left of the viewport get labels rendered at the
            # clamped crop edge — the phantom-label bug from 12-vermeer.
            ivx0 = max(bx0, vp_x0)
            ivy0 = max(by0, vp_y0)
            lx = (ivx0 - ox) * sx + 3
            ly_top = (ivy0 - oy) * sy - 12
            ly = ly_top if ly_top >= 0 else (ivy0 - oy) * sy + 3
            lx = max(0, min(img.size[0] - 1, lx))
            ly = max(0, min(img.size[1] - 1, ly))
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                draw.text((lx + dx, ly + dy), label, fill=(0, 0, 0), font=font)
            draw.text((lx, ly), label, fill=color, font=font)


def handle_get_image(
    store: ProjectStore,
    image_registry: dict,
    fid: str,
    max_dim: int = 2048,
    overlay: bool = False,
) -> list:
    from PIL import Image as PILImage
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    file_entry = project.get("file", {}).get(str(fid))
    if file_entry is None:
        return _text(f"File ID '{fid}' not found. Use via_list_files to see available files.")
    abs_path = file_entry.get("abs_path") or image_registry.get(file_entry.get("fname", ""))
    if not abs_path:
        return _text(f"Image path not available for fid={fid} (file may have been loaded via browser, not via_add_file).")
    from pathlib import Path as _Path
    p = _Path(abs_path)
    if not p.exists():
        return _text(f"Image file not found at {abs_path}")
    with PILImage.open(p) as img:
        orig_w, orig_h = img.size
        orig_fmt = img.format or "JPEG"
        display_w, display_h = orig_w, orig_h
        if max_dim and max(orig_w, orig_h) > max_dim:
            scale = max_dim / max(orig_w, orig_h)
            display_w = round(orig_w * scale)
            display_h = round(orig_h * scale)
            img = img.resize((display_w, display_h), PILImage.LANCZOS)
        if orig_fmt == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if overlay:
            # ensure we can draw colored shapes on JPEGs
            if img.mode != "RGB":
                img = img.convert("RGB")
            img._overlay_orig_w = orig_w
            img._overlay_orig_h = orig_h
            _draw_overlay(img, project, str(fid))
        buf = io.BytesIO()
        img.save(buf, format=orig_fmt)
        data = base64.b64encode(buf.getvalue()).decode()
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    header = (
        f"Image fid={fid} fname={file_entry.get('fname')} "
        f"original={orig_w}×{orig_h}px"
        + (f" returned={display_w}×{display_h}px" if (display_w, display_h) != (orig_w, orig_h) else "")
        + (" overlay=on (existing regions drawn)" if overlay else "")
        + ". Coordinates you provide must be in original pixel space."
    )
    return [
        types.TextContent(type="text", text=header),
        types.ImageContent(type="image", data=data, mimeType=mime),
    ]


def handle_get_image_crop(
    store: ProjectStore,
    image_registry: dict,
    fid: str,
    bbox: list,
    xy_space: str = "original",
    max_dim: int = 2048,
    overlay: bool = False,
) -> list:
    """Return a high-resolution crop of the original image.

    Use when a region of the full image is too small to verify at the
    standard downscaled resolution. The crop is taken from the *original*
    image, then downscaled only if it still exceeds max_dim.
    """
    from PIL import Image as PILImage
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    file_entry = project.get("file", {}).get(str(fid))
    if file_entry is None:
        return _text(f"File ID '{fid}' not found. Use via_list_files to see available files.")
    abs_path = file_entry.get("abs_path") or image_registry.get(file_entry.get("fname", ""))
    if not abs_path:
        return _text(f"Image path not available for fid={fid}.")
    from pathlib import Path as _Path
    p = _Path(abs_path)
    if not p.exists():
        return _text(f"Image file not found at {abs_path}")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return _text("bbox must be [x, y, w, h]")
    if xy_space not in ("original", "fraction"):
        return _text(f"xy_space must be 'original' or 'fraction', got {xy_space!r}")
    with PILImage.open(p) as img:
        orig_w, orig_h = img.size
        orig_fmt = img.format or "JPEG"
        x, y, w, h = bbox
        if xy_space == "fraction":
            x, w = x * orig_w, w * orig_w
            y, h = y * orig_h, h * orig_h
        x = max(0, min(orig_w - 1, x))
        y = max(0, min(orig_h - 1, y))
        w = max(1, min(orig_w - x, w))
        h = max(1, min(orig_h - y, h))
        crop = img.crop((int(x), int(y), int(x + w), int(y + h)))
        crop_w, crop_h = crop.size
        display_w, display_h = crop_w, crop_h
        if max_dim and max(crop_w, crop_h) > max_dim:
            scale = max_dim / max(crop_w, crop_h)
            display_w = round(crop_w * scale)
            display_h = round(crop_h * scale)
            crop = crop.resize((display_w, display_h), PILImage.LANCZOS)
        if orig_fmt == "JPEG" and crop.mode in ("RGBA", "P"):
            crop = crop.convert("RGB")
        if overlay:
            if crop.mode != "RGB":
                crop = crop.convert("RGB")
            crop._overlay_orig_w = crop_w
            crop._overlay_orig_h = crop_h
            crop._overlay_offset_x = int(x)
            crop._overlay_offset_y = int(y)
            _draw_overlay(crop, project, str(fid))
        buf = io.BytesIO()
        crop.save(buf, format=orig_fmt)
        data = base64.b64encode(buf.getvalue()).decode()
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    header = (
        f"Crop fid={fid} fname={file_entry.get('fname')} "
        f"window=({int(x)},{int(y)},{int(w)}×{int(h)}) of original {orig_w}×{orig_h}px"
        + (f" returned={display_w}×{display_h}px" if (display_w, display_h) != (crop_w, crop_h) else "")
        + (" overlay=on" if overlay else "")
        + ". Writes still address the full image, not this crop. "
        f"For xy_space='original' coords measured inside this crop: "
        f"add ({int(x)}, {int(y)}) to (x, y). "
        f"For xy_space='fraction': always compute against the full {orig_w}×{orig_h} dims, "
        f"never against the crop window."
    )
    return [
        types.TextContent(type="text", text=header),
        types.ImageContent(type="image", data=data, mimeType=mime),
    ]


def handle_save_project(store: ProjectStore, path: str) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.parent.exists():
        return _text(f"Directory not found: {p.parent}")
    p.write_text(json.dumps(project, indent=2), encoding="utf-8")
    return _text(f"Saved to {p}")


# ---------------------------------------------------------------------------
# Local-model assistance (Phase 1 — registry + stub tools, no weights yet)
# ---------------------------------------------------------------------------

def handle_model_status(registry) -> list[types.TextContent]:
    if registry is None:
        return _text(json.dumps({
            "ai_extra_available": False,
            "loaded": [],
            "configured_pipelines": [],
            "hint": "Local-model assistance is disabled. Install with: pip install 'annotate[ai]'",
        }, indent=2))
    return _text(json.dumps(registry.status(), indent=2))


def _ai_stub_response(tool_name: str, registry, reason: str | None = None) -> list[types.TextContent]:
    """Returned by AI tools whose adapter isn't available yet. Carries
    enough context for the LLM to either suggest an install or pick a
    different pipeline.
    """
    from annotate.models import ai_extra_available

    msg = {
        "tool": tool_name,
        "available": False,
        "reason": reason or (
            "Local-model assistance tools are scaffolded but the requested "
            "capability has no adapter registered for the configured model."
        ),
        "ai_extra_installed": ai_extra_available(),
    }
    if not msg["ai_extra_installed"]:
        msg["install_hint"] = "pip install 'annotate[ai]'"
    if registry is not None:
        msg["registered_adapter_prefixes"] = registry.status()["registered_adapter_prefixes"]
        msg["configured_pipelines"] = registry.status()["configured_pipelines"]
    return _text(json.dumps(msg, indent=2))


# ---------------------------------------------------------------------------
# Phase 2: detection (open-vocab) + tiling
# ---------------------------------------------------------------------------

def _open_image(project: dict, image_registry_map: dict, fid: str):
    """Return (PIL.Image, abs_path_str) or (None, error_text)."""
    from PIL import Image as PILImage
    from pathlib import Path as _Path
    file_entry = project.get("file", {}).get(str(fid))
    if file_entry is None:
        return None, f"File ID {fid!r} not found."
    abs_path = file_entry.get("abs_path") or image_registry_map.get(file_entry.get("fname", ""))
    if not abs_path:
        return None, f"Image path not available for fid={fid}."
    p = _Path(abs_path)
    if not p.exists():
        return None, f"Image file not found at {abs_path}."
    return PILImage.open(p), str(p)


def _existing_boxes_fraction(
    project: dict, image_registry_map: dict, fid: str,
) -> list[tuple[float, float, float, float]]:
    """Return existing region rect bboxes in fraction space for the given fid.

    Non-rect shapes are converted to their axis-aligned bbox.
    """
    dims = _image_dims(project, image_registry_map, str(fid))
    if dims is None:
        return []
    w, h = dims
    if w <= 0 or h <= 0:
        return []

    view_to_fids = {
        vid: [str(x) for x in v.get("fid_list", [])]
        for vid, v in project.get("view", {}).items()
    }
    out: list[tuple[float, float, float, float]] = []
    for _mid, m in project.get("metadata", {}).items():
        if str(fid) not in view_to_fids.get(m.get("vid", ""), []):
            continue
        xy = m.get("xy") or []
        if not xy:
            continue
        head = xy[0]
        shape = int(head) if isinstance(head, (int, float)) and head == int(head) else 0
        bbox = _region_bbox_orig(shape, xy[1:])
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        out.append((x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h))
    return out


def handle_suggest_regions(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    fid: str,
    prompts: list[str] | None,
    tiling: str = "auto",
    exclude_existing: bool = False,
    broad_prompts: bool = False,
    min_confidence: float = 0.3,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    from annotate.models import NotInstalledError
    from annotate.models.tiling import (
        DEFAULT_BROAD_PROMPTS, CandidateBox, auto_grid, filter_existing,
        generate_tiles, nms_merge, parse_grid,
    )

    if registry is None:
        return _ai_stub_response(
            "via_suggest_regions", None,
            reason="Model registry not initialised (server started with --no-ai or init failed).",
        )

    project = store.get()
    if project is None:
        return _text("No project loaded.")

    img, err_or_path = _open_image(project, image_registry_map, fid)
    if img is None:
        return _text(err_or_path)
    pil_image = img
    W, H = pil_image.size

    # Eager-import adapter modules so their factories are registered.
    # Safe even without [ai] — the modules don't import torch at module load.
    try:
        from annotate.models import grounding_dino  # noqa: F401
    except ImportError:
        pass

    # Resolve and load the detector
    try:
        # scene_class lives on the file entry once Phase 5 routing lands;
        # for now, pass it through if present.
        scene_class = (project.get("file", {}).get(str(fid)) or {}).get("scene_class")
        adapter = registry.acquire("detect", "detect",
                                   scene_class=scene_class, pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_suggest_regions", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load detector: {e}")

    # Decide prompts
    use_prompts = list(prompts or [])
    if not use_prompts and (broad_prompts or exclude_existing):
        use_prompts = list(DEFAULT_BROAD_PROMPTS)
    if not use_prompts:
        return _text("No prompts provided. Pass prompts=[...] or broad_prompts=True.")

    # Decide tiling
    mode = tiling
    tiles = None
    notes: list[str] = []
    if mode == "auto":
        mode = auto_grid(W, H)
    if mode == "adaptive":
        tiles = _adaptive_tiles(pil_image, registry, notes)
        if tiles is None:
            mode = auto_grid(W, H)
            notes.append(f"saliency unavailable; fell back to {mode}")
    if tiles is None:
        tiles = generate_tiles(W, H, mode)
    cols, rows = parse_grid(mode if mode != "auto" else "none")

    # Run detector per tile
    all_candidates: list[CandidateBox] = []
    for t in tiles:
        crop = pil_image.crop((t.x, t.y, t.x + t.w, t.y + t.h)) if mode != "none" else pil_image
        try:
            tile_detections = adapter.detect(crop, use_prompts, min_confidence=min_confidence)
        except Exception as e:
            return _text(f"Detector raised on tile ({t.col},{t.row}): {e}")
        for det in tile_detections:
            # det.xy is fraction-of-tile in [2, x, y, w, h]; remap to image fraction
            _, tx, ty, tw, th = det.xy
            x_frac = (t.x + tx * t.w) / W
            y_frac = (t.y + ty * t.h) / H
            w_frac = (tw * t.w) / W
            h_frac = (th * t.h) / H
            all_candidates.append(CandidateBox(
                x=x_frac, y=y_frac, w=w_frac, h=h_frac,
                label=det.label, score=det.score, tile=(t.col, t.row),
            ))

    merged = nms_merge(all_candidates, iou_threshold=0.5, per_label=True)

    if exclude_existing:
        existing = _existing_boxes_fraction(project, image_registry_map, fid)
        merged = filter_existing(merged, existing, iou_threshold=0.5)

    response = {
        "fid": fid,
        "image_dims": [W, H],
        "model": adapter.model_id,
        "tile_grid": mode,
        "tile_count": len(tiles),
        "prompts": use_prompts,
        "candidate_count": len(merged),
        "candidates": [
            {
                "xy": c.to_xy(),
                "label": c.label,
                "score": round(c.score, 4),
                "tile": list(c.tile) if c.tile else None,
            }
            for c in merged
        ],
        "notes": notes,
    }

    return _text(json.dumps(response, indent=2))


def _adaptive_tiles(image, registry, notes: list):
    """Try to build a saliency-driven tile set. Returns None on failure
    (caller falls back to auto-grid)."""
    from annotate.models import NotInstalledError
    try:
        from annotate.models import saliency as _saliency_mod  # noqa: F401
    except ImportError:
        return None
    try:
        adapter = registry.acquire("saliency", "saliency")
    except NotInstalledError as e:
        notes.append(f"saliency adapter unavailable: {e}")
        return None
    except Exception as e:
        notes.append(f"saliency load failed: {e}")
        return None
    try:
        sal_map = adapter.saliency(image)
        tiles = _saliency_mod.cluster_to_tiles(sal_map, max_tiles=8)
    except Exception as e:
        notes.append(f"saliency clustering failed: {e}")
        return None
    if not tiles:
        notes.append("saliency produced no clusters; using uniform grid")
        return None
    notes.append(f"adaptive tiling placed {len(tiles)} salience-focused tiles")
    return tiles


# ---------------------------------------------------------------------------
# Phase 3: via_tighten_region + via_grade_annotations
# ---------------------------------------------------------------------------

def handle_tighten_region(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    metadata_id: str,
    auto_apply: bool = False,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    from annotate.models import NotInstalledError

    if registry is None:
        return _ai_stub_response("via_tighten_region", None,
                                 reason="Model registry not initialised.")
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    region = project.get("metadata", {}).get(metadata_id)
    if region is None:
        return _text(f"metadata_id {metadata_id!r} not found.")

    vid = region.get("vid")
    fid = _resolve_fid_for_vid(project, vid) if vid else None
    if fid is None:
        return _text(f"Could not resolve fid for region {metadata_id!r}.")
    img, err_or_path = _open_image(project, image_registry_map, fid)
    if img is None:
        return _text(err_or_path)

    xy = region.get("xy") or []
    if not xy:
        return _text(f"Region {metadata_id!r} has no xy.")
    shape = xy[0]
    coords = xy[1:]
    bbox = _region_bbox_orig(int(shape) if isinstance(shape, (int, float)) else 0, coords)
    if bbox is None:
        return _text(f"Region {metadata_id!r} has unsupported shape {shape!r} for tightening.")
    x0, y0, x1, y1 = bbox

    # Eager-import segment adapters
    try:
        from annotate.models import sam2  # noqa: F401
    except ImportError:
        pass

    try:
        scene_class = (project.get("file", {}).get(str(fid)) or {}).get("scene_class")
        adapter = registry.acquire("segment", "segment",
                                   scene_class=scene_class, pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_tighten_region", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load segmenter: {e}")

    try:
        mask = adapter.segment(img, box=(x0, y0, x1, y1))
    except Exception as e:
        return _text(f"Segmenter raised: {e}")

    W, H = img.size
    # Tightened bbox in original-pixel space for round-tripping
    _, tx_f, ty_f, tw_f, th_f = mask.xy
    tightened_pixel = [2, int(tx_f * W), int(ty_f * H), int(tw_f * W), int(th_f * H)]

    applied = False
    if auto_apply:
        project["metadata"][metadata_id]["xy"] = tightened_pixel
        store.set_project(project)
        applied = True

    response = {
        "metadata_id": metadata_id,
        "model": adapter.model_id,
        "current_xy_pixel": list(xy),
        "tightened_xy_pixel": tightened_pixel,
        "tightened_xy_fraction": [round(v, 4) if isinstance(v, float) else v for v in mask.xy],
        "iou_with_input": round(mask.iou_with_input, 4) if mask.iou_with_input is not None else None,
        "mask_area_fraction": round(mask.area_fraction, 6) if mask.area_fraction is not None else None,
        "auto_applied": applied,
    }
    return _text(json.dumps(response, indent=2))


def handle_grade_annotations(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    fid: str,
    mids: list[str] | None = None,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    from annotate.models import NotInstalledError

    if registry is None:
        return _ai_stub_response("via_grade_annotations", None,
                                 reason="Model registry not initialised.")
    project = store.get()
    if project is None:
        return _text("No project loaded.")

    img, err_or_path = _open_image(project, image_registry_map, fid)
    if img is None:
        return _text(err_or_path)

    view_to_fids = {
        v: [str(x) for x in vd.get("fid_list", [])]
        for v, vd in project.get("view", {}).items()
    }
    candidates: list[tuple[str, dict]] = []
    for mid, m in project.get("metadata", {}).items():
        if mids is not None and mid not in mids:
            continue
        if str(fid) not in view_to_fids.get(m.get("vid", ""), []):
            continue
        candidates.append((mid, m))

    if not candidates:
        return _text(json.dumps({
            "fid": fid,
            "regions": [],
            "overall": {"flagged_count": 0, "graded_count": 0},
            "notes": ["no regions on this fid match the filter"],
        }, indent=2))

    # Eager-import grader adapters
    try:
        from annotate.models import clip_grader  # noqa: F401
    except ImportError:
        pass

    try:
        scene_class = (project.get("file", {}).get(str(fid)) or {}).get("scene_class")
        adapter = registry.acquire("grade", "grade",
                                   scene_class=scene_class, pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_grade_annotations", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load grader: {e}")

    region_results: list[dict] = []
    flagged = 0
    pos_sum = size_sum = match_sum = 0.0
    for mid, m in candidates:
        label = (m.get("av") or {}).get("1", "")
        if not label:
            region_results.append({
                "metadata_id": mid, "label": "", "skipped": "no label",
            })
            continue
        try:
            grade = adapter.grade(img, {**m, "_mid": mid}, label)
        except Exception as e:
            region_results.append({
                "metadata_id": mid, "label": label, "error": str(e),
            })
            continue
        is_flagged = (
            grade.position < 0.7 or grade.size < 0.7
            or grade.label_match < 0.5 or grade.shape_encoding_fit != "good"
        )
        if is_flagged:
            flagged += 1
        pos_sum += grade.position
        size_sum += grade.size
        match_sum += grade.label_match
        region_results.append({
            "metadata_id": mid,
            "label": label,
            "position": round(grade.position, 3),
            "size": round(grade.size, 3),
            "label_match": round(grade.label_match, 3),
            "shape_encoding_fit": grade.shape_encoding_fit,
            "issues": grade.issues,
            "flagged": is_flagged,
        })

    n = max(1, sum(1 for r in region_results if "error" not in r and "skipped" not in r))
    response = {
        "fid": fid,
        "model": adapter.model_id,
        "graded_count": n,
        "overall": {
            "mean_position": round(pos_sum / n, 3),
            "mean_size": round(size_sum / n, 3),
            "mean_label_match": round(match_sum / n, 3),
            "flagged_count": flagged,
        },
        "regions": region_results,
    }
    return _text(json.dumps(response, indent=2))


# ---------------------------------------------------------------------------
# Phase 4: via_verify_region
# ---------------------------------------------------------------------------

def handle_verify_region(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    metadata_id: str,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    from annotate.models import NotInstalledError

    if registry is None:
        return _ai_stub_response("via_verify_region", None,
                                 reason="Model registry not initialised.")
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    region = project.get("metadata", {}).get(metadata_id)
    if region is None:
        return _text(f"metadata_id {metadata_id!r} not found.")
    label = (region.get("av") or {}).get("1", "")
    if not label:
        return _text(f"Region {metadata_id!r} has no label — nothing to verify.")

    vid = region.get("vid")
    fid = _resolve_fid_for_vid(project, vid) if vid else None
    if fid is None:
        return _text(f"Could not resolve fid for region {metadata_id!r}.")
    img, err_or_path = _open_image(project, image_registry_map, fid)
    if img is None:
        return _text(err_or_path)

    xy = region.get("xy") or []
    head = xy[0] if xy else 0
    shape = int(head) if isinstance(head, (int, float)) and head == int(head) else 0
    bbox = _region_bbox_orig(shape, xy[1:])
    if bbox is None:
        return _text(f"Region {metadata_id!r} has unsupported shape for verification.")
    x0, y0, x1, y1 = bbox
    # Clamp + minimum padding around the crop so the VLM has context
    W, H = img.size
    pad_x = max(8, int((x1 - x0) * 0.1))
    pad_y = max(8, int((y1 - y0) * 0.1))
    cx0 = max(0, int(x0) - pad_x)
    cy0 = max(0, int(y0) - pad_y)
    cx1 = min(W, int(x1) + pad_x)
    cy1 = min(H, int(y1) + pad_y)
    crop = img.crop((cx0, cy0, cx1, cy1))

    try:
        from annotate.models import florence2  # noqa: F401
    except ImportError:
        pass

    try:
        scene_class = (project.get("file", {}).get(str(fid)) or {}).get("scene_class")
        adapter = registry.acquire("verify", "verify",
                                   scene_class=scene_class, pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_verify_region", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load verifier: {e}")

    try:
        verdict = adapter.verify(crop, label)
    except Exception as e:
        return _text(f"Verifier raised: {e}")

    response = {
        "metadata_id": metadata_id,
        "model": adapter.model_id,
        "label_claimed": verdict.label_claimed,
        "verdict": verdict.verdict,
        "confidence": round(verdict.confidence, 3),
        "supporting": verdict.supporting,
        "contradicting": verdict.contradicting,
        "suggested_label": verdict.suggested_label,
        "crop_window_pixel": [cx0, cy0, cx1 - cx0, cy1 - cy0],
    }
    return _text(json.dumps(response, indent=2))


# ---------------------------------------------------------------------------
# Phase 5: scene classification + automatic routing
# ---------------------------------------------------------------------------

def handle_classify_scene(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    fid: str,
    labels: list[str] | None = None,
    cache: bool = True,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    from annotate.models import NotInstalledError

    if registry is None:
        return _ai_stub_response("via_classify_scene", None,
                                 reason="Model registry not initialised.")
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    img, err_or_path = _open_image(project, image_registry_map, fid)
    if img is None:
        return _text(err_or_path)

    # Eager-import classifier adapters (CLIP serves classify + grade).
    try:
        from annotate.models import clip_grader  # noqa: F401
    except ImportError:
        pass

    cfg = registry.config.pipeline_for("classify", override=pipeline)
    if cfg is None:
        return _text("No classify pipeline configured.")
    candidate_labels = labels or cfg.extra.get("labels") or []
    if not candidate_labels:
        return _text("No candidate labels: pass labels=[...] or add a labels= list under [classify.default] in models.toml.")

    try:
        adapter = registry.acquire("classify", "classify", pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_classify_scene", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load classifier: {e}")

    try:
        scores = adapter.classify(img, candidate_labels)
    except Exception as e:
        return _text(f"Classifier raised: {e}")

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_score = ranked[0]

    cached = False
    if cache:
        # Persist on the file entry so subsequent detection/segment calls
        # can route via registry.acquire(scene_class=...).
        project["file"][str(fid)]["scene_class"] = top_label
        project["file"][str(fid)]["scene_class_confidence"] = round(top_score, 4)
        store.set_project(project)
        cached = True

    response = {
        "fid": fid,
        "model": adapter.model_id,
        "scene_class": top_label,
        "confidence": round(top_score, 4),
        "all_scores": {k: round(v, 4) for k, v in ranked},
        "cached_on_file_entry": cached,
        "routing_effect": _explain_routing(registry.config, top_label),
    }
    return _text(json.dumps(response, indent=2))


def _explain_routing(cfg, scene_class: str) -> dict:
    """Surface which pipelines this scene class would route to."""
    rules = cfg.routing.get(scene_class, {})
    out = {}
    for task in ("detect", "segment", "verify", "grade"):
        chosen = rules.get(task, "default")
        out[task] = f"{task}.{chosen}"
    return out


# ---------------------------------------------------------------------------
# Phase 6: ask_model + find_similar
# ---------------------------------------------------------------------------

def handle_ask_model(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    fid: str,
    question: str,
    region_bbox: list | None = None,
    xy_space: str = "fraction",
    metadata_id: str | None = None,
    max_new_tokens: int = 256,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    """Free-form Q&A about an image (or a region of one).

    Either pass ``region_bbox=[x, y, w, h]`` (fraction or original-pixel
    per ``xy_space``) or ``metadata_id=`` to use an existing region's
    bbox. Omit both to ask about the full image.
    """
    from annotate.models import NotInstalledError

    if registry is None:
        return _ai_stub_response("via_ask_model", None,
                                 reason="Model registry not initialised.")
    if not question or not question.strip():
        return _text("Pass a non-empty 'question'.")

    project = store.get()
    if project is None:
        return _text("No project loaded.")
    img, err_or_path = _open_image(project, image_registry_map, fid)
    if img is None:
        return _text(err_or_path)
    W, H = img.size

    crop_window = None
    if metadata_id is not None:
        region = project.get("metadata", {}).get(metadata_id)
        if region is None:
            return _text(f"metadata_id {metadata_id!r} not found.")
        xy = region.get("xy") or []
        head = xy[0] if xy else 0
        shape = int(head) if isinstance(head, (int, float)) and head == int(head) else 0
        bbox = _region_bbox_orig(shape, xy[1:])
        if bbox is None:
            return _text(f"Region {metadata_id!r} has unsupported shape for crop.")
        crop_window = bbox
    elif region_bbox is not None:
        if len(region_bbox) != 4:
            return _text("region_bbox must be [x, y, w, h]")
        x, y, w, h = region_bbox
        if xy_space == "fraction":
            x, w = x * W, w * W
            y, h = y * H, h * H
        elif xy_space != "original":
            return _text(f"xy_space must be 'fraction' or 'original', got {xy_space!r}")
        crop_window = (int(x), int(y), int(x + w), int(y + h))

    if crop_window is not None:
        x0, y0, x1, y1 = crop_window
        pad_x = max(8, int((x1 - x0) * 0.1))
        pad_y = max(8, int((y1 - y0) * 0.1))
        cx0 = max(0, x0 - pad_x); cy0 = max(0, y0 - pad_y)
        cx1 = min(W, x1 + pad_x); cy1 = min(H, y1 + pad_y)
        target = img.crop((cx0, cy0, cx1, cy1))
        used_window_pixel = [cx0, cy0, cx1 - cx0, cy1 - cy0]
    else:
        target = img
        used_window_pixel = [0, 0, W, H]

    try:
        from annotate.models import chat_vlm  # noqa: F401
    except ImportError:
        pass

    try:
        scene_class = (project.get("file", {}).get(str(fid)) or {}).get("scene_class")
        adapter = registry.acquire("ask", "ask",
                                   scene_class=scene_class, pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_ask_model", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load chat VLM: {e}")

    try:
        answer = adapter.ask(target, question, max_new_tokens=max_new_tokens)
    except Exception as e:
        return _text(f"Chat VLM raised: {e}")

    response = {
        "fid": fid,
        "model": adapter.model_id,
        "question": answer.question,
        "answer": answer.text,
        "finish_reason": answer.finish_reason,
        "tokens_generated": answer.tokens_generated,
        "used_window_pixel": used_window_pixel,
    }
    if metadata_id:
        response["metadata_id"] = metadata_id
    return _text(json.dumps(response, indent=2))


def handle_find_similar(
    store: ProjectStore,
    image_registry_map: dict,
    registry,
    *,
    metadata_id: str,
    target_fids: list[str] | None = None,
    min_confidence: float = 0.3,
    pipeline: str | None = None,
) -> list[types.TextContent]:
    """Use a labelled region as a visual prompt; return similar regions
    from each target image. If ``target_fids`` is omitted, searches the
    reference's own image."""
    from annotate.models import NotInstalledError

    if registry is None:
        return _ai_stub_response("via_find_similar", None,
                                 reason="Model registry not initialised.")
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    ref_region = project.get("metadata", {}).get(metadata_id)
    if ref_region is None:
        return _text(f"metadata_id {metadata_id!r} not found.")
    ref_vid = ref_region.get("vid")
    ref_fid = _resolve_fid_for_vid(project, ref_vid) if ref_vid else None
    if ref_fid is None:
        return _text(f"Could not resolve fid for reference region {metadata_id!r}.")
    ref_img, err = _open_image(project, image_registry_map, ref_fid)
    if ref_img is None:
        return _text(err)

    xy = ref_region.get("xy") or []
    head = xy[0] if xy else 0
    shape = int(head) if isinstance(head, (int, float)) and head == int(head) else 0
    ref_bbox = _region_bbox_orig(shape, xy[1:])
    if ref_bbox is None:
        return _text(f"Reference region {metadata_id!r} has unsupported shape.")

    targets = target_fids if target_fids else [ref_fid]
    # Resolve all target images up front
    target_images = {}
    for tfid in targets:
        timg, terr = _open_image(project, image_registry_map, tfid)
        if timg is None:
            return _text(f"fid={tfid}: {terr}")
        target_images[tfid] = timg

    try:
        from annotate.models import yoloe  # noqa: F401
    except ImportError:
        pass

    try:
        adapter = registry.acquire("find_similar", "find_similar", pipeline=pipeline)
    except NotInstalledError as e:
        return _ai_stub_response("via_find_similar", registry, reason=str(e))
    except Exception as e:
        return _text(f"Failed to load find_similar adapter: {e}")

    ref_label = (ref_region.get("av") or {}).get("1", "")
    all_results = []
    for tfid, timg in target_images.items():
        try:
            detections = adapter.find_similar(
                timg, ref_img, ref_bbox, min_confidence=min_confidence,
            )
        except Exception as e:
            return _text(f"find_similar raised on fid={tfid}: {e}")
        all_results.append({
            "fid": tfid,
            "candidate_count": len(detections),
            "candidates": [
                {"xy": d.xy, "score": round(d.score, 4), "label": ref_label or d.label}
                for d in detections
            ],
        })

    response = {
        "reference_mid": metadata_id,
        "reference_label": ref_label,
        "reference_fid": ref_fid,
        "model": adapter.model_id,
        "results": all_results,
    }
    return _text(json.dumps(response, indent=2))


# ---------------------------------------------------------------------------
# main() and async MCP runner
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="annotate: MCP server + VIA image annotator on localhost"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("VIA_MCP_PORT", "0")),
        help="HTTP port (default: 0 = OS-assigned, env: VIA_MCP_PORT)",
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get("VIA_MCP_STATE_FILE"),
        help="Path to project state JSON (env: VIA_MCP_STATE_FILE)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Open browser on startup",
    )
    parser.add_argument(
        "--models-config",
        default=os.environ.get("ANNOTATE_MODELS_CONFIG"),
        help="Path to models.toml (env: ANNOTATE_MODELS_CONFIG; "
             "default: ~/.config/annotate/models.toml, auto-generated on first run)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip building the model registry (AI tools return install hint)",
    )
    args = parser.parse_args()

    if args.state_file:
        state_file = Path(args.state_file)
    else:
        state_file = Path(
            platformdirs.user_data_dir("annotate", appauthor=False)
        ) / "project_state.json"

    html_template = (
        files("annotate").joinpath("via_image_annotator.html").read_text(encoding="utf-8")
    )

    store = ProjectStore(state_file=state_file)

    from annotate.http_handler import make_handler
    # Bind with port 0 so the OS assigns a free port, then patch html_content
    # with the actual port before the first request is served.
    # Rebuild image registry from persisted project (survives server restart)
    image_registry: dict = {}
    existing = store.get()
    if existing:
        for entry in existing.get("file", {}).values():
            if entry.get("loc") == 2 and entry.get("abs_path") and entry.get("fname"):
                image_registry[entry["fname"]] = entry["abs_path"]

    handler_cls = make_handler(store, b"", image_registry)
    try:
        httpd = HTTPServer(("127.0.0.1", args.port), handler_cls)
    except OSError as e:
        print(f"annotate: cannot bind port {args.port}: {e}", file=sys.stderr)
        sys.exit(1)
    actual_port = httpd.server_address[1]

    # Heal stale localhost src URLs from prior sessions whose port is now wrong.
    # abs_path is preserved per file entry, so we just rewrite src to the new port.
    if existing:
        import re as _re
        changed = False
        for entry in existing.get("file", {}).values():
            src = entry.get("src", "") or ""
            fname = entry.get("fname", "")
            if (
                entry.get("loc") == 2
                and fname
                and _re.match(r"^http://localhost:\d+/img/", src)
            ):
                expected = f"http://localhost:{actual_port}/img/{fname}"
                if src != expected:
                    entry["src"] = expected
                    changed = True
        if changed:
            store.set_project(existing)
            print(
                f"annotate: healed stale src URLs to port {actual_port}",
                file=sys.stderr,
            )
    auto_pull_script = f"""<script>
/* annotate: auto-load server project when VIA opens empty */
(async function() {{
  try {{
    if (Object.keys(via.d.store.file).length > 0) return;
    const r = await fetch('http://localhost:{actual_port}/api/project');
    const d = await r.json();
    if (d.pid) via.s.pull(d.pid);
  }} catch(e) {{}}
}})();
</script>"""
    patched = html_template.replace("__VIA_MCP_PORT__", str(actual_port))
    html_content = patched.replace("</body>", auto_pull_script + "\n</body>").encode("utf-8")
    handler_cls.html_content = html_content

    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    annotator_url = f"http://localhost:{actual_port}/"
    print(f"VIA annotator: {annotator_url}", file=sys.stderr)

    if args.browser:
        webbrowser.open(annotator_url)

    registry = None
    if not args.no_ai:
        try:
            from annotate.models import ModelRegistry, load_config
            registry = ModelRegistry(load_config(args.models_config))
            print(
                f"annotate: model registry loaded "
                f"({len(registry.config.pipelines)} pipelines from "
                f"{registry.config.source_path or 'builtin defaults'})",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"annotate: model registry init failed ({e}); AI tools disabled", file=sys.stderr)
            registry = None

    asyncio.run(_run_mcp(store, annotator_url, actual_port, image_registry, registry))


async def _run_mcp(store: ProjectStore, annotator_url: str, port: int, image_registry: dict, registry=None) -> None:
    mcp_server = mcp.server.Server("annotate")

    @mcp_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="via_get_annotator_url",
                description="Return the URL of the live VIA annotator. Share with the user so they can open it.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_add_file",
                description=(
                    "Load a local image file into the VIA project so it appears in the annotator. "
                    "Creates a file + view entry and serves the image via HTTP. "
                    "If no project is loaded yet, creates a new empty one. "
                    "Returns {fid, vid, url}."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to the image file"},
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="via_get_project",
                description="Return the full VIA project JSON (files, views, metadata, attributes).",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_list_files",
                description="List all image files in the project (id, name, type). Lightweight — use before via_get_project.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_get_annotations",
                description=(
                    "Return all annotation metadata, optionally filtered by view ID. "
                    "format='pixel' (default) returns coords in original pixel space; "
                    "'fraction' returns coords as 0–1 fractions of the original dims "
                    "(useful for diffing your placements against user corrections "
                    "without manual arithmetic); 'both' returns pixels plus an extra "
                    "xy_fraction field per region."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vid": {"type": "string", "description": "View ID to filter by (omit for all)"},
                        "format": {
                            "type": "string",
                            "enum": ["pixel", "fraction", "both"],
                            "default": "pixel",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="via_add_region",
                description=(
                    "Add one annotation region. xy encoding: rectangle=[2,x,y,w,h], "
                    "point=[1,x,y], circle=[3,cx,cy,r], polygon=[7,x1,y1,x2,y2,...]. "
                    "Coordinates default to original pixel space; pass xy_space='fraction' "
                    "to use 0.0–1.0 values that the server scales by the original image dims. "
                    "av keys may be attribute IDs ('1','2',...) or anames ('label','description'); "
                    "unknown keys are rejected (use via_get_project to see the schema)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vid": {"type": "string", "description": "View ID"},
                        "z": {"type": "array", "items": {"type": "number"}, "description": "Temporal coords ([] for images)"},
                        "xy": {"type": "array", "items": {"type": "number"}, "description": "[shape_id, ...coords]"},
                        "av": {"type": "object", "description": "Attribute key-value pairs (strings)", "additionalProperties": {"type": "string"}},
                        "xy_space": {
                            "type": "string",
                            "enum": ["original", "fraction"],
                            "description": "'original' = pixel coords (default); 'fraction' = 0.0–1.0 of original dims",
                            "default": "original",
                        },
                    },
                    "required": ["vid", "z", "xy", "av"],
                },
            ),
            types.Tool(
                name="via_update_region",
                description=(
                    "Replace an existing annotation region's z, xy, and av fields. "
                    "Same xy_space and av-key handling as via_add_region."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {"type": "string"},
                        "z": {"type": "array", "items": {"type": "number"}},
                        "xy": {"type": "array", "items": {"type": "number"}},
                        "av": {"type": "object", "additionalProperties": {"type": "string"}},
                        "xy_space": {
                            "type": "string",
                            "enum": ["original", "fraction"],
                            "default": "original",
                        },
                    },
                    "required": ["metadata_id", "z", "xy", "av"],
                },
            ),
            types.Tool(
                name="via_delete_region",
                description="Remove an annotation region by its metadata ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {"type": "string"},
                    },
                    "required": ["metadata_id"],
                },
            ),
            types.Tool(
                name="via_update_project",
                description="Replace the full project JSON. Use for bulk changes. project_json must be a valid VIA project JSON string.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_json": {"type": "string"},
                    },
                    "required": ["project_json"],
                },
            ),
            types.Tool(
                name="via_get_image",
                description=(
                    "Return the image so you can see what you are annotating. "
                    "Always call before placing coordinates. Returns original pixel dims "
                    "(use these for all coordinates) plus the image, downscaled to max_dim "
                    "on the longest edge for context efficiency. Pass overlay=true to draw "
                    "existing regions on the returned image — use this to verify placements."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string", "description": "File ID from via_list_files or via_add_file"},
                        "max_dim": {"type": "integer", "description": "Limit longest edge to this many pixels (default 2048; drop to 1024 for context savings on large images with no small features)", "default": 2048},
                        "overlay": {"type": "boolean", "description": "Draw existing regions onto the returned image (default false)", "default": False},
                    },
                    "required": ["fid"],
                },
            ),
            types.Tool(
                name="via_get_image_crop",
                description=(
                    "Return a high-resolution crop of the original image. Use when you "
                    "need to verify a small feature or a suspected mis-placement and the "
                    "standard via_get_image returned the area too downscaled to judge. "
                    "bbox is [x, y, w, h]; xy_space='fraction' (0.0–1.0 of original dims) "
                    "is usually easiest after eyeballing the area on a prior via_get_image. "
                    "The crop is taken from the full-res original, then downscaled only "
                    "if it still exceeds max_dim. Pass overlay=true to draw existing "
                    "regions on the crop."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string"},
                        "bbox": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": "[x, y, w, h] in the chosen xy_space",
                        },
                        "xy_space": {
                            "type": "string",
                            "enum": ["original", "fraction"],
                            "default": "original",
                        },
                        "max_dim": {"type": "integer", "default": 2048},
                        "overlay": {"type": "boolean", "default": False},
                    },
                    "required": ["fid", "bbox"],
                },
            ),
            types.Tool(
                name="via_save_project",
                description="Write the current project JSON to a file on disk. Use when the user asks to save or export their work.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to write, e.g. /home/user/project.json"},
                    },
                    "required": ["path"],
                },
            ),
            # --- Local-model assistance (Phase 1 stubs; adapters land in 2-4) ---
            types.Tool(
                name="via_model_status",
                description=(
                    "Report the local-model assistance state: whether the [ai] "
                    "extra is installed, which model pipelines are configured, "
                    "which are currently loaded in memory, and the registered "
                    "adapter prefixes."
                ),
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_suggest_regions",
                description=(
                    "[Phase 2] Open-vocabulary detection: given text prompts, "
                    "return candidate regions with confidences. With "
                    "exclude_existing=True, subtracts anything already covered "
                    "by current annotations (find-missing mode). Tiling modes: "
                    "none | 2x2 | 4x4 | 8x8 | auto | adaptive. Returns "
                    "candidates only — nothing is written to the project."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string"},
                        "prompts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Text prompts; omit/empty + broad_prompts=True uses default broad set",
                        },
                        "tiling": {
                            "type": "string",
                            "enum": ["none", "2x2", "4x4", "8x8", "16x16", "auto", "adaptive"],
                            "default": "auto",
                        },
                        "exclude_existing": {"type": "boolean", "default": False},
                        "broad_prompts": {"type": "boolean", "default": False},
                        "min_confidence": {"type": "number", "default": 0.3},
                        "pipeline": {"type": "string", "description": "Override the default pipeline"},
                    },
                    "required": ["fid"],
                },
            ),
            types.Tool(
                name="via_tighten_region",
                description=(
                    "[Phase 3] Use SAM-style promptable segmentation to tighten "
                    "an existing region's box to the actual object outline. "
                    "Returns the original box, the tightened box, IoU between "
                    "them, and mask area. Does not write unless auto_apply=True."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {"type": "string"},
                        "auto_apply": {"type": "boolean", "default": False},
                        "pipeline": {"type": "string"},
                    },
                    "required": ["metadata_id"],
                },
            ),
            types.Tool(
                name="via_verify_region",
                description=(
                    "[Phase 4] Crop-and-verify with a VLM. Asks 'is this a "
                    "{label}? what supports / contradicts that?' and returns a "
                    "structured verdict with confidence, supporting features, "
                    "contradicting features, and a suggested re-label if the "
                    "model disagrees."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {"type": "string"},
                        "pipeline": {"type": "string"},
                    },
                    "required": ["metadata_id"],
                },
            ),
            types.Tool(
                name="via_classify_scene",
                description=(
                    "[Phase 5] Run zero-shot scene classification on the image. "
                    "Returns the top scene class (e.g. dense_crowd, painting, "
                    "aerial_or_satellite) plus all candidate scores. With "
                    "cache=True (default), the result is persisted on the file "
                    "entry as scene_class, so subsequent detection / segment / "
                    "grade / verify calls automatically route to the pipeline "
                    "mapped under [routing] in models.toml."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string"},
                        "labels": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Override the default label set from models.toml",
                        },
                        "cache": {"type": "boolean", "default": True},
                        "pipeline": {"type": "string"},
                    },
                    "required": ["fid"],
                },
            ),
            types.Tool(
                name="via_ask_model",
                description=(
                    "[Phase 6] Ask a local chat VLM a free-form question "
                    "about an image (or a specific region). Use for things "
                    "structured tools don't cover: 'how many people are "
                    "wearing red?', 'what's the make of this car?', 'read "
                    "the text on this label', 'describe the lighting'. "
                    "Pass region_bbox=[x,y,w,h] (defaults to fraction "
                    "space) OR metadata_id=<mid> to crop to a region; "
                    "omit both to ask about the full image. Default model: "
                    "Qwen2.5-VL-3B (Apache 2.0)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string"},
                        "question": {"type": "string"},
                        "region_bbox": {
                            "type": "array", "items": {"type": "number"},
                            "minItems": 4, "maxItems": 4,
                            "description": "[x, y, w, h] in xy_space coords",
                        },
                        "xy_space": {
                            "type": "string", "enum": ["fraction", "original"],
                            "default": "fraction",
                        },
                        "metadata_id": {
                            "type": "string",
                            "description": "Use an existing region's bbox as the crop",
                        },
                        "max_new_tokens": {"type": "integer", "default": 256},
                        "pipeline": {"type": "string"},
                    },
                    "required": ["fid", "question"],
                },
            ),
            types.Tool(
                name="via_find_similar",
                description=(
                    "[Phase 6] Use one annotated region as a visual prompt "
                    "and find similar regions in the same image or across "
                    "other images in the project. The killer feature for "
                    "dense scenes: annotate one ice skater, find all of "
                    "them. Requires the YOLOE pipeline (AGPL-3.0) to be "
                    "configured under [find_similar.default] in "
                    "models.toml — see the design doc for the license "
                    "note."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {
                            "type": "string",
                            "description": "Reference region whose visual content drives the search",
                        },
                        "target_fids": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Files to search (omit = just the reference's own image)",
                        },
                        "min_confidence": {"type": "number", "default": 0.3},
                        "pipeline": {"type": "string"},
                    },
                    "required": ["metadata_id"],
                },
            ),
            types.Tool(
                name="via_grade_annotations",
                description=(
                    "[Phase 3] Score every region (or a specified subset) on "
                    "position accuracy, size/extent, label-content match, and "
                    "shape-encoding choice. Returns per-region scores plus "
                    "issue notes; flagged regions are surfaced for review. "
                    "Does not modify the project."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string"},
                        "mids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Region IDs to grade (omit for all on the file)",
                        },
                        "pipeline": {"type": "string"},
                    },
                    "required": ["fid"],
                },
            ),
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        arguments = arguments or {}
        try:
            if name == "via_add_file":
                return handle_add_file(store, image_registry, port, arguments["path"])
            if name == "via_get_annotator_url":
                return _text(annotator_url)
            if name == "via_get_project":
                return handle_get_project(store)
            if name == "via_list_files":
                return handle_list_files(store)
            if name == "via_get_annotations":
                return handle_get_annotations(
                    store, image_registry,
                    vid=arguments.get("vid"),
                    format=arguments.get("format", "pixel"),
                )
            if name == "via_add_region":
                return handle_add_region(
                    store, image_registry,
                    vid=arguments["vid"],
                    z=arguments["z"],
                    xy=arguments["xy"],
                    av=arguments["av"],
                    xy_space=arguments.get("xy_space", "original"),
                )
            if name == "via_update_region":
                return handle_update_region(
                    store, image_registry,
                    metadata_id=arguments["metadata_id"],
                    z=arguments["z"],
                    xy=arguments["xy"],
                    av=arguments["av"],
                    xy_space=arguments.get("xy_space", "original"),
                )
            if name == "via_delete_region":
                return handle_delete_region(store, metadata_id=arguments["metadata_id"])
            if name == "via_update_project":
                return handle_update_project(store, project_json=arguments["project_json"])
            if name == "via_get_image":
                return handle_get_image(
                    store, image_registry,
                    fid=arguments["fid"],
                    max_dim=int(arguments.get("max_dim", 2048)),
                    overlay=bool(arguments.get("overlay", False)),
                )
            if name == "via_get_image_crop":
                return handle_get_image_crop(
                    store, image_registry,
                    fid=arguments["fid"],
                    bbox=arguments["bbox"],
                    xy_space=arguments.get("xy_space", "original"),
                    max_dim=int(arguments.get("max_dim", 2048)),
                    overlay=bool(arguments.get("overlay", False)),
                )
            if name == "via_save_project":
                return handle_save_project(store, path=arguments["path"])
            if name == "via_model_status":
                return handle_model_status(registry)
            if name == "via_suggest_regions":
                return handle_suggest_regions(
                    store, image_registry, registry,
                    fid=arguments["fid"],
                    prompts=arguments.get("prompts"),
                    tiling=arguments.get("tiling", "auto"),
                    exclude_existing=bool(arguments.get("exclude_existing", False)),
                    broad_prompts=bool(arguments.get("broad_prompts", False)),
                    min_confidence=float(arguments.get("min_confidence", 0.3)),
                    pipeline=arguments.get("pipeline"),
                )
            if name == "via_tighten_region":
                return handle_tighten_region(
                    store, image_registry, registry,
                    metadata_id=arguments["metadata_id"],
                    auto_apply=bool(arguments.get("auto_apply", False)),
                    pipeline=arguments.get("pipeline"),
                )
            if name == "via_grade_annotations":
                return handle_grade_annotations(
                    store, image_registry, registry,
                    fid=arguments["fid"],
                    mids=arguments.get("mids"),
                    pipeline=arguments.get("pipeline"),
                )
            if name == "via_verify_region":
                return handle_verify_region(
                    store, image_registry, registry,
                    metadata_id=arguments["metadata_id"],
                    pipeline=arguments.get("pipeline"),
                )
            if name == "via_classify_scene":
                return handle_classify_scene(
                    store, image_registry, registry,
                    fid=arguments["fid"],
                    labels=arguments.get("labels"),
                    cache=bool(arguments.get("cache", True)),
                    pipeline=arguments.get("pipeline"),
                )
            if name == "via_ask_model":
                return handle_ask_model(
                    store, image_registry, registry,
                    fid=arguments["fid"],
                    question=arguments["question"],
                    region_bbox=arguments.get("region_bbox"),
                    xy_space=arguments.get("xy_space", "fraction"),
                    metadata_id=arguments.get("metadata_id"),
                    max_new_tokens=int(arguments.get("max_new_tokens", 256)),
                    pipeline=arguments.get("pipeline"),
                )
            if name == "via_find_similar":
                return handle_find_similar(
                    store, image_registry, registry,
                    metadata_id=arguments["metadata_id"],
                    target_fids=arguments.get("target_fids"),
                    min_confidence=float(arguments.get("min_confidence", 0.3)),
                    pipeline=arguments.get("pipeline"),
                )
            return _text(f"Unknown tool: {name}")
        except KeyError as e:
            return _text(f"Missing required argument: {e}")

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
