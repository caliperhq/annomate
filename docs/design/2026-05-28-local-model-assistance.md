# Local-model assistance for annotation

**Status:** Design proposal — not yet approved for implementation.
**Branch:** `feat/local-model-assistance`.
**Date:** 2026-05-28.

---

## Goal

Add a layer of *optional* local-model assistance to the `annotate` MCP server.
The model does the boring/repetitive part (seed boxes, tighten edges, verify
labels, find what was missed, grade the final set); the human and the LLM
collaborate on the judgment calls. Nothing is auto-applied without review.

The training-session corpus (12 sessions in `annotate-examples/.training/`)
exposes the failure modes this targets:

- **Coordinate drift** — eyeballed positions off by 30–500 px (10, 11, 12).
- **Label-swap / identity errors** — helmet labelled "windscreen" (04).
- **Vocabulary errors** — "pebble" used for cobble-sized rocks (05).
- **Missed features** — single birds, distant figures, foreground objects
  (07, 08, 11).
- **Named-object-vs-named-part** — boxing the tower but labelling it "church"
  (11, 12).
- **Reflection vs. feature** — boxes extending into the mirror image (03).

## Non-goals

- Replacing the human review loop.
- Training/fine-tuning models inside the server.
- Shipping model weights with the package (lazy download from HF).
- Forcing a specific accelerator. CPU / Apple ANE / Snapdragon Hexagon /
  Intel NPU / NVIDIA GPU should all work via the same tool surface, with
  performance set by the configured backend.

---

## The four new MCP tools (down from five — find-missing folded into suggest-regions)

### 1. `via_suggest_regions(fid, prompts, tiling="auto")`

Open-vocabulary detection. Given text prompts, return candidate boxes with
confidences. The LLM reviews, accepts, edits, or drops them; nothing is
written to the project automatically.

```python
via_suggest_regions(
    fid="3",
    prompts=["person", "horse", "ship's binnacle", "globe"],
    tiling="auto",          # auto | none | 2x2 | 4x4 | adaptive
    min_confidence=0.3,
)
→ {
    "candidates": [
      {"xy": [2, 0.51, 0.62, 0.04, 0.06], "label": "person",
       "score": 0.82, "tile": [1,2]},
      ...
    ],
    "model": "grounding-dino-tiny",
    "tile_grid": "4x4 overlap=0.2",
}
```

Maps directly to the workflow gap from 11-avercamp (200 small figures) and
09-marche-dauphine (dense shop inventory).

### 2. `via_tighten_region(mid)`

Promptable segmentation. Take the existing box, run SAM with it as a prompt,
derive a tight bounding box from the resulting mask. Show the LLM the IoU
delta with the original; auto-apply only if the user opts in.

```python
via_tighten_region(mid="abc12345", auto_apply=False)
→ {
    "current_xy": [2, 1000, 500, 200, 200],
    "tightened_xy": [2, 1018, 520, 164, 178],
    "iou": 0.78,
    "mask_area_px": 28910,
    "model": "sam2-tiny",
}
```

Directly targets named-object-vs-named-part (when SAM gets the *whole*
object including the shadow/base) and extend-to-extremity (SAM doesn't
miss the abdomen tip).

### 3. `via_verify_region(mid)`

Crop-and-verify with a VLM. Crops to the region, asks "is this a {label}?
What supports that? What contradicts?" Returns structured output.

```python
via_verify_region(mid="abc12345")
→ {
    "label_claimed": "windscreen",
    "verdict": "no",
    "confidence": 0.71,
    "supporting": [],
    "contradicting": ["solid leather surface, no transparency",
                       "visible buckle suggests a helmet strap"],
    "suggested_label": "leather flying helmet",
    "model": "florence-2-base",
}
```

Catches label-swap (04-haefeli pilot↔windscreen) and identity errors before
save. Use as a pre-save linter.

### 4. `via_suggest_regions(..., exclude_existing=True, broad_prompts=True)` — find-missing mode

Rather than a separate tool, **rolled into `via_suggest_regions`** as two flags:

- `exclude_existing=True` → subtract any candidate whose IoU with an
  existing region exceeds a threshold (default 0.5).
