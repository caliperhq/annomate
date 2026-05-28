import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from annotate.http_handler import make_handler
from annotate.store import ProjectStore


FAKE_HTML = b"<html>port=__VIA_MCP_PORT__</html>"


def _minimal_project():
    return {
        "project": {"pid": "__VIA_PROJECT_ID__", "rev": "__VIA_PROJECT_REV_ID__",
                    "rev_timestamp": "0", "pname": "test"},
        "config": {}, "attribute": {}, "metadata": {},
        "file": {"1": {"fid": 1, "fname": "img.jpg", "type": 2, "loc": 3, "src": "/img.jpg"}},
        "view": {"1": {"fid_list": [1]}},
    }


@pytest.fixture
def server(tmp_path):
    store = ProjectStore(state_file=tmp_path / "state.json")
    html_content = FAKE_HTML.replace(b"__VIA_MCP_PORT__", b"9669")
    handler_cls = make_handler(store, html_content)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield store, port
    httpd.shutdown()


def _get(port, path="/"):
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.read()


def _post(port, path, body):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def _head(port, path):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_get_root_returns_html(server):
    store, port = server
    status, body = _get(port, "/")
    assert status == 200
    assert b"port=9669" in body


def test_post_root_creates_project(server):
    store, port = server
    status, result = _post(port, "/", _minimal_project())
    assert status == 200
    assert "pid" in result
    assert result["rev"] == "1"


def test_get_pid_returns_project(server):
    store, port = server
    _, result = _post(port, "/", _minimal_project())
    pid = result["pid"]
    status, body = _get(port, f"/{pid}")
    assert status == 200
    project = json.loads(body)
    assert project["project"]["pid"] == pid


def test_head_pid_returns_200_when_exists(server):
    store, port = server
    _, result = _post(port, "/", _minimal_project())
    pid = result["pid"]
    assert _head(port, f"/{pid}") == 200


def test_head_unknown_pid_returns_404(server):
    store, port = server
    assert _head(port, "/nonexistent-pid") == 404


def test_get_unknown_pid_returns_404(server):
    store, port = server
    try:
        _get(port, "/nonexistent-pid")
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_post_pid_updates_project(server):
    store, port = server
    _, created = _post(port, "/", _minimal_project())
    pid, rev = created["pid"], created["rev"]
    payload = store.get()
    status, result = _post(port, f"/{pid}?rev={rev}", payload)
    assert status == 200
    assert result["rev"] == "2"
    assert result["pid"] == pid


def test_post_pid_wrong_rev_returns_409(server):
    store, port = server
    _, created = _post(port, "/", _minimal_project())
    pid = created["pid"]
    payload = store.get()
    try:
        _post(port, f"/{pid}?rev=999", payload)
        assert False, "expected 409"
    except urllib.error.HTTPError as e:
        assert e.code == 409


def test_post_pid_wrong_pid_returns_404(server):
    store, port = server
    _, created = _post(port, "/", _minimal_project())
    payload = store.get()
    try:
        _post(port, "/bad-pid?rev=1", payload)
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


# --- image serving ---

@pytest.fixture
def server_with_image(tmp_path):
    store = ProjectStore(state_file=tmp_path / "state.json")
    img_path = tmp_path / "photo.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-data")
    registry = {"photo.jpg": str(img_path)}
    handler_cls = make_handler(store, FAKE_HTML, registry)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield store, port, img_path
    httpd.shutdown()


def test_get_registered_image(server_with_image):
    store, port, img_path = server_with_image
    status, body = _get(port, "/img/photo.jpg")
    assert status == 200
    assert body == img_path.read_bytes()


def test_get_unregistered_image_returns_404(server_with_image):
    store, port, _ = server_with_image
    try:
        _get(port, "/img/missing.jpg")
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


# --- /api/project ---

def test_api_project_no_project(server):
    store, port = server
    status, body = _get(port, "/api/project")
    assert status == 200
    assert json.loads(body) == {"pid": None}


def test_api_project_returns_pid(server):
    store, port = server
    _post(port, "/", _minimal_project())
    status, body = _get(port, "/api/project")
    assert status == 200
    d = json.loads(body)
    assert d["pid"] is not None
    assert d["pid"] == store.get()["project"]["pid"]
