---
name: annotate
description: Use when the user wants to annotate images in VIA, discuss or analyze VIA annotations, asks Claude to add/review/analyze bounding boxes or regions, or mentions opening the VIA annotator. Also trigger when the user shares a VIA project JSON file.
---

# annotate

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
5. After every 3–5 region writes, call `via_get_image(fid, overlay=true)` to see
   your placements drawn on the image. Correct anything that landed off-target
   before continuing — without overlay you are dead-reckoning on your own work.
6. Tell the user: "your browser will update in a few seconds"

**Do not write annotations without reading first.** State may have changed since
the last tool call.

**Be honest about cited evidence.** When a region's `description` claims it
exemplifies some feature ("this rosette shows the diagnostic interior dot"),
re-look at the box after placing it and verify that the cited feature is
actually inside *that* box — not merely somewhere in the image.

**Batching granularity.** Images with clean spatial separation between
features (each feature has a clear centre and clean edges) tolerate one-shot
parallel batched placement — 10+ regions in one call, no inter-region
feedback needed. Images with overlapping, occluded, or scale-ambiguous
features want iterative batches of 2–3 with an overlay check between them.
If you're not sure which kind of image you have, start with a small batch.

## Tool Quick Reference

| Situation | Tool |
|-----------|------|
| Get annotator URL | `via_get_annotator_url` |
| Load an image into the project | `via_add_file` |
| **See the image you're annotating** | `via_get_image` |
| Zoom into a sub-region at full res | `via_get_image_crop` |
| Orient to project | `via_get_project` |
| Quick file list | `via_list_files` |
| Read annotations | `via_get_annotations` |
| Add one region | `via_add_region` |
| Fix an annotation | `via_update_region` |
| Remove an annotation | `via_delete_region` |
| Bulk / wholesale changes | `via_update_project` |
| Save project JSON to disk | `via_save_project` |

Prefer `via_get_annotations` over `via_get_project` for reads — it returns only
metadata, not the full project blob. After the user corrects placements in the
browser, call `via_get_annotations(format="fraction")` to get coords as 0–1
fractions — easier to diff against your originals than raw pixels, and forces
the calibration arithmetic onto the server.

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
the image (downscaled for context efficiency, default longest edge = 2048 px) plus
the **original pixel dimensions** — coordinates you pass back must be in original
pixel space (unless you opt into `xy_space="fraction"` on the write call).

```
via_get_image("1")
→ "Image fid=1 fname=cat.jpg original=1798×1211px.
   Coordinates you provide must be in original pixel space."
+ <image>
```

**For small features** (people, faces, text, anything smaller than ~2% of the
frame), call `via_get_image(fid, max_dim=2048)` *first*, not as a correction
step. The previous default was 1024 — features under ~20 px in returned space
were ambiguous and produced placement errors of multiple box-widths. The default
is now 2048 for exactly this reason; drop it back to 1024 only when you know the
image has no small targets and you want the context savings.

**To verify placements**, pass `overlay=true`:

```
via_get_image("1", overlay=true)
→ image with every existing region drawn as a coloured shape + label
```

Use overlay after every 3–5 writes. Without it you are estimating where your
previous boxes landed, which is exactly the perception loop the tool is meant
to close.

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

### Choose the natural shape first; rectangle is the fallback

Before placing each region, ask what the natural shape of the feature is. Each
shape choice is itself a claim about the feature, and a varied-shape annotation
set communicates more than a rectangle-only one.

- Spheres, disc faces, clock faces, single visible eyes → **circle**
- Foreshortened domes, brims, oblique disks → **ellipse**
- Irregular tear-drops, triangular bodies, path/road footprints → **polygon**
- Thin diagonal features (struts, wires, ropes, branches) → **polyline**
- Directional intent (gaze, trajectory, line of sight) → **polyline** with arrow-phrase label
- Genuinely axis-aligned bounded regions (a window, a body, a building face) → **rectangle**

Default to rectangle only when the feature is genuinely rectangular or when no
other shape obviously fits.

### Coordinate space — prefer `xy_space="fraction"`

`via_add_region` and `via_update_region` accept an optional `xy_space`:

- `"fraction"` — coords are 0.0–1.0 fractions of the original dims; the server
  scales them for you. **Prefer this** — every multi-region session that used
  fractions has hit zero arithmetic errors. Read positions off the returned
  image proportionally ("about 40% from the left, 60% down") and pass the
  fractions through. Rect `w`/`h` and ellipse `rx`/`ry` scale per-axis;
  circle `r` scales by the geometric mean.
- `"original"` (default) — coords in `xy` are pixels in the *original* image.
  Kept as the default for back-compat; you have to do the
  returned-px-to-original-px multiplication yourself.

