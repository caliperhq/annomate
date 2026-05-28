"""
via-mcp server — HTTP (VIA push/pull) + MCP stdio in one process.
"""

import argparse
import asyncio
import base64
import io
import json
import mimetypes
import os
import random
import string
import sys
import threading
import webbrowser
from http.server import HTTPServer
from importlib.resources import files
from pathlib import Path

import mcp.server
import mcp.server.stdio
import mcp.types as types
import platformdirs

from annotate.store import ProjectStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(s: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=s)]


def _gen_metadata_id(existing: dict) -> str:
    chars = string.ascii_letters + string.digits + "-_"
    while True:
        mid = "".join(random.choices(chars, k=8))
        if mid not in existing:
            return mid


# ---------------------------------------------------------------------------
# Helpers for project structure
# ---------------------------------------------------------------------------

def _minimal_project() -> dict:
    import time
    ts = str(int(time.time()))
    return {
        "project": {
            "pid": "__VIA_PROJECT_ID__",
            "rev": "1",
            "rev_timestamp": ts,
            "pname": "Untitled",
            "data_format_version": "3.1.1",
            "creator": "annotate (https://github.com/caliperhq/annotate)",
            "created": int(time.time() * 1000),
            "vid_list": [],
        },
        "config": {
            "file": {"loc_prefix": {"1": "", "2": "", "3": "", "4": ""}},
            "ui": {
                "file_content_align": "center",
                "file_metadata_editor_visible": True,
                "spatial_metadata_editor_visible": True,
                "temporal_segment_metadata_editor_visible": True,
                "spatial_region_label_attribute_id": "1",
                "gtimeline_visible_row_count": "4",
            },
        },
        # Default schema: label (on-canvas) + description (longer notes).
        # anchor_id "FILE1_Z0_XY1" = spatial region on an image file.
        # type 1 = TEXT. VIA renders attribute "1" as the on-canvas label.
        "attribute": {
            "1": {
                "aname": "label",
                "anchor_id": "FILE1_Z0_XY1",
                "type": 1,
                "desc": "Short region label shown on the canvas",
                "options": {},
                "default_option_id": "",
            },
            "2": {
                "aname": "description",
                "anchor_id": "FILE1_Z0_XY1",
                "type": 1,
                "desc": "Longer annotation notes",
                "options": {},
                "default_option_id": "",
            },
        },
        "file": {},
        "view": {},
        "metadata": {},
    }


def _next_id(mapping: dict) -> int:
    """Return the next integer key (as int) for a VIA file/view dict."""
    return max((int(k) for k in mapping if k.isdigit()), default=0) + 1


# ---------------------------------------------------------------------------
# Tool handlers — take store as first arg for testability
# ---------------------------------------------------------------------------

def handle_get_project(store: ProjectStore) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded. Ask the user to open the VIA annotator and push a project.")
    return _text(json.dumps(project, indent=2))


def handle_list_files(store: ProjectStore) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    file_list = [
        {"id": fid, "name": f["fname"], "type": f["type"]}
        for fid, f in project.get("file", {}).items()
    ]
    return _text(json.dumps(file_list, indent=2))


