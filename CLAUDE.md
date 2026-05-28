# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## What this is

`via-mcp` is a Python MCP server that wraps VIA v3 (VGG Image Annotator).
It runs two interfaces in one process:
- HTTP on port 9669: implements VIA's push/pull share protocol + serves patched HTML
- MCP stdio: exposes 7 annotation tools to Claude Code

Sister project to [jscad-mcp](https://github.com/caliperhq/jscad-mcp).

## Layout

- `src/via_mcp/store.py` — `ProjectStore`: in-memory + disk-persisted annotation state
- `src/via_mcp/http_handler.py` — `VIAHandler` + `make_handler()`: VIA REST protocol
- `src/via_mcp/server.py` — tool handler functions + `main()` entry point
- `scripts/build_html.py` — patches VIA HTML from submodule before release
- `skills/via-annotator/SKILL.md` — Claude Code skill for the annotation workflow

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
venv/bin/pytest          # run all tests
venv/bin/annotate --no-browser   # start server
```

## Tests

Tests use `pytest`. No mocks — `test_http.py` spins up a real `HTTPServer` on a random port.

## VIA HTML token

`src/via_mcp/via_image_annotator.html` is gitignored (generated). It contains
`__VIA_MCP_PORT__` as a literal token; `main()` substitutes the real port at startup
before serving. Run `scripts/build_html.py` after `git submodule update` to regenerate it.
