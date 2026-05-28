---
name: via-annotator
description: Use when the user wants to annotate images in VIA, discuss or analyze VIA annotations, asks Claude to add/review/analyze bounding boxes or regions, or mentions opening the VIA annotator. Also trigger when the user shares a VIA project JSON file.
---

# via-annotator

## Overview

You have access to a live VIA v3 image annotator running on localhost (port is
OS-assigned — call `via_get_annotator_url` to get the current URL).
The user annotates in their browser; you read and write annotations via MCP tools.
The browser auto-updates within ~3 seconds of any change you make — you don't need to
tell the user to click anything.

## The Loop

Before any analysis or write:

1. Call `via_get_image(fid)` — **you must see the image to place accurate coordinates**
2. Call `via_get_annotations()` — always read current state first, don't assume
3. Discuss, analyze, or propose changes
4. Write via the appropriate tool
5. Tell the user: "your browser will update in a few seconds"

**Do not write annotations without reading first.** State may have changed since
the last tool call.

## Tool Quick Reference

| Situation | Tool |
|-----------|------|
| Get annotator URL | `via_get_annotator_url` |
| Load an image into the project | `via_add_file` |
| **See the image you're annotating** | `via_get_image` |
| Orient to project | `via_get_project` |
| Quick file list | `via_list_files` |
| Read annotations | `via_get_annotations` |
| Add one region | `via_add_region` |
| Fix an annotation | `via_update_region` |
| Remove an annotation | `via_delete_region` |
| Bulk / wholesale changes | `via_update_project` |
| Save project JSON to disk | `via_save_project` |

Prefer `via_get_annotations` over `via_get_project` for reads — it returns only
metadata, not the full project blob.

## Loading Images

**Always use `via_add_file` to load images** — never hand-roll file entries in
`via_update_project`. `via_add_file` takes an absolute path, registers the image
with the HTTP server, and returns `{fid, vid, url}`. The browser can then display
the image over HTTP with no file:// permission issues.

```
via_add_file("/home/mike/photos/cat.jpg")
→ {"fid": "1", "vid": "1", "url": "http://localhost:PORT/img/cat.jpg"}
```

**Resolve relative paths against your working directory before calling.**
If the user says `../img002.jpg` and your cwd is `/home/mike/work/`, the absolute
path is `/home/mike/img002.jpg` — don't guess, use `realpath` or `find` if uncertain.

If no project is loaded yet, `via_add_file` creates a minimal empty one automatically,
with a default `label` + `description` attribute schema already installed.

## Viewing Images

**Always call `via_get_image(fid)` before placing coordinates.** The tool returns
the image (downscaled for context efficiency, default longest edge = 1024 px) plus
the **original pixel dimensions** — all coordinates you provide must be in original
pixel space regardless of the returned image size.

```
via_get_image("1")
→ "Image fid=1 fname=cat.jpg original=1798×1211px returned=846×570px.
   Coordinates must be in original pixel space."
+ <image>
```

Without calling this you are estimating coordinates blind, which produces bad annotations.

## File Entry Reference (for via_update_project)

When hand-editing project JSON, each file entry looks like:

```json
{"fid": 1, "fname": "cat.jpg", "type": 2, "loc": 2, "src": "http://localhost:PORT/img/cat.jpg"}
```

**`type`** — media type:
- `2` = IMAGE  ← almost always this

**`loc`** — where the file lives:
- `1` = LOCAL — file loaded from disk via the browser's file picker (no `src` used)
- `2` = URIHTTP — `src` is an HTTP URL; use this when via_add_file serves the image
- `3` = URIFILE — `src` is used verbatim as `img.src`
- `4` = INLINE — `src` is a data URI

**Always pair `loc=2` with an `http://localhost:PORT/img/<name>` `src`** when adding
files programmatically.

Each file entry must have a matching **view entry**:
```json
"view": {"1": {"fid_list": [1]}}
```

## Region Shape Encoding

The `xy` field is an array where the first element is the shape ID:

| Shape | xy encoding |
|-------|-------------|
| Point | `[1, x, y]` |
| Rectangle | `[2, x, y, width, height]` |
| Circle | `[3, cx, cy, r]` |
| Ellipse | `[4, cx, cy, rx, ry]` |
| Polyline | `[6, x1, y1, x2, y2, ...]` |
| Polygon | `[7, x1, y1, x2, y2, ...]` |

Coordinates are **image pixel space**, not browser canvas space.

**Note on user-drawn regions:** Browser-drawn regions occasionally arrive without
a shape-ID prefix if the browser saved an in-progress draw state — the first element
will be a large float rather than an integer 1–7. Treat these as polylines (shape 6).
Server-side fix is pending; for now filter them by checking `xy[0] <= 7`.

