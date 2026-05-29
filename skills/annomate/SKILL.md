---
name: annomate
description: Use when the user wants to annotate images in VIA, discuss or analyze VIA annotations, asks Claude to add/review/analyze bounding boxes or regions, or mentions opening the VIA annotator. Also trigger when the user shares a VIA project JSON file.
---

# annomate

## Overview

You have access to a live VIA v3 image annotator running on localhost
(port is OS-assigned — call `via_get_annotator_url` to get the current
URL). The user annotates in their browser; you read and write
annotations via MCP tools. The browser auto-updates within ~3 seconds
of any change you make — you don't need to tell the user to click
anything.

## Reference files

Detailed material is split across sibling files. Read on demand:

- **[region-encoding.md](region-encoding.md)** — shape encoding table,
  coordinate space (`xy_space="fraction"`), shape-choice guidelines,
  region IDs, file-entry shape, geometry helpers
- **[attributes.md](attributes.md)** — schema, `av` key conventions,
  how to write a strong `description`, bulk-edit attribute shape
- **[perception-gotchas.md](perception-gotchas.md)** — the failure
  modes that survive overlay checks (box extent, identity vs position,
  reflections, time-of-day priors, …) — read before any non-trivial
  annotation session
- **[ai-tools.md](ai-tools.md)** — the local-model assistance layer
  (suggest / tighten / verify / grade / classify / ask / find-similar).
  Only relevant when `[ai]` is installed; check `via_model_status`
- **[common-patterns.md](common-patterns.md)** — trigger-phrase
  cheatsheet for the most common requests

## The loop

Before any analysis or write:

1. Call `via_get_image(fid)` — **you must see the image to place
   accurate coordinates**.
2. Call `via_get_annotations()` — always read current state first,
   don't assume.
3. Discuss, analyze, or propose changes.
4. Write via the appropriate tool.
5. After every 3–5 region writes, call `via_get_image(fid, overlay=true)`
   to see your placements drawn on the image. Correct anything that
   landed off-target before continuing — without overlay you are
   dead-reckoning on your own work.
6. Tell the user: "your browser will update in a few seconds".

**Do not write annotations without reading first.** State may have
changed since the last tool call.