def handle_get_annotations(
    store: ProjectStore, vid: str | None
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    metadata = project.get("metadata", {})
    if vid is not None:
        metadata = {mid: m for mid, m in metadata.items() if m.get("vid") == vid}
    return _text(json.dumps(metadata, indent=2))


def handle_add_region(
    store: ProjectStore,
    vid: str,
    z: list,
    xy: list,
    av: dict,
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if not isinstance(av, dict):
        return _text(f"'av' must be a dict, got {type(av).__name__}")
    if vid not in project.get("view", {}):
        return _text(f"View '{vid}' not found. Use via_list_files to see available views.")
    mid = _gen_metadata_id(project["metadata"])
    project["metadata"][mid] = {
        "vid": vid,
        "flg": 0,
        "z": z,
        "xy": xy,
        "av": {str(k): str(v) for k, v in av.items()},
    }
    # set_project() is a last-write-wins overwrite; concurrent browser pushes will
    # see ProjectConflictError on next sync and re-pull.
    store.set_project(project)
    return _text(json.dumps({"metadata_id": mid}))


def handle_update_region(
    store: ProjectStore,
    metadata_id: str,
    z: list,
    xy: list,
    av: dict,
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if not isinstance(av, dict):
        return _text(f"'av' must be a dict, got {type(av).__name__}")
    if metadata_id not in project.get("metadata", {}):
        return _text(f"Metadata ID '{metadata_id}' not found.")
    project["metadata"][metadata_id]["z"] = z
    project["metadata"][metadata_id]["xy"] = xy
    project["metadata"][metadata_id]["av"] = {str(k): str(v) for k, v in av.items()}
    store.set_project(project)
    return _text("Updated.")


def handle_delete_region(
    store: ProjectStore, metadata_id: str
) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    if metadata_id not in project.get("metadata", {}):
        return _text(f"Metadata ID '{metadata_id}' not found.")
    del project["metadata"][metadata_id]
    store.set_project(project)
    return _text("Deleted.")


def handle_add_file(
    store: ProjectStore,
    image_registry: dict,
    port: int,
    path: str,
) -> list[types.TextContent]:
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        return _text(f"File not found: {abs_path}")
    suffix = abs_path.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
        return _text(f"Unsupported image format: {suffix}")

    project = store.get()
    if project is None:
        project = _minimal_project()

    # Resolve basename conflicts: if another file already occupies this basename
    # with a different path, prefix with a counter.
    fname = abs_path.name
    if fname in image_registry and image_registry[fname] != str(abs_path):
        stem, ext = abs_path.stem, abs_path.suffix
        counter = 2
        while f"{stem}_{counter}{ext}" in image_registry:
            counter += 1
        fname = f"{stem}_{counter}{ext}"

    image_registry[fname] = str(abs_path)
    src = f"http://localhost:{port}/img/{fname}"

    fid = _next_id(project["file"])
    vid = _next_id(project["view"])
    # abs_path is a via-mcp extension field; VIA ignores unknown keys
    project["file"][str(fid)] = {"fid": fid, "fname": fname, "type": 2, "loc": 2, "src": src, "abs_path": str(abs_path)}
    project["view"][str(vid)] = {"fid_list": [fid]}
    project["project"].setdefault("vid_list", []).append(str(vid))

    store.set_project(project)
    return _text(json.dumps({"fid": str(fid), "vid": str(vid), "url": src}))


def handle_update_project(
    store: ProjectStore, project_json: str
) -> list[types.TextContent]:
    try:
        project = json.loads(project_json)
    except json.JSONDecodeError as e:
        return _text(f"Invalid JSON: {e}")
    required = {"project", "file", "view", "metadata", "attribute"}
    missing = required - set(project.keys())
    if missing:
        return _text(f"Missing required keys: {sorted(missing)}")
    store.set_project(project)
    return _text("Project updated.")


def handle_get_image(
    store: ProjectStore,
    image_registry: dict,
    fid: str,
    max_dim: int = 1024,
) -> list:
    from PIL import Image as PILImage
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    file_entry = project.get("file", {}).get(str(fid))
    if file_entry is None:
        return _text(f"File ID '{fid}' not found. Use via_list_files to see available files.")
    abs_path = file_entry.get("abs_path") or image_registry.get(file_entry.get("fname", ""))
    if not abs_path:
        return _text(f"Image path not available for fid={fid} (file may have been loaded via browser, not via_add_file).")
    from pathlib import Path as _Path
    p = _Path(abs_path)
    if not p.exists():
        return _text(f"Image file not found at {abs_path}")
    with PILImage.open(p) as img:
        orig_w, orig_h = img.size
        orig_fmt = img.format or "JPEG"
        display_w, display_h = orig_w, orig_h
        if max_dim and max(orig_w, orig_h) > max_dim:
            scale = max_dim / max(orig_w, orig_h)
            display_w = round(orig_w * scale)
            display_h = round(orig_h * scale)
            img = img.resize((display_w, display_h), PILImage.LANCZOS)
        if orig_fmt == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format=orig_fmt)
        data = base64.b64encode(buf.getvalue()).decode()
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    header = (
        f"Image fid={fid} fname={file_entry.get('fname')} "
        f"original={orig_w}×{orig_h}px"
        + (f" returned={display_w}×{display_h}px" if (display_w, display_h) != (orig_w, orig_h) else "")
        + ". Coordinates you provide must be in original pixel space."
    )
    return [
        types.TextContent(type="text", text=header),
        types.ImageContent(type="image", data=data, mimeType=mime),
    ]


def handle_save_project(store: ProjectStore, path: str) -> list[types.TextContent]:
    project = store.get()
    if project is None:
        return _text("No project loaded.")
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.parent.exists():
        return _text(f"Directory not found: {p.parent}")
    p.write_text(json.dumps(project, indent=2), encoding="utf-8")
    return _text(f"Saved to {p}")


# ---------------------------------------------------------------------------
# main() and async MCP runner
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="via-mcp: MCP server + VIA image annotator on localhost"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("VIA_MCP_PORT", "0")),
        help="HTTP port (default: 0 = OS-assigned, env: VIA_MCP_PORT)",
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get("VIA_MCP_STATE_FILE"),
        help="Path to project state JSON (env: VIA_MCP_STATE_FILE)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Open browser on startup",
    )
    args = parser.parse_args()

    if args.state_file:
        state_file = Path(args.state_file)
    else:
        state_file = Path(
            platformdirs.user_data_dir("via-mcp", appauthor=False)
        ) / "project_state.json"

    html_template = (
        files("annotate").joinpath("via_image_annotator.html").read_text(encoding="utf-8")
    )

    store = ProjectStore(state_file=state_file)

    from annotate.http_handler import make_handler
    # Bind with port 0 so the OS assigns a free port, then patch html_content
    # with the actual port before the first request is served.
    # Rebuild image registry from persisted project (survives server restart)
    image_registry: dict = {}
    existing = store.get()
    if existing:
        for entry in existing.get("file", {}).values():
            if entry.get("loc") == 2 and entry.get("abs_path") and entry.get("fname"):
                image_registry[entry["fname"]] = entry["abs_path"]

    handler_cls = make_handler(store, b"", image_registry)
    try:
        httpd = HTTPServer(("127.0.0.1", args.port), handler_cls)
    except OSError as e:
        print(f"via-mcp: cannot bind port {args.port}: {e}", file=sys.stderr)
        sys.exit(1)
    actual_port = httpd.server_address[1]
    auto_pull_script = f"""<script>
/* via-mcp: auto-load server project when VIA opens empty */
(async function() {{
  try {{
    if (Object.keys(via.d.store.file).length > 0) return;
    const r = await fetch('http://localhost:{actual_port}/api/project');
    const d = await r.json();
    if (d.pid) via.s.pull(d.pid);
  }} catch(e) {{}}
}})();
</script>"""
    patched = html_template.replace("__VIA_MCP_PORT__", str(actual_port))
    html_content = patched.replace("</body>", auto_pull_script + "\n</body>").encode("utf-8")
    handler_cls.html_content = html_content

    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    annotator_url = f"http://localhost:{actual_port}/"
    print(f"VIA annotator: {annotator_url}", file=sys.stderr)

    if args.browser:
        webbrowser.open(annotator_url)

    asyncio.run(_run_mcp(store, annotator_url, actual_port, image_registry))