**If you must use `"original"`:**
Returned image 1024×683, original 4651×3101. A feature observed at returned
(500, 300) is at original `(500 × 4651/1024, 300 × 3101/683)` ≈ `(2271, 1362)`.
Multiply per axis by `original_dim / returned_dim`. Or just use
`xy_space="fraction"` and pass `(500/1024, 300/683)` ≈ `(0.488, 0.439)` — same
destination, no arithmetic.

**Thin diagonal features (struts, wires, ropes, poles, branches)** want the
polyline encoding `[6, x1, y1, x2, y2]`, not an axis-aligned rectangle. A
bounding rect around a diagonal will be visibly off at both ends; the polyline
sits on the feature.

**Linear-but-wide features (paths, stair flights, streams, road surfaces)**
want the polygon footprint `[7, x1, y1, x2, y2, ...]`, not a polyline along
the centreline. A thin polyline beside an irregular wide feature reads as
"almost on it but not quite" even when geometrically correct, because the eye
cannot judge a 2-px line against a 50-px-wide path. Trace the visible footprint
as a polygon instead.

**Directional / axial intent (gaze, trajectory, approach line, sightline)**
wants the polyline encoding `[6, x1, y1, x2, y2]` with an arrow-phrase label
(`fly→flower approach axis`, `looking toward X`). An axis-aligned rectangle
across the same line reads as a bounded region, not a direction.

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

> **The av-key gotcha.** The default schema's attributes are `"1"` (label) and
> `"2"` (description). Pass *those IDs* as keys — `av: {"1": "jaguar", "2": "head-on pose"}`.
> The server also accepts the human anames as a courtesy: `av: {"label": "jaguar", "description": "..."}`
> gets remapped to numeric IDs. **Unknown keys are now rejected** (they used to
> be silently stored, which dropped labels from the canvas). If you see
> `Unknown av key(s)`, call `via_get_project` to see the current schema.

### Attribute conventions

**`label` names the thing. `description` cites the visible evidence** for the
label — colour, shape, scale, texture, position — so the annotation is a
falsifiable claim, not a free-text guess. Compare:

- Weak: `label="compound eye"`, `description="red eye"`
- Strong: `label="compound eye"`, `description="deep red-brown holoptic eye with visible pseudopupil and fine pale setae on the surface"`

The strong form lets the next reader (or the user) check the claim against the
pixels; the weak form is just a re-statement of the label.

**For labels that imply a measurable property** (grain size, age class,
magnitude, count, distance, height), ground the term in the visible scale of
the feature, not the lexical association. Example: pebble (4–64 mm) vs cobble
(64–256 mm) vs boulder (>256 mm) are real Wentworth distinctions — don't
reach for "pebble" just because the rocks are rounded and in a streambed.
Same family: "small mammal" without a length cue, "tall tree" without a
scale reference, "magnitude 5 quake" with no felt-intensity evidence.

**Describe what's visible, not what it might be called.** For terms imported
from training data (architectural names, species, archaeological labels,
geological formations), describe the visible structure first and supply the
canonical name only if the image itself disambiguates. Resisting "Court of
Three Stupas" in favour of "upper terrace ruins (north annex)" is a feature,
not a hedge — the visible description is verifiable; the canonical name needs
a site plan to confirm.

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

## Perception gotchas

Errors that have shown up repeatedly across sessions and survived the overlay
check. Each one costs a user-correction round if you don't watch for it.

**Extend the box to the visible extremity, not the bulk.** When boxing a
shape with a thin, curved, or projecting part — a tail, an abdomen tip, an
ear, a wing tip, the south wing of an L-shaped roof — the reflexive failure
is to box the *bulk* of the feature and clip the *extremity*. Before
declaring a placement done, identify the lowest/rightmost/etc. pixel that
semantically belongs to the named feature and check the box edge reaches it.

**Box the whole named object, not its most prominent part.** A box labelled
`church` covers nave + tower + spire, not just the tower. A box labelled
`foot warmer` covers the wooden housing + base + visible shadow, not just
the pierced-top cube. A box labelled `tree` covers trunk + crown together.
Different family from extend-to-extremity (which is about a single part's
edges) — this is about *which structural parts count as the thing being
named*. Ask: if a viewer drew a line around "the X" with no prior context,
where would they stop? Box to there.

**Re-crop immediately before placing small features.** Crops you took during
orientation fall out of working memory after ~5 unrelated tool calls. For
any feature under ~10% of canvas extent, call `via_get_image_crop` on its
local area again *just before* placing — not as a correction step after.
Reading position off a remembered orientation crop reverts to thumbnail-
eyeball precision and produces 30–500 px offsets that survive overlay.
Sub-30 px features in clean backgrounds (single birds in sky, distant
figures) want this even more strongly — place coords directly off a full-
res crop, not off the full-image overlay.