Temporal annotations use the `z` field: `[t0, t1]` for a segment, `[t]` for a
single frame. For image annotations, `z` is always `[]`.

## Attribute Schema

Every new project starts with two TEXT attributes pre-installed:

```json
"attribute": {
  "1": {"aname": "label",       "anchor_id": "FILE1_Z0_XY1", "type": 1, ...},
  "2": {"aname": "description", "anchor_id": "FILE1_Z0_XY1", "type": 1, ...}
}
```

`config.ui.spatial_region_label_attribute_id = "1"` makes VIA render the `label`
value on the canvas next to each region.

**`av` key conventions:** keys are attribute IDs as strings (`"1"`, `"2"`), values
are always strings. Empty annotation: `av: {}`.

### Attribute object shape (for via_update_project bulk edits)

```json
{
  "aname": "label",
  "anchor_id": "FILE1_Z0_XY1",
  "type": 1,
  "desc": "Short description shown in UI",
  "options": {},
  "default_option_id": ""
}
```

**`anchor_id` values:**
- `"FILE1_Z0_XY1"` — spatial region attribute (bounding boxes, polygons, etc.)
- `"FILE1_Z0_XY0"` — file-level attribute (applies to the whole image)

**`type` enum:**
- `1` = TEXT
- `2` = CHECKBOX
- `3` = RADIO
- `4` = SELECT
- `5` = IMAGE

For SELECT/RADIO/CHECKBOX, populate `options` as `{"0": "Cat", "1": "Dog", ...}`.

### Making labels visible on the canvas

`config.ui.spatial_region_label_attribute_id` must be set to the attribute ID
(as a string) whose value you want rendered on each region. It defaults to `"1"`
(the `label` attribute). If labels stop appearing, the browser UI may have reset
it — re-assert it with a `via_update_project` call that sets it back to `"1"`.

## Region ID Conventions

- **Server-generated IDs** (from `via_add_region`): 8-char alphanumeric + `-_`,
  e.g. `8zFsb80J`. Never construct these; let the tool generate them.
- **Browser-drawn IDs**: `<vid>_<8char>`, e.g. `1_dOFsKKoJ`. The `<vid>_` prefix
  is added by VIA when the user draws directly in the browser. Both formats are
  valid and interchangeable for update/delete operations.

## Saving Work

```
via_save_project("/home/mike/project/annotations.json")
→ "Saved to /home/mike/project/annotations.json"
```

The server also auto-persists state across restarts (at the platform data dir).
`via_save_project` is for explicit user-requested saves or handoff exports.

## Geometry Helpers (manual recipes until tool helpers are added)

### Polyline → polygon band

To turn a polyline into a filled band polygon (e.g. road, track, river):

```python
import math

def polyline_to_band(points, half_width):
    """points = [(x,y), ...], returns flat polygon xy list [7, x1,y1, ...]"""
    n = len(points)
    perps = []
    for i in range(n):
        if i == 0:
            dx, dy = points[1][0]-points[0][0], points[1][1]-points[0][1]
        elif i == n-1:
            dx, dy = points[-1][0]-points[-2][0], points[-1][1]-points[-2][1]
        else:
            dx = points[i+1][0]-points[i-1][0]
            dy = points[i+1][1]-points[i-1][1]
        length = math.hypot(dx, dy) or 1
        perps.append((-dy/length, dx/length))
    left  = [(p[0] + half_width*n[0], p[1] + half_width*n[1]) for p, n in zip(points, perps)]
    right = [(p[0] - half_width*n[0], p[1] - half_width*n[1]) for p, n in zip(points, perps)]
    poly = left + list(reversed(right))
    return [7] + [c for pt in poly for c in pt]
```

## Common Patterns

**"Annotate this image" / "Load img002.jpg"**
→ `via_add_file("/absolute/path/to/img002.jpg")`, then `via_get_image(fid)`, then share the URL

**"What's in this project?"**
→ `via_list_files` then `via_get_annotations`

**"Add a bounding box for X at coordinates..."**
→ `via_get_image(fid)` first, then `via_add_region` with `xy: [2, x, y, w, h]`

**"Review my annotations for consistency"**
→ `via_get_image(fid)` + `via_get_annotations`, then reason over both

**"Re-annotate everything based on..."**
→ `via_update_project` with the full modified project JSON

**"Open the annotator" / "Where do I go?"**
→ `via_get_annotator_url`, share the URL with the user

**"Save this work"**
→ `via_save_project("/path/to/output.json")`
