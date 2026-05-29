# Common patterns

Trigger-phrase cheatsheet — natural-language requests mapped to the
right tool calls.

## Core workflow

**"Annotate this image" / "Load img002.jpg"**
→ `via_add_file("/absolute/path/to/img002.jpg")`, then `via_get_image(fid)`,
then share the URL.

**"What's in this project?"**
→ `via_list_files` then `via_get_annotations`.

**"Add a bounding box for X at coordinates..."**
→ `via_get_image(fid)` first, then `via_add_region` with `xy: [2, x, y, w, h]`.

**"Annotate a direction / line of sight / trajectory / approach axis"**
→ polyline `via_add_region` with `xy: [6, x1, y1, x2, y2]` and an
arrow-phrase label (`"fly→flower approach axis"`, `"gaze toward X"`).
Rectangles read as bounded regions, not directions.

**"Annotate a path / road / stair flight / stream"**
→ polygon `via_add_region` with `xy: [7, x1, y1, x2, y2, ...]` tracing
the visible footprint. Polylines along the centreline of a wide
irregular feature look misaligned in the overlay even when the
geometry is fine.

**"Review my annotations for consistency"**
→ `via_get_image(fid)` + `via_get_annotations`, then reason over both.

**"Re-annotate everything based on..."**
→ `via_update_project` with the full modified project JSON.

**"Open the annotator" / "Where do I go?"**
→ `via_get_annotator_url`, share the URL with the user.

**"Save this work"**
→ `via_save_project("/path/to/output.json")`.

**"When was this photo taken?" / "What camera / lens / GPS?"**
→ `via_read_metadata(fid)`. Returns capture time, GPS, camera, lens,
exposure, orientation, dims. Run at session start on each new file
to ground priors — a capture timestamp + GPS catches a whole class
of lighting / season / location prior failures.

**"Load this PDF for annotation" / "Annotate page 3 of this report"**
→ `via_load_document("/path/to/doc.pdf", pages="all")` for everything,
or `pages=[2]` for just page 3 (0-based). Each page becomes its own
file entry that the rest of the tools treat as a normal image.
Requires the `[io]` extra and `poppler-utils` on PATH.

## Verification + diff

**"That box looks off — let me check"**
→ `via_get_image_crop(fid, bbox, xy_space="fraction", overlay=true)`
on the suspected area; full-res crop reveals what the downscaled
overlay hid.

**"User just corrected my boxes in the browser — what changed?"**
→ `via_get_annotations(format="fraction")`, compare against the
fractions you last placed. Pixel-space diffs require manual scale
arithmetic; the fraction format makes the deltas readable directly.

## AI-assisted (only when `[ai]` is installed)

**"This image has a lot of stuff — help me find candidates"**
→ `via_classify_scene(fid)` then
`via_suggest_regions(fid, prompts=[...])`. The classifier sets
routing; the suggester returns boxes to review.

**"Did I miss anything?"**
→ `via_suggest_regions(fid, exclude_existing=True, broad_prompts=True)`.
Returns candidates that don't overlap your existing annotations.

**"That box looks loose around the object — can you tighten it?"**
→ `via_tighten_region(metadata_id)`, review the proposed tighter box,
then `via_update_region` if it looks right.

**"Am I sure this is actually a windscreen?"**
→ `via_verify_region(metadata_id)`. The VLM describes what it sees;
read the supporting/contradicting text and decide.

**"Quality-check my annotations before saving"**
→ `via_grade_annotations(fid)`, surface anything flagged to the user.

**"What models are available?"** / **"Why isn't the AI doing anything?"**
→ `via_model_status`. Reports whether the `[ai]` extra is installed,
configured pipelines, currently loaded adapters, memory use.

**"Annotate one X, find all the others"** (one-shot detection)
→ Annotate one example via `via_add_region`, then
`via_find_similar(metadata_id=...)`. Returns candidate boxes for
every similar object found.

**"What does this image / region actually look like?"** (free-form)
→ `via_ask_model(fid, question="describe this in detail")` for the
whole image, or with `metadata_id=<mid>` / `region_bbox=[...]` for a
specific area. Use when the structured tools (verify, grade) don't
cover what you need to know.

**"Detection is too slow / I just need rough boxes fast"**
→ `via_suggest_regions(..., pipeline="fast")` if `[yolo]` is installed
and `detect.fast` is configured. YOLO-World is ~10× faster than the
GroundingDINO default on CPU.