**Crop-and-drop, not just crop-and-place.** `via_get_image_crop` is also
a go/no-go tool. If a candidate feature doesn't survive a high-resolution
crop — the "lightning tower" turns out to be utility poles, the "single
wall clock" turns out to be empty shelf-front — drop the annotation rather
than place a confident box on a feature you can't actually see. Resist the
urge to label features from prior expectation when the pixels don't
disambiguate them.

**Densely-figured vertical compositions: pre-crop the halves.** When a
figural composition fills most of the frame vertically (a Lamentation,
a multi-figure interior, a tall street scene), a single full-image overlay
at 2048 px compresses the vertical extent enough to hide ~15% y-bias on
your placements. Before placing any boxes, take two crops — upper half and
lower half — and place positions off those, not the full-image view. Saves
a full correction round.

**Time-of-day priors need season and latitude.** "5 a.m. local" is
pre-dawn in winter at mid-latitudes but well past sunrise in late spring;
"morning" at the equator is different from the same hour at 60° N. When
your prior depends on lighting, shadow direction, or sky colour from a
timestamp, check season + latitude before committing — otherwise the
clock-face overrides the actual photometry visible in the image.

**Reflections are separate regions.** On still water, glass, polished floors,
or any mirror surface, the reflection is its own visual region — not part of
the object it reflects. A box for "tree" must not extend into the inverted
mirror of the tree below the waterline, even though the two look visually
identical. If the image has a reflective surface, add a separate region for
the reflection rather than letting an object box swallow it.

**Verify identity, not just position.** For named-part labels (windscreen,
lens, helmet, eye, cockpit), ask "does this shape actually *look like* the
thing I'm calling it?" — a "windscreen" should be visibly transparent; a
"helmet" should be solid; an "eye" should have a pupil. The overlay check
confirms a box is in the right neighborhood; it does *not* catch you labeling
the helmeted head "windscreen" because the prior expected one nearby. One
session shipped a complete pilot↔windscreen label swap that overlay passed.

**Overlay is necessary but not sufficient.** The overlay renders at returned-
image resolution; you're judging placements at coarser spatial fidelity than
the user sees in the browser at full resolution. Before declaring done /
saving / handing off, pause and let the user spot-check in the browser. Most
errors that survive overlay are caught by the user in ~5 seconds.

**Use `via_get_image_crop` when overlay is ambiguous.** If a placement looks
"close" on overlay but you're not sure, call `via_get_image_crop(fid, bbox)`
on the area at full resolution. The crop is taken from the original image,
so a 500×500 window inside a 4651×3101 photo comes back at native pixel
density, not the heavily-downscaled view.

## Common Patterns

**"Annotate this image" / "Load img002.jpg"**
→ `via_add_file("/absolute/path/to/img002.jpg")`, then `via_get_image(fid)`, then share the URL

**"What's in this project?"**
→ `via_list_files` then `via_get_annotations`

**"Add a bounding box for X at coordinates..."**
→ `via_get_image(fid)` first, then `via_add_region` with `xy: [2, x, y, w, h]`

**"Annotate a direction / line of sight / trajectory / approach axis"**
→ polyline `via_add_region` with `xy: [6, x1, y1, x2, y2]` and an
arrow-phrase label (`"fly→flower approach axis"`, `"gaze toward X"`).
Rectangles read as bounded regions, not directions.

**"Annotate a path / road / stair flight / stream"**
→ polygon `via_add_region` with `xy: [7, x1, y1, x2, y2, ...]` tracing the
visible footprint. Polylines along the centreline of a wide irregular
feature look misaligned in the overlay even when the geometry is fine.

**"Review my annotations for consistency"**
→ `via_get_image(fid)` + `via_get_annotations`, then reason over both

**"That box looks off — let me check"**
→ `via_get_image_crop(fid, bbox, xy_space="fraction", overlay=true)`
on the suspected area; full-res crop reveals what the downscaled overlay hid

**"User just corrected my boxes in the browser — what changed?"**
→ `via_get_annotations(format="fraction")`, compare against the fractions
you last placed. Pixel-space diffs require manual scale arithmetic; the
fraction format makes the deltas readable directly.

**"Re-annotate everything based on..."**
→ `via_update_project` with the full modified project JSON

**"Open the annotator" / "Where do I go?"**
→ `via_get_annotator_url`, share the URL with the user

**"Save this work"**
→ `via_save_project("/path/to/output.json")`