- `broad_prompts=True` (or `prompts=None`) → use a default broad prompt set
  ("any person, animal, vehicle, instrument, tool, text, face, sign, vessel,
  flying object"). User can override.

Same model, same code path; eliminates a separate tool from the surface.

Would have caught the V-formation displacement in 11-avercamp, the lone
west-slope visitor in 07-takht-i-bahi, and the punt-pole hand object in
03-mangrove.

### 5. `via_grade_annotations(fid, mids=None)`

Per-region quality assessment. For each region (or a specified subset),
compute:

- **Position accuracy** — is the box centred on the feature, or offset?
- **Size / extent correctness** — does it cover the named object's full
  extent?
- **Label accuracy** — does the visual content match the label?
- **Shape-encoding appropriateness** — is the shape *choice*
  (rect / circle / polygon / polyline) the natural fit? **Note:** this
  grades the *encoding choice*, not the per-vertex accuracy of polygon
  perimeters. Polygon perimeter accuracy needs a SAM-segment-and-compare
  pass (proposal: a separate `via_polish_polygon(mid)` tool, deferred).

```python
via_grade_annotations(fid="11")
→ {
    "regions": [
      {"mid": "abc12345", "label": "fallen skater",
       "position": 0.92, "size": 0.61, "label_match": 0.88,
       "shape_encoding_fit": "good",
       "issues": ["box extends ~15% past skater's outline to the south"]},
      ...
    ],
    "overall": {"mean_position": 0.84, "mean_size": 0.79,
                 "mean_label_match": 0.86, "flagged_count": 3},
    "model": "clip-cosine-rubric (or upgraded grader when one ships)",
}
```

**Honest scope note:** the original [ClipGrader paper](https://arxiv.org/pdf/2503.02897)
(March 2025) does not have a public checkpoint at the time of writing
(verified May 2026). Phase 3 will implement a *ClipGrader-inspired*
scorer: crop the region, compute CLIP text-image cosine similarity
between the crop and the label, then compute the same against a tightened
crop (cropped to a SAM mask) — divergence between the two scores
flags position/size errors. This is weaker than the paper's purpose-built
grader but uses only off-the-shelf checkpoints and runs in <1 s on CPU.
If a real ClipGrader checkpoint appears later, drop it in as a pipeline
override.

Replaces the manual diff against user-correction coords (P0 #2 from the
prior plan addressed reading the diff; this addresses *catching what the
user would have flagged before they have to*).

---

## Cross-cutting feature: image tiling

Recommended approach: **adopt SAHI** (`obss/sahi`) as the slicing/merging
engine rather than reinvent. It's the standard for slicing-aided hyper-
inference, supports HuggingFace Transformers detectors, handles NMS-based
merging across tile boundaries, and has the boundary-recall numbers to
back it up (overlap-0.2 raises boundary-zone recall from ~26-63% to
70-100%; sweet spot is 6-20% overlap, then diminishing returns).

### Tiling modes

| Mode | Behaviour |
|------|-----------|
| `none` | Single full-image inference. |
| `2x2`, `4x4`, `8x8`, `16x16` | Uniform grid, 20% overlap. |
| `auto` | Pick the grid from image size and aspect ratio (table below). |
| `adaptive` | Saliency-driven: run a fast saliency pass, place dense tiles only on busy regions. |

Auto-grid heuristic (start simple, refine from corpus data):

| Longest edge | Default grid |
|--------------|--------------|
| ≤ 1024 px | `none` |
| 1025–2048 | `2x2` |
| 2049–4096 | `4x4` |
| > 4096 | `8x8` |

Aspect ratios trigger asymmetric grids (e.g. 4×2 for wide landscapes).

### Adaptive (saliency-driven) tiling

Two paths to evaluate:

1. **Saliency map → cluster → tile.** Run a fast saliency model
   (BASNet, U²-Net, or even classical OpenCV `saliency.StaticSaliencySpectralResidual`),
   threshold, cluster into connected components, place a tile around each
   cluster. Cheap; works well when "interesting" regions are spatially
   clustered.
2. **VLM tile suggestion.** Ask a small VLM: "Where should I look more
   closely in this image? Return up to 8 bounding boxes covering the
   regions with the most detail." This is the "ask a local model for
   optimal cut locations" feature from the brief. Output goes through the
   same SAHI merge pipeline; the tiles just aren't on a uniform grid.

The VLM path is more flexible but slower; the saliency path is sub-second.
Both are exposed; default is `none` for small images, `auto` for large,
`adaptive` as opt-in.

### Tiling is an *internal* helper, not its own tool

The LLM doesn't need to plan tiles directly — the detection / find-missing
tools handle tiling via the `tiling=` arg. Keep `_suggest_tiles` as an
internal function used by those tools. If a real use-case emerges for
exposing it (e.g. an LLM wants to plan a multi-pass workflow), promote it
to a tool then.

---

## Cross-cutting feature: pluggable model registry

A TOML config file at `~/.config/annotate/models.toml` (overridable by
`--models-config` flag) defines named pipelines and per-task defaults.

```toml
[detect.default]
model = "grounding-dino-tiny"
device = "auto"           # auto | cpu | mps | cuda | cuda:0 | ane

[detect.dense_scene]
model = "owlv2-large-patch14-ensemble"
device = "auto"

[detect.aerial]
model = "locate-anything-3b"
device = "cuda"           # too big for CPU

[segment.default]
model = "sam2-tiny"
device = "auto"

[segment.high_quality]
model = "sam3"
device = "auto"

[verify.default]
model = "florence-2-base"
device = "auto"

[grade.default]
model = "clip-grader-vit-b16"
device = "auto"

[classify.default]
model = "clip-vit-b32"
device = "auto"
labels = ["dense_crowd", "single_subject", "painting",
          "aerial_or_satellite", "document", "diagram",
          "interior_scene", "landscape"]
```

Each tool accepts `pipeline="..."` to override the default at call time:

```python
via_suggest_regions(fid="3", prompts=["person"], pipeline="dense_scene")
```

### Auto-classifier (model routing)

A `classify.default` model (default: `CLIP-ViT-B/32`, ~150 MB) runs once per
image on first use, caches the result, and stores it on the file entry as
`scene_class`. The dispatcher then picks the pipeline mapped to that class
in the config:

```toml
[routing]
dense_crowd        = "dense_scene"
single_subject     = "default"
painting           = "default"
aerial_or_satellite = "aerial"
document           = "default"  # or a doc-layout model when one is added
```

CLIP zero-shot classification on a fixed label set is fast (sub-second on
CPU for the base model) and good enough for routing. Misclassifications
just mean a suboptimal model is picked — the user can override.

### Lazy loading + eviction

- Models load on first use, not at startup.
- LRU eviction across loaded models when memory pressure (configurable
  `max_loaded_models` and `max_gpu_memory_gb`).
- Status surfaced through a new `via_model_status` tool: which models are
  loaded, current memory use, last call time.

### Default model picks — small and runnable

| Task | Default | Size | Why |
|------|---------|------|-----|
| Detect | `IDEA-Research/grounding-dino-tiny` | 172 M | Workhorse, CPU-viable, ~0.5-1 s on a modern x86 core |
| Segment | `facebook/sam2-hiera-tiny` | 38.9 M | Smallest SAM 2; sub-second on CPU at 1024 px |
| Verify | `microsoft/Florence-2-base` | 230 M | Good for grounding/captioning at small size; ANE/Core ML port exists |
| Grade | `clip-grader-vit-b16` *(or fallback CLIP)* | 150 M | If ClipGrader checkpoint isn't released, fall back to CLIP cosine-similarity scoring against the label |
| Classify | `openai/clip-vit-base-patch32` | 150 M | Standard zero-shot scene classifier |
| Saliency | `briaai/RMBG-2.0` *(or U²-Net)* | 196 M | Used for adaptive tiling |

Total cold-cache footprint: ~950 MB on disk, ~3-4 GB resident if all
loaded simultaneously. LRU eviction keeps this manageable.

Optional larger pipelines named in config (user opts in):
- `detect.dense_scene` → OWLv2 large ensemble (430 M)
- `verify.deep` → MiniCPM-V 4.6 (1 B) or Qwen 2.5-VL 3B
- `segment.high_quality` → SAM 3 (800 M)

---

## Multiple models / finetunes per tool

The TOML structure already supports this via named pipelines. To use a
finetune, register it as a new pipeline entry and route to it by image
class or by explicit `pipeline=` arg. Example: register a fine-tuned
GroundingDINO for medical imaging as `[detect.medical]`, route to it via
the scene classifier outputting `medical_image` → `medical`.

The system doesn't *train* anything itself — you bring the weights, the
registry exposes them.

---

## Implementation phases

**Phase 1 — foundations (no models loaded yet)**
- Model registry, config loader, lazy adapter framework.
- Tool stubs that return `"feature not enabled — install `annotate[ai]` extra"` until weights are present.
- `via_model_status` tool.

**Phase 2 — detection + tiling**
- `via_suggest_regions` with uniform tiling (SAHI).
- Default: GroundingDINO tiny.
- Saliency-based adaptive tiling (internal `_suggest_tiles`, fast path:
  classical OpenCV saliency or U²-Net).

**Phase 3 — segmentation + grading**
- `via_tighten_region` (SAM 2 tiny).
- `via_grade_annotations` (CLIP-cosine rubric; see scope note on the tool).

**Phase 4 — verification + missed-feature search**
- `via_verify_region` (Florence-2 base).
- `via_suggest_regions(exclude_existing=True, broad_prompts=True)` — find-missing mode.

**Phase 5 — auto-classifier + routing + VLM tile suggestion**
- CLIP-based scene classifier, scene→pipeline routing.
- VLM-based adaptive tile suggestion (depends on Phase 4 verify model
  being available, so cannot land earlier).

Phases 1–3 are the high-value MVP — every training session would have
benefited from these. Phases 4–5 are leverage features for users who run
many sessions.

---

## Packaging

- New optional extra: `pip install annotate[ai]` pulls `torch`,
  `transformers`, `sahi`, `sam2`, `accelerate`.
- The base package stays small; AI features are off by default.
- Models download lazily from HF Hub via the standard cache.
- The MCP tool list always advertises the AI tools, but they return a
  helpful "install the `ai` extra" error when not available.

---

## Open questions

1. **Should `via_grade_annotations` write back into the project, or only
   return a report?** Proposal: return only; let the LLM decide which
   suggestions to apply via `via_update_region`. Keeps the human in the
   loop.
2. **Should we ship a sample TOML config or auto-generate one on first
   run?** Proposal: auto-generate at `~/.config/annotate/models.toml` if
   missing, with the defaults table above as the contents.
3. **How does this interact with the existing browser-side workflow?**
   Suggestions don't touch the browser until the user / LLM writes via
   `via_add_region`, so no impact on the push/pull protocol.
   *To verify before Phase 5*: pushing a project with a custom
   `scene_class` field on file entries and pulling it back must preserve
   the field. `abs_path` already does this, so the precedent is good, but
   a 30-second smoke test in Phase 1 confirms it for `scene_class`
   specifically.
4. **ONNX/Core ML/QNN backends — same tool surface, different config?**
   Proposal: yes, via a `device`/`backend` field per pipeline. Start with
   PyTorch + MPS/CUDA; ONNX is a phase-6 follow-up.
5. **Failure modes when the model output is garbage** (hallucinated boxes,
   wrong language, exceeded confidence threshold but obviously off)?
   Proposal: every result includes the raw model output in a `debug`
   field so the LLM can sanity-check before trusting.
6. **Token / context costs** — these tools return JSON, sometimes long
   lists (200+ candidate boxes on 11-avercamp). Consider a `top_k`
   parameter and / or returning a summary by default with a paginated
   detail tool.

---

## What this is *not* solving

- The model-output trust problem is unchanged. Treat every suggestion as a
  hint, not a fact. The skill already drills this; the new tools just give
  the LLM more hints to evaluate.
- Performance on tiny features (sub-30 px) is bounded by model
  capability — tiling helps, but won't beat actual high-resolution training
  data for the specific feature class.
- Cross-image consistency (same label means the same thing in image 1 and
  image 9) — that's an annotation-set-level concern, not a per-region tool.
  Could be a phase-6 addition.

---

## Concrete first step

If you approve the plan, the next commit on this branch is Phase 1:
the registry, config loader, lazy adapter framework, and the stub tools.
No model weights, no inference, no large dependencies — just the
scaffolding the rest hangs off, plus tests. Roughly 300–500 LOC + 50 LOC
of tests, no new runtime deps yet.
