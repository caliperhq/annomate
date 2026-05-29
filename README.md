# annomate

MCP server for [VIA v3](https://gitlab.com/vgg/via) — lets Claude Code read
and write image annotations in real time alongside the user.

> Part of [**caliperhq**](https://caliperhq.dev) — open-source tools that
> give Claude senses for the things it builds.

Sister project to [jscad-mcp](https://github.com/caliperhq/jscad-mcp).

## What it does

- Serves the VIA image annotator at a localhost URL
- Implements VIA's push/pull protocol so annotations sync to a local store
- Auto-refreshes the browser when Claude makes changes (no manual pull needed)
- Exposes a suite of MCP tools so Claude can read, add, edit, and delete regions
- **Optional local-model assistance** — open-vocabulary detection (GroundingDINO /
  YOLO-World), promptable segmentation (SAM 2), VLM verification (Florence-2),
  scene classification, annotation grading, and free-form Q&A (Qwen2.5-VL)
- **Optional format conversion** — HEIC / HEIF / AVIF (iPhone photos), PDF
  pages, EXIF / GPS / camera metadata
- **Optional OCR** — Tesseract over image regions, returns word-level boxes
  as detection candidates

All optional features advertise themselves on the MCP tool surface even when
their dependencies aren't installed — they return a structured install hint
instead of erroring.

## Install

### Base (annotation server only)

`annomate` is not on PyPI — install from GitHub:

```bash
python -m venv ~/.local/annomate
~/.local/annomate/bin/pip install \
  'annomate @ git+https://github.com/caliperhq/annomate.git'
```

### Pick the extras you want

```bash
# Local AI models (~3 GB on disk, lazy-downloaded on first use)
pip install 'annomate[ai] @ git+https://github.com/caliperhq/annomate.git'

# Faster detection (YOLO-World, ~95 MB)
pip install 'annomate[ai,yolo] @ git+https://github.com/caliperhq/annomate.git'

# Free-form Q&A via chat VLM (Qwen2.5-VL-3B, ~6 GB)
pip install 'annomate[ai,chat] @ git+https://github.com/caliperhq/annomate.git'

# Format conversion (HEIC, PDF) + EXIF metadata
pip install 'annomate[io] @ git+https://github.com/caliperhq/annomate.git'

# OCR via Tesseract
pip install 'annomate[ocr] @ git+https://github.com/caliperhq/annomate.git'

# Everything
pip install 'annomate[ai,yolo,chat,io,ocr] @ git+https://github.com/caliperhq/annomate.git'
```

## System packages

Several optional features need command-line tools the Python extras can't
install themselves. Install only what you'll use.

| Feature | Tool | Debian/Ubuntu | macOS | Gentoo | Fedora/RHEL | Arch |
|---|---|---|---|---|---|---|
| PDF loading (`[io]`) | `pdftoppm` | `apt install poppler-utils` | `brew install poppler` | `emerge app-text/poppler` | `dnf install poppler-utils` | `pacman -S poppler` |
| OCR (`[ocr]`) | `tesseract` | `apt install tesseract-ocr` | `brew install tesseract` | `emerge app-text/tesseract` | `dnf install tesseract` | `pacman -S tesseract` |
| OCR language packs | `tesseract-<lang>` | `apt install tesseract-ocr-eng tesseract-ocr-spa …` | `brew install tesseract-lang` | set `LINGUAS="en es …"` then re-emerge tesseract | `dnf install tesseract-langpack-eng …` | `pacman -S tesseract-data-eng …` |
| Rich EXIF (`[io]`) | `exiftool` | `apt install libimage-exiftool-perl` | `brew install exiftool` | `emerge media-libs/exiftool` | `dnf install perl-Image-ExifTool` | `pacman -S perl-image-exiftool` |
| AI accelerator (optional) | NVIDIA drivers + CUDA | distribution-specific | n/a (use MPS) | `emerge nvidia-drivers` | `dnf install akmod-nvidia` | `pacman -S nvidia` |

HEIC support (`pillow-heif`) bundles its own `libheif`; no system package is
required.

## Setup

Add to your Claude Code MCP config (`.mcp.json`):

```json
{
  "mcpServers": {
    "annomate": {
      "command": "/path/to/annomate"
    }
  }
}
```

Find your entry point after install:

```bash
which annomate                    # global/user install
~/.local/annomate/bin/annomate    # venv
```

## Usage

```bash
annomate                         # start server (port OS-assigned)
annomate --port 9669             # pin the port
annomate --browser               # open the UI on startup
annomate --no-ai                 # skip the model registry entirely
annomate --models-config FILE    # override ~/.config/annomate/models.toml
```

The local URL prints to stderr on startup. Open it to use the annotator; load
images, draw boxes, then ask Claude about them — or ask Claude to add
annotations directly. Changes appear in your browser within a few seconds.

## Skills

Install the companion Claude Code skill so Claude knows the annotation
workflow, the AI tools, and the trigger-phrase patterns automatically:

```bash
cp -r skills/annomate ~/.claude/skills/
```

The skill is split into a small `SKILL.md` plus sibling reference files
(region-encoding, attributes, perception-gotchas, ai-tools, common-patterns)
— Claude loads only what's relevant to a given request.

## Tool reference (high level)

### Core (always available)

`via_get_annotator_url`, `via_add_file`, `via_get_image`,
`via_get_image_crop`, `via_get_project`, `via_list_files`,
`via_get_annotations`, `via_add_region`, `via_update_region`,
`via_delete_region`, `via_update_project`, `via_save_project`

### Local-model assistance (needs `[ai]`)

`via_model_status`, `via_suggest_regions`, `via_tighten_region`,
`via_verify_region`, `via_grade_annotations`, `via_classify_scene`,
`via_ask_model`, `via_find_similar`

### IO layer (needs `[io]` / `[ocr]`)

`via_load_document` (PDFs), `via_read_metadata`, `via_run_ocr`

See [`skills/annomate/`](skills/annomate/) for the full per-tool guidance,
and [`docs/design/`](docs/design/) for the longer-form design docs.

## License

MIT — see [LICENSE](LICENSE).
VIA is included under its BSD 2-Clause License — see [NOTICE](NOTICE).

YOLOE (an optional adapter) is upstream-licensed AGPL-3.0; if you enable
that pipeline make sure that license fits your deployment context. See
`src/annotate/models/yoloe.py` for the in-tree notice.

## Development

```bash
python -m venv venv
venv/bin/pip install -e ".[dev]"
venv/bin/pytest
venv/bin/annomate --browser
```

The VIA HTML is not committed directly; it's generated from the VIA
submodule and committed to releases:

```bash
git submodule update --init --recursive
cd via/via-3.x.y/scripts && python3 pack.py via image_annotator && cd ../../..
python scripts/build_html.py
python -m build
```
