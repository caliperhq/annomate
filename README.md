# annotate

MCP server for [VIA v3](https://gitlab.com/vgg/via) — lets Claude Code read and write image annotations in real time alongside the user.

> Part of [**caliperhq**](https://caliperhq.dev) — open-source tools that give Claude senses for the things it builds.

Sister project to [jscad-mcp](https://github.com/caliperhq/jscad-mcp).

## What it does

- Serves the VIA image annotator at `http://localhost:9669/`
- Implements VIA's push/pull protocol so annotations sync to a local store
- Auto-refreshes the browser when Claude makes changes (no manual pull needed)
- Exposes MCP tools so Claude can read, add, edit, and delete regions

## Install

```bash
# Recommended: venv
python -m venv ~/.local/annotate
~/.local/annotate/bin/pip install annotate

# Or: global user install
pip install --user annotate
```

## Setup

Add to your Claude Code MCP config (`.mcp.json`):

```json
{
  "mcpServers": {
    "via": {
      "command": "/path/to/annotate"
    }
  }
}
```

Find your entry point after install:

```bash
which annotate                    # global/user install
~/.local/annotate/bin/annotate    # venv
```

## Usage

```bash
annotate           # starts server, opens browser automatically
annotate --port 9669 --no-browser
```

Open `http://localhost:9669/` to use the annotator. Load images, draw
annotations, then ask Claude about them — or ask Claude to add annotations
directly. Changes appear in your browser within a few seconds.

## Skills

Install the companion Claude Code skill so Claude knows the annotation
workflow and JSON format automatically:

```bash
cp -r skills/via-annotator ~/.claude/skills/
```

See [skills/README.md](skills/README.md) for details.

## License

MIT — see [LICENSE](LICENSE).
VIA is included under its BSD 2-Clause License — see [NOTICE](NOTICE).

## Development

The VIA HTML is not committed directly; it is generated from the VIA
submodule and committed to releases:

```bash
git submodule update --init --recursive   # fetch VIA source
cd via/via-3.x.y/scripts && python3 pack.py via image_annotator && cd ../../..
python scripts/build_html.py              # patch and copy HTML
python -m build                           # build wheel
```