**Be honest about cited evidence.** When a region's `description`
claims it exemplifies some feature ("this rosette shows the diagnostic
interior dot"), re-look at the box after placing it and verify that
the cited feature is actually inside *that* box — not merely somewhere
in the image.

**Batching granularity.** Images with clean spatial separation between
features (each feature has a clear centre and clean edges) tolerate
one-shot parallel batched placement — 10+ regions in one call, no
inter-region feedback needed. Images with overlapping, occluded, or
scale-ambiguous features want iterative batches of 2–3 with an
overlay check between them. If you're not sure which kind of image
you have, start with a small batch.

For the recurring failure modes that even a careful loop misses, read
[perception-gotchas.md](perception-gotchas.md).

## Tool quick reference

### Core annotation tools (always available)

| Situation | Tool |
|-----------|------|
| Get annotator URL | `via_get_annotator_url` |
| Load an image into the project | `via_add_file` |
| **See the image you're annotating** | `via_get_image` |
| Zoom into a sub-region at full res | `via_get_image_crop` |
| Environment inventory (version, extras, pipelines) | `via_capabilities` |
| Orient to project | `via_get_project` |
| Quick file list | `via_list_files` |
| Read annotations (summary) | `via_get_annotations` |
| Read annotations for one view | `via_get_annotations(vid=<vid>)` |
| Add one region | `via_add_region` |
| Fix an annotation | `via_update_region` |
| Remove an annotation | `via_delete_region` |
| Bulk / wholesale changes | `via_update_project` |
| Save project JSON to disk | `via_save_project` |
| Read EXIF / GPS / camera metadata | `via_read_metadata` |
| Load a PDF as one file per page | `via_load_document` |
| OCR an image / region → word boxes | `via_run_ocr` |

### Local-model assistance (optional; needs `pip install 'annomate[ai]'`)

| Situation | Tool |
|-----------|------|
| What models are configured / loaded? | `via_model_status` |
| Seed candidate boxes from text prompts | `via_suggest_regions` |
| Find what I *missed* on this image | `via_suggest_regions(exclude_existing=True, broad_prompts=True)` |
| Tighten a loose box to the actual object outline | `via_tighten_region` |
| "Does this region really show what I labelled?" | `via_verify_region` |
| Score every region for position / size / label match | `via_grade_annotations` |
| Classify the scene (cached → auto-routes other tools) | `via_classify_scene` |
| Ask the local VLM a free-form question | `via_ask_model` |
| "Find more things that look like this one" | `via_find_similar` |

The AI tools are advertised even when the `[ai]` extra isn't installed —
they return a structured `install_hint` rather than erroring. Call
`via_model_status` to see what's available before relying on them. For
how to use them and the typical AI-assisted flow, read
[ai-tools.md](ai-tools.md).

Prefer `via_get_annotations(vid=<vid>)` over `via_get_project` for
reading a specific view's annotations — it returns only that view's
metadata. `via_get_annotations` without `vid` gives a lightweight
per-file summary (region counts + label lists). The default format
is `"fraction"` — 0–1 coords that diff cleanly against your own
placements without pixel arithmetic.

Call `via_capabilities` once at session start to fill the
`annotator_version` / `extras` fields in `.training/` frontmatter.

## Loading images

**Always use `via_add_file` to load images** — never hand-roll file
entries in `via_update_project`. `via_add_file` takes an absolute
path, registers the image with the HTTP server, and returns
`{fid, vid, url}`. The browser can then display the image over HTTP
with no `file://` permission issues.

```
via_add_file("/home/mike/photos/cat.jpg")
→ {"fid": "1", "vid": "1", "url": "http://localhost:PORT/img/cat.jpg"}
```

**Resolve relative paths against your working directory before
calling.** If the user says `../img002.jpg` and your cwd is
`/home/mike/work/`, the absolute path is `/home/mike/img002.jpg` —
don't guess, use `realpath` or `find` if uncertain.

If no project is loaded yet, `via_add_file` creates a minimal empty
one automatically, with a default `label` + `description` attribute
schema already installed.

**Format support.** Pillow-native formats (jpg, png, gif, webp, bmp,
tiff) work out of the box. HEIC / HEIF / AVIF (default iPhone-camera
format) and PDF need the `[io]` extra: `pip install 'annomate[io]'`
(PDF also needs `poppler-utils` on the system path). Non-browser-
native formats (HEIC, BMP, TIFF, PDF pages) are converted once to
JPEG and cached at `~/.cache/annotate/converted/` for browser
display — the cache invalidates automatically when the source file's
mtime changes. The original file path stays authoritative in the
project; AI tools operate on the original.

For **PDFs**, use `via_load_document(path, pages="all")` — it loads
one file entry per page (with `source_pdf` + `source_pdf_page`
fields). For everything else, `via_add_file(path)` handles format
detection automatically.

Other formats (RAW, video, GeoTIFF, DICOM, PSD, gigapixel TIFF) are
designed but not yet implemented — see
`docs/design/2026-05-28-format-conversion-and-tooling.md`.

## Viewing images

**Always call `via_get_image(fid)` before placing coordinates.** The
tool returns the image (downscaled for context efficiency, default
longest edge = 2048 px) plus the **original pixel dimensions** —
coordinates you pass back must be in original pixel space (unless you
opt into `xy_space="fraction"` on the write call).

```
via_get_image("1")
→ "Image fid=1 fname=cat.jpg original=1798×1211px.
   Coordinates you provide must be in original pixel space."
+ <image>
```

**For small features** (people, faces, text, anything smaller than
~2% of the frame), call `via_get_image(fid, max_dim=2048)` *first*,
not as a correction step. Features under ~20 px in returned space
were ambiguous at the old 1024 default and produced placement errors
of multiple box-widths. The default is now 2048 for exactly this
reason; drop it back to 1024 only when you know the image has no
small targets and you want the context savings.

**To verify placements**, pass `overlay=true`:

```
via_get_image("1", overlay=true)
→ image with every existing region drawn as a coloured shape + label
```

Use overlay after every 3–5 writes. Without it you are estimating
where your previous boxes landed, which is exactly the perception
loop the tool is meant to close.

For shape encoding, coordinate-space choices, and the full set of
shape-selection guidelines, read
[region-encoding.md](region-encoding.md). For trigger-phrase
shortcuts, read [common-patterns.md](common-patterns.md).

## Saving work

```
via_save_project("/home/mike/project/annotations.json")
→ "Saved to /home/mike/project/annotations.json"
```

The server also auto-persists state across restarts (at the platform
data dir). `via_save_project` is for explicit user-requested saves or
handoff exports.
