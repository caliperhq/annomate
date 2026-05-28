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

1. Call `via_get_annotations()` — always read current state first, don't assume
2. Discuss, analyze, or propose changes
3. Write via the appropriate tool
4. Tell the user: "your browser will update in a few seconds"

**Do not write annotations without reading first.** State may have changed since
the last tool call.

## Tool Quick Reference

| Situation | Tool |
|-----------|------|
| Get annotator URL | `via_get_annotator_url` |
| Load an image into the project | `via_add_file` |
| Orient to project | `via_get_project` |
| Quick file list | `via_list_files` |
| Read annotations | `via_get_annotations` |
| Add one region | `via_add_region` |
| Fix an annotation | `via_update_region` |
| Remove an annotation | `via_delete_region` |
| Bulk / wholesale changes | `via_update_project` |

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

If no project is loaded yet, `via_add_file` creates a minimal empty one automatically.

## File Entry Reference (for via_update_project)

When hand-editing project JSON, each file entry looks like:

```json
{"fid": 1, "fname": "cat.jpg", "type": 2, "loc": 2, "src": "http://localhost:PORT/img/cat.jpg"}
```

**`type`** — media type:
- `2` = IMAGE  ← almost always this

**`loc`** — where the file lives:
- `1` = LOCAL — file loaded from disk via the browser's file picker (no `src` used, browser holds a `FileRef`)
- `2` = URIHTTP — `src` is an HTTP URL; use this when via_add_file serves the image
- `3` = URIFILE — `src` is used verbatim as `img.src` in the browser (can be `file:///...` or a relative HTTP path)
- `4` = INLINE — `src` is a data URI

**Always pair `loc=2` with an `http://localhost:PORT/img/<name>` `src`** when adding
files programmatically — it's the only approach that works without browser file access.

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
| Polygon | `[7, x1, y1, x2, y2, ...]` |

Coordinates are **image pixel space**, not browser canvas space.

Temporal annotations use the `z` field: `[t0, t1]` for a segment, `[t]` for a
single frame. For image annotations, `z` is always `[]`.

## Attribute Values

- `av` is always `{attribute_id: value}` where both key and value are strings
- Empty annotations have `av: {}`
- Metadata IDs are server-generated — never construct them; let `via_add_region` do it

## Common Patterns

**"Annotate this image" / "Load img002.jpg"**
→ `via_add_file("/absolute/path/to/img002.jpg")`, then share the URL with the user

**"What's in this project?"**
→ `via_list_files` then `via_get_annotations`

**"Add a bounding box for X at coordinates..."**
→ `via_add_region` with `xy: [2, x, y, w, h]`

**"Review my annotations for consistency"**
→ `via_get_annotations`, then reason over the JSON

**"Re-annotate everything based on..."**
→ `via_update_project` with the full modified project JSON

**"Open the annotator" / "Where do I go?"**
→ `via_get_annotator_url`, share the URL with the user
