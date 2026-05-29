# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## What this is

`annotate` is a Python MCP server that wraps VIA v3 (VGG Image Annotator).
It runs two interfaces in one process:

- **HTTP** (OS-assigned port): implements VIA's push/pull share protocol +
  serves patched HTML + serves images (transparently converting non-
  browser-native formats to JPEG via an mtime-keyed cache)
- **MCP stdio**: currently exposes ~20 tools across three layers — the
  always-available annotation tools, the optional `[ai]` model-assisted
  tools, and the optional `[io]`/`[ocr]` format/metadata tools

Sister project to [jscad-mcp](https://github.com/caliperhq/jscad-mcp).

## Layout

```
src/annotate/
├── store.py              # ProjectStore: in-memory + disk-persisted annotations
├── http_handler.py       # VIA REST protocol + image serving (cache-aware)
├── server.py             # MCP tool handlers + main() entry point
├── image_io.py           # Format detection, loader dispatch, JPEG cache,
│                         #   EXIF metadata, OCR (when [ocr] installed)
└── models/               # Local-model assistance (lazy adapters)
    ├── __init__.py
    ├── base.py           # Adapter ABC, capability literals, result types
    ├── config.py         # TOML config (~/.config/annotate/models.toml)
    ├── registry.py       # Lazy load + LRU eviction
    ├── tiling.py         # SAHI-style tile generation + cross-tile NMS
    ├── grounding_dino.py # detect: text-prompt detection
    ├── yolo_world.py     # detect: fast text-prompt detection (Apache YOLO)
    ├── yoloe.py          # detect/segment/find_similar (AGPL — opt-in)
    ├── sam2.py           # segment: box-prompt SAM
    ├── florence2.py      # verify: crop-and-verify VLM
    ├── chat_vlm.py       # ask: free-form Q&A (Qwen-VL family etc.)
    ├── clip_grader.py    # grade + classify: CLIP-cosine rubric
    └── saliency.py       # OpenCV spectral residual (adaptive tiling)

scripts/build_html.py     # Patches VIA HTML from submodule before release
skills/annotate/          # Claude Code skill (SKILL.md + 5 reference files)
docs/design/              # Long-form design docs (AI layer, IO layer)
tests/                    # pytest; no mocks for the I/O paths
```

## Optional extras

The base install is small — just `mcp`, `pillow`, `platformdirs`, `tomli`
(on 3.10). Heavy or specialised deps ride in optional extras:

| Extra | Purpose | Heavyweight deps |
|---|---|---|
| `[ai]` | model registry + GroundingDINO/SAM 2/Florence-2/CLIP/OpenCV | torch, transformers, sahi, opencv-python-headless |
| `[yolo]` | YOLO-World (Apache) + YOLOE (AGPL) | ultralytics |
| `[chat]` | Qwen-VL utils for free-form Q&A | qwen-vl-utils |
| `[io]` | HEIC, PDF, ExifTool metadata | pillow-heif, pdf2image, PyExifTool |
| `[ocr]` | Tesseract OCR | pytesseract |

Several extras also need system packages — see the table in README.md
("System packages") for per-distro install commands.

When an extra isn't installed, the corresponding tools stay advertised on
the MCP surface and return a structured `install_hint`. This is by design:
the LLM can suggest the right `pip install` to the user rather than the
tool disappearing.

## Build (release)

```bash
git submodule update --init --recursive
cd via/via-3.x.y/scripts && python3 pack.py via image_annotator && cd ../../..
python scripts/build_html.py
python -m build
```

## Dev setup

```bash
python -m venv venv && venv/bin/pip install -e ".[dev]"
venv/bin/pytest          # 186+ tests, some skip without optional extras
venv/bin/annotate                # start server
venv/bin/annotate --browser      # start and open browser
venv/bin/annotate --no-ai        # skip the model registry
```

## Tests

Tests use `pytest`. The IO and AI test files (`test_image_io.py`,
`test_models.py`, `test_tools.py`, `test_tiling.py`) skip gracefully when
their optional extras aren't installed. `test_http.py` spins up a real
`HTTPServer` on a random port (no mocks for the protocol layer).

Tests for the model layer use lightweight mock adapters — no torch
required to exercise the handler paths. The few tests that need real
inference are explicitly gated with `pytest.importorskip` or `skipif`.

## Skills

`skills/annotate/SKILL.md` plus five sibling reference files are the
source of truth for what the agent knows about this server:

- `region-encoding.md` — shape table, coordinate spaces, geometry helpers
- `attributes.md` — av schema, description-as-evidence convention
- `perception-gotchas.md` — failure modes that survive overlay checks
- `ai-tools.md` — the `[ai]` tool surface (loaded only when relevant)
- `common-patterns.md` — trigger-phrase cheatsheet

After editing, copy to the user-level install so the live Claude Code
session picks the new version up immediately:

```bash
cp skills/annotate/*.md ~/.claude/skills/annotate/
```

## VIA HTML token

`src/annotate/via_image_annotator.html` is gitignored (generated). It
contains `__VIA_MCP_PORT__` as a literal token; `main()` substitutes the
real port at startup before serving. Run `scripts/build_html.py` after
`git submodule update` to regenerate it.

## Design docs

Long-form rationale and roadmaps for the optional layers live in
`docs/design/`:

- `2026-05-26-via-mcp-server.md` — the original MCP server design
- `2026-05-28-local-model-assistance.md` — AI layer (phases 1-6, all
  shipped on this branch)
- `2026-05-28-format-conversion-and-tooling.md` — IO layer (phases 1-2
  shipped; 3 currently OCR; 4+ deferred)
