import json
import mimetypes
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler

from annotate.store import ProjectNotFoundError, ProjectConflictError, ProjectStore


class VIAHandler(BaseHTTPRequestHandler):
    store: ProjectStore
    html_content: bytes
    image_registry: dict  # basename → absolute path; populated by via_add_file

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.strip("/")
        if path == "":
            self._respond(200, "text/html; charset=utf-8", self.html_content)
            return
        if path == "api/project":
            project = self.store.get()
            pid = project["project"]["pid"] if project else None
            self._respond(200, "application/json", json.dumps({"pid": pid}).encode("utf-8"))
            return
        if path.startswith("img/"):
            self._serve_image(path[len("img/"):])
            return
        project = self.store.get_if_exists(path)
        if project is None:
            self._respond(404, "text/plain", b"Not found")
            return
        self._respond(200, "application/json", json.dumps(project).encode("utf-8"))

    def _serve_image(self, basename: str) -> None:
        abs_path = self.image_registry.get(basename)
        if abs_path is None:
            self._respond(404, "text/plain", b"Not found")
            return
        # Translate non-browser-native formats to a cached JPEG, on disk
        # in platformdirs.user_cache_dir. PIL-native browser formats
        # (jpg/png/gif/webp) get the original path back unchanged.
        try:
            from annotate import image_io
            serve_path = image_io.cached_browser_path(abs_path)
        except (LookupError, ImportError):
            serve_path = abs_path
        try:
            with open(serve_path, "rb") as f:
                data = f.read()
        except OSError:
            self._respond(404, "text/plain", b"Not found")
            return
        mime = mimetypes.guess_type(str(serve_path))[0] or "application/octet-stream"
        self._respond(200, mime, data)

    def do_HEAD(self):
        path = urllib.parse.urlparse(self.path).path.strip("/")
        code = 200 if self.store.exists(path) else 404
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_POST(self):
        length = max(0, int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, "text/plain", b"Invalid JSON")
            return

        parsed = urllib.parse.urlparse(self.path)
        pid = parsed.path.strip("/")
        params = urllib.parse.parse_qs(parsed.query)

        if pid == "":
            result = self.store.create(payload)
            self._respond(200, "application/json", json.dumps(result).encode("utf-8"))
            return

        rev = params.get("rev", [None])[0]
        try:
            result = self.store.update_from_browser(pid, rev, payload)
        except ProjectNotFoundError:
            self._respond(404, "text/plain", b"Not found")
            return
        except ProjectConflictError:
            self._respond(409, "text/plain", b"Conflict: pull first")
            return
        self._respond(200, "application/json", json.dumps(result).encode("utf-8"))

    def _respond(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress per-request logging


def make_handler(store: ProjectStore, html_content: bytes, image_registry: dict | None = None) -> type:
    class Handler(VIAHandler):
        pass
    Handler.store = store
    Handler.html_content = html_content
    Handler.image_registry = image_registry if image_registry is not None else {}
    return Handler
