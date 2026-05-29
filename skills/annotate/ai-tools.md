# Local-model assistance

Optional tools that delegate the boring/repetitive parts of annotation
to small local models. **Only relevant when the `[ai]` extra is
installed** — without it these tools return a structured `install_hint`
rather than running.

Call `via_model_status` if you're unsure what's loaded. The report
includes `ai_extra_installed`, configured pipelines, currently loaded
adapters, and memory use.

## The cardinal rule

**Nothing is auto-written.** Every AI tool returns *candidates* or a
*report* — you (and the user) still decide what makes it into the
project via `via_add_region` / `via_update_region`. Treat every
suggestion as a hint, not a fact.

## The tools

### `via_suggest_regions(fid, prompts=[...])`

Open-vocabulary detection from text prompts, with automatic tiling so
small objects in large images don't get downscaled into invisibility.
Use when:
- Annotating a dense scene with many similar features (Avercamp-style
  ice skaters, market-stall inventories) — seed candidates, then
  review / edit rather than placing every box from scratch.
- The image is large (≥ 2048 px on the longest edge) and you'd
  otherwise be eyeballing positions off a downscaled view.

```
via_suggest_regions(fid="3", prompts=["person", "horse", "boat"], tiling="auto")
→ {candidates: [{xy, label, score, tile}, ...], tile_grid: "4x4", ...}
```

Tiling modes: `none` / `2x2` / `4x4` / `8x8` / `16x16` / `auto` /
`adaptive`. Use `auto` (default) unless you have a specific reason;
`adaptive` runs saliency clustering to focus on busy regions.

### `via_suggest_regions(..., exclude_existing=True, broad_prompts=True)` — find-missing

Same tool, two flags: subtracts anything you've already annotated and
runs a default broad prompt set ("person, animal, vehicle,
instrument, tool, text, ..."). Use as a final pass before declaring
done — catches the V-formation in the sky you didn't notice, the lone
figure on the perimeter road, the small object in the corner.

### `via_tighten_region(metadata_id)`

SAM-based region refinement. Pass an existing region; the segmenter
runs inside the bounding box and returns a tightened result, plus IoU
and mask area. Use when:
- Your initial box is "roughly right" but you want crisp edges.
- The named object has parts you might have clipped (foot warmer base,
  church spire, extended limb) — SAM often catches the full extent.

**Rectangle input** → returns `tightened_xy_pixel` + `tightened_xy_fraction`.

**Polygon input** (shape `[7, x1, y1, ...]`) → also returns
`tightened_polygon_fraction`, a SAM-mask contour in the same `[7, ...]`
fraction encoding. When you call `auto_apply=True` on a polygon region,
the polygon contour is written back — not the bbox.

Pipeline `segment.yoloe` (YOLOE native segmentation, no separate SAM
pass) is faster and often tighter on objects the detector already knows.
Use it via `via_tighten_region(mid, pipeline="yoloe")` when YOLOE is
configured.

**Does not write** unless `auto_apply=True`. Review the proposed change
first, then apply via `via_update_region` if preferred.

### `via_verify_region(metadata_id)`

VLM crop-and-verify. Qwen2.5-VL-3B identifies the cropped region; if the
label is in the description, the verdict is `yes` with the description
as supporting evidence; if not, `no` with the description as
contradicting evidence plus a `suggested_label` extracted from the
caption. Use when:
- The named-part is ambiguous and you want a sanity check before save
  (the canonical pilot↔windscreen swap would have been caught here).
- You're not sure between two plausible identifications.
- Pre-save linting: run over every region with a non-trivial label.

The verdict is heuristic — read the supporting / contradicting text
and use your own judgment. Don't auto-relabel on `no`; surface the
result for review.

### `via_grade_annotations(fid)`

CLIP-cosine rubric scoring every region (or a subset via `mids=[...]`)
on position, size, label match, and shape-encoding fit. Use when:
- You want a quick pass over a finished annotation set to flag any
  obvious issues before handoff / save.
- The user has corrected several regions and you want to learn from
  the pattern (high-scoring regions tend to be the ones they didn't
  touch).

Reports only — never modifies the project. Each flagged region comes
with `issues` notes; surface those to the user rather than silently
acting.

### `via_classify_scene(fid)`

Zero-shot scene classification (CLIP against a config-defined label
set: `dense_crowd`, `painting`, `aerial_or_satellite`, etc.). Persists
the top label on the file entry as `scene_class` so subsequent
detection / segment / verify / grade calls automatically route to the
pipeline configured for that scene. Run once at the start of a session
on each new file; it costs sub-second and influences every downstream
call.

### `via_ask_model(fid, question, ...)`

Free-form Q&A. Routes to a chat-capable VLM (default: Qwen2.5-VL-3B).
Use for questions that don't fit the structured tools above:
- "How many people are wearing red shirts?"
- "What's the make and model of this car?"
- "Read the text on this label."
- "What's the dominant lighting direction?"
- "Is the door open or closed?"

Pass `region_bbox=[x, y, w, h]` (default `xy_space` is `fraction`)
**or** `metadata_id=<mid>` to ask about a specific region; omit both
to ask about the whole image. The answer is whatever the model writes —
read it the way you'd read any other LLM response, and treat its
claims as hints to verify against the pixels, not gospel.

### `via_find_similar(metadata_id, target_fids=None)`

Visual-prompt one-shot detection. Takes an annotated region as the
example; finds similar regions in the same image (default) or across
a list of other images. Killer feature for dense scenes: annotate one
ice skater, find all the others; annotate one bolt-head, find every
bolt on the assembly. Runs on the `find_similar.default` pipeline
(YOLOE-11l, AGPL-3.0). Returns candidates only — nothing is written.

## Pipeline overrides

Most AI tools default to:

| Task | Default pipeline | Model |
|------|-------------|-------|
| detect | `detect.default` | GroundingDINO-tiny |
| detect (fast) | `detect.fast` | YOLO-World (`[yolo]` extra) |
| segment | `segment.default` | SAM 2 hiera-tiny |
| segment (native) | `segment.yoloe` | YOLOE-11l (`[yolo]` extra, AGPL) |
| verify | `verify.default` | Qwen2.5-VL-3B (`[ai]`+`[chat]` extras) |
| grade | `grade.default` | CLIP-ViT-B/32 |
| classify | `classify.default` | CLIP-ViT-B/32 |
| ask | `ask.default` | Qwen2.5-VL-3B (`[chat]` extra) |
| find_similar | `find_similar.default` | YOLOE-11l (`[yolo]` extra, AGPL) |

Override per call via `pipeline=`. The `detect.fast` pipeline (YOLO-
World) is an order-of-magnitude faster than the default on CPU; swap
to it via `via_suggest_regions(..., pipeline="fast")` when latency
matters more than absolute precision.

## Typical AI-assisted flow

For a new dense image:

1. `via_add_file(path)` → fid
2. `via_classify_scene(fid)` — sets routing for the rest
3. `via_suggest_regions(fid, prompts=[...broad list of expected things...])`
4. Review the candidates: accept good ones via `via_add_region`, drop
   bad ones, edit borderline ones.
5. For any candidate where the bbox looks loose:
   `via_tighten_region(mid)`.
6. After your own annotation pass:
   `via_suggest_regions(fid, exclude_existing=True, broad_prompts=True)`
   — catches the things you missed.
7. Pre-save lint: `via_grade_annotations(fid)`; for anything flagged,
   `via_verify_region(mid)` on the suspicious labels.
8. User reviews in the browser. Apply their corrections.

For a single-subject photo: skip steps 2–3 and 6; the AI tools' value
shows up on dense / large images.