async def _run_mcp(store: ProjectStore, annotator_url: str, port: int, image_registry: dict) -> None:
    mcp_server = mcp.server.Server("via-mcp")

    @mcp_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="via_get_annotator_url",
                description="Return the URL of the live VIA annotator. Share with the user so they can open it.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_add_file",
                description=(
                    "Load a local image file into the VIA project so it appears in the annotator. "
                    "Creates a file + view entry and serves the image via HTTP. "
                    "If no project is loaded yet, creates a new empty one. "
                    "Returns {fid, vid, url}."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to the image file"},
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="via_get_project",
                description="Return the full VIA project JSON (files, views, metadata, attributes).",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_list_files",
                description="List all image files in the project (id, name, type). Lightweight — use before via_get_project.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="via_get_annotations",
                description="Return all annotation metadata, optionally filtered by view ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vid": {"type": "string", "description": "View ID to filter by (omit for all)"},
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="via_add_region",
                description=(
                    "Add one annotation region. xy encoding: rectangle=[2,x,y,w,h], "
                    "point=[1,x,y], circle=[3,cx,cy,r], polygon=[7,x1,y1,x2,y2,...]. "
                    "Coordinates are image pixel space. av values must be strings."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vid": {"type": "string", "description": "View ID"},
                        "z": {"type": "array", "items": {"type": "number"}, "description": "Temporal coords ([] for images)"},
                        "xy": {"type": "array", "items": {"type": "number"}, "description": "[shape_id, ...coords]"},
                        "av": {"type": "object", "description": "Attribute key-value pairs (strings)", "additionalProperties": {"type": "string"}},
                    },
                    "required": ["vid", "z", "xy", "av"],
                },
            ),
            types.Tool(
                name="via_update_region",
                description="Replace an existing annotation region's z, xy, and av fields.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {"type": "string"},
                        "z": {"type": "array", "items": {"type": "number"}},
                        "xy": {"type": "array", "items": {"type": "number"}},
                        "av": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                    "required": ["metadata_id", "z", "xy", "av"],
                },
            ),
            types.Tool(
                name="via_delete_region",
                description="Remove an annotation region by its metadata ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "metadata_id": {"type": "string"},
                    },
                    "required": ["metadata_id"],
                },
            ),
            types.Tool(
                name="via_update_project",
                description="Replace the full project JSON. Use for bulk changes. project_json must be a valid VIA project JSON string.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_json": {"type": "string"},
                    },
                    "required": ["project_json"],
                },
            ),
            types.Tool(
                name="via_get_image",
                description=(
                    "Return the image so you can see what you are annotating. "
                    "Always call this before adding regions — you need to see the image to place accurate coordinates. "
                    "Returns the original pixel dimensions (use these for all coordinates) plus the image, "
                    "downscaled to max_dim on the longest edge for context efficiency."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fid": {"type": "string", "description": "File ID from via_list_files or via_add_file"},
                        "max_dim": {"type": "integer", "description": "Limit longest edge to this many pixels for context efficiency (default 1024)", "default": 1024},
                    },
                    "required": ["fid"],
                },
            ),
            types.Tool(
                name="via_save_project",
                description="Write the current project JSON to a file on disk. Use when the user asks to save or export their work.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to write, e.g. /home/user/project.json"},
                    },
                    "required": ["path"],
                },
            ),
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        arguments = arguments or {}
        try:
            if name == "via_add_file":
                return handle_add_file(store, image_registry, port, arguments["path"])
            if name == "via_get_annotator_url":
                return _text(annotator_url)
            if name == "via_get_project":
                return handle_get_project(store)
            if name == "via_list_files":
                return handle_list_files(store)
            if name == "via_get_annotations":
                return handle_get_annotations(store, vid=arguments.get("vid"))
            if name == "via_add_region":
                return handle_add_region(
                    store,
                    vid=arguments["vid"],
                    z=arguments["z"],
                    xy=arguments["xy"],
                    av=arguments["av"],
                )
            if name == "via_update_region":
                return handle_update_region(
                    store,
                    metadata_id=arguments["metadata_id"],
                    z=arguments["z"],
                    xy=arguments["xy"],
                    av=arguments["av"],
                )
            if name == "via_delete_region":
                return handle_delete_region(store, metadata_id=arguments["metadata_id"])
            if name == "via_update_project":
                return handle_update_project(store, project_json=arguments["project_json"])
            if name == "via_get_image":
                return handle_get_image(
                    store, image_registry,
                    fid=arguments["fid"],
                    max_dim=int(arguments.get("max_dim", 1024)),
                )
            if name == "via_save_project":
                return handle_save_project(store, path=arguments["path"])
            return _text(f"Unknown tool: {name}")
        except KeyError as e:
            return _text(f"Missing required argument: {e}")

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
