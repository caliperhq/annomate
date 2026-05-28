from pathlib import Path
import json

import pytest
import mcp.types as types

from via_mcp.store import ProjectStore
from via_mcp.server import (
    handle_get_project,
    handle_list_files,
    handle_get_annotations,
    handle_add_region,
    handle_update_region,
    handle_delete_region,
    handle_update_project,
    handle_add_file,
)


def _text(result: list[types.TextContent]) -> str:
    return result[0].text


@pytest.fixture
def store(tmp_path):
    return ProjectStore(state_file=tmp_path / "state.json")


@pytest.fixture
def loaded_store(store):
    project = {
        "project": {"pid": "__VIA_PROJECT_ID__", "rev": "__VIA_PROJECT_REV_ID__",
                    "rev_timestamp": "0", "pname": "test"},
        "config": {}, "attribute": {},
        "file": {
            "1": {"fid": 1, "fname": "cat.jpg", "type": 2, "loc": 3, "src": "/cat.jpg"},
            "2": {"fid": 2, "fname": "dog.jpg", "type": 2, "loc": 3, "src": "/dog.jpg"},
        },
        "view": {
            "1": {"fid_list": [1]},
            "2": {"fid_list": [2]},
        },
        "metadata": {
            "abc12345": {"vid": "1", "flg": 0, "z": [], "xy": [2, 10, 20, 50, 30], "av": {"1": "cat"}},
        },
    }
    store.create(project)
    return store


# --- via_get_project ---

def test_get_project_no_project(store):
    result = handle_get_project(store)
    assert "No project" in _text(result)


def test_get_project_returns_json(loaded_store):
    result = handle_get_project(loaded_store)
    data = json.loads(_text(result))
    assert "file" in data
    assert "metadata" in data


# --- via_list_files ---

def test_list_files_no_project(store):
    result = handle_list_files(store)
    assert "No project" in _text(result)


def test_list_files_returns_file_list(loaded_store):
    result = handle_list_files(loaded_store)
    files = json.loads(_text(result))
    assert len(files) == 2
    names = {f["name"] for f in files}
    assert names == {"cat.jpg", "dog.jpg"}
    assert all("id" in f and "type" in f for f in files)


# --- via_get_annotations ---

def test_get_annotations_no_project(store):
    result = handle_get_annotations(store, vid=None)
    assert "No project" in _text(result)


def test_get_annotations_returns_all(loaded_store):
    result = handle_get_annotations(loaded_store, vid=None)
    data = json.loads(_text(result))
    assert "abc12345" in data


def test_get_annotations_filtered_by_vid(loaded_store):
    result = handle_get_annotations(loaded_store, vid="2")
    data = json.loads(_text(result))
    assert "abc12345" not in data  # belongs to vid="1"
    assert data == {}


# --- via_add_region ---

def test_add_region_no_project(store):
    result = handle_add_region(store, vid="1", z=[], xy=[2, 0, 0, 10, 10], av={})
    assert "No project" in _text(result)


def test_add_region_unknown_vid(loaded_store):
    result = handle_add_region(loaded_store, vid="99", z=[], xy=[2, 0, 0, 10, 10], av={})
    assert "not found" in _text(result).lower()


def test_add_region_returns_metadata_id(loaded_store):
    result = handle_add_region(loaded_store, vid="1", z=[], xy=[2, 5, 5, 20, 20], av={"label": "new"})
    data = json.loads(_text(result))
    assert "metadata_id" in data
    mid = data["metadata_id"]
    project = loaded_store.get()
    assert mid in project["metadata"]
    assert project["metadata"][mid]["xy"] == [2, 5, 5, 20, 20]


def test_add_region_bumps_revision(loaded_store):
    rev_before = loaded_store.get()["project"]["rev"]
    handle_add_region(loaded_store, vid="1", z=[], xy=[1, 0, 0], av={})
    rev_after = loaded_store.get()["project"]["rev"]
    assert int(rev_after) > int(rev_before)


def test_add_region_coerces_av_to_strings(loaded_store):
    handle_add_region(loaded_store, vid="1", z=[], xy=[2, 0, 0, 10, 10], av={"score": 42})
    project = loaded_store.get()
    metas = list(project["metadata"].values())
    new_meta = next(m for m in metas if m.get("av", {}).get("score") is not None)
    assert new_meta["av"]["score"] == "42"


# --- via_update_region ---

def test_update_region_no_project(store):
    result = handle_update_region(store, metadata_id="x", z=[], xy=[], av={})
    assert "No project" in _text(result)


def test_update_region_unknown_id(loaded_store):
    result = handle_update_region(loaded_store, metadata_id="unknown", z=[], xy=[], av={})
    assert "not found" in _text(result).lower()


def test_update_region_modifies_metadata(loaded_store):
    handle_update_region(loaded_store, metadata_id="abc12345",
                         z=[], xy=[2, 1, 2, 3, 4], av={"label": "updated"})
    project = loaded_store.get()
    assert project["metadata"]["abc12345"]["xy"] == [2, 1, 2, 3, 4]
    assert project["metadata"]["abc12345"]["av"]["label"] == "updated"


def test_update_region_bumps_revision(loaded_store):
    rev_before = loaded_store.get()["project"]["rev"]
    handle_update_region(loaded_store, metadata_id="abc12345", z=[], xy=[], av={})
    rev_after = loaded_store.get()["project"]["rev"]
    assert int(rev_after) > int(rev_before)


# --- via_delete_region ---

def test_delete_region_no_project(store):
    result = handle_delete_region(store, metadata_id="x")
    assert "No project" in _text(result)


def test_delete_region_unknown_id(loaded_store):
    result = handle_delete_region(loaded_store, metadata_id="unknown")
    assert "not found" in _text(result).lower()


def test_delete_region_removes_metadata(loaded_store):
    handle_delete_region(loaded_store, metadata_id="abc12345")
    project = loaded_store.get()
    assert "abc12345" not in project["metadata"]


def test_delete_region_bumps_revision(loaded_store):
    rev_before = loaded_store.get()["project"]["rev"]
    handle_delete_region(loaded_store, metadata_id="abc12345")
    rev_after = loaded_store.get()["project"]["rev"]
    assert int(rev_after) > int(rev_before)


# --- via_update_project ---

def test_update_project_invalid_json(loaded_store):
    result = handle_update_project(loaded_store, "not json")
    assert "Invalid JSON" in _text(result)


def test_update_project_missing_keys(loaded_store):
    result = handle_update_project(loaded_store, json.dumps({"project": {}}))
    assert "Missing" in _text(result)


def test_update_project_replaces_project(loaded_store):
    project = loaded_store.get()
    project["metadata"]["new_key"] = {"vid": "1", "flg": 0, "z": [], "xy": [], "av": {}}
    handle_update_project(loaded_store, json.dumps(project))
    updated = loaded_store.get()
    assert "new_key" in updated["metadata"]


def test_update_project_bumps_revision(loaded_store):
    rev_before = loaded_store.get()["project"]["rev"]
    project = loaded_store.get()
    handle_update_project(loaded_store, json.dumps(project))
    rev_after = loaded_store.get()["project"]["rev"]
    assert int(rev_after) > int(rev_before)


# --- via_add_file ---

def test_add_file_missing_path(store):
    result = handle_add_file(store, {}, 9669, "/nonexistent/file.jpg")
    assert "not found" in _text(result).lower()


def test_add_file_unsupported_extension(store, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    result = handle_add_file(store, {}, 9669, str(f))
    assert "unsupported" in _text(result).lower()


def test_add_file_creates_project_if_none(store, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"\xff\xd8fake")
    registry = {}
    result = handle_add_file(store, registry, 9669, str(img))
    data = json.loads(_text(result))
    assert "fid" in data and "vid" in data and "url" in data
    project = store.get()
    assert project is not None
    assert str(data["fid"]) in project["file"]
    assert str(data["vid"]) in project["view"]


def test_add_file_registers_image_for_serving(store, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"\xff\xd8fake")
    registry = {}
    handle_add_file(store, registry, 9669, str(img))
    assert "cat.jpg" in registry
    assert registry["cat.jpg"] == str(img)


def test_add_file_src_uses_http_url(store, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"\xff\xd8fake")
    result = handle_add_file(store, {}, 9669, str(img))
    data = json.loads(_text(result))
    assert data["url"].startswith("http://localhost:9669/img/")
    project = store.get()
    fid = data["fid"]
    file_entry = project["file"][fid]
    assert file_entry["loc"] == 2
    assert file_entry["src"] == data["url"]


def test_add_file_appends_to_existing_project(loaded_store, tmp_path):
    img = tmp_path / "newimg.jpg"
    img.write_bytes(b"\xff\xd8fake")
    result = handle_add_file(loaded_store, {}, 9669, str(img))
    data = json.loads(_text(result))
    project = loaded_store.get()
    assert len(project["file"]) == 3  # 2 from fixture + 1 new
    assert str(data["fid"]) in project["file"]


def test_add_file_stores_abs_path_in_project(store, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"\xff\xd8fake")
    handle_add_file(store, {}, 9669, str(img))
    project = store.get()
    file_entry = next(iter(project["file"].values()))
    assert file_entry["abs_path"] == str(img)


def test_add_file_populates_vid_list(store, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"\xff\xd8fake")
    result = handle_add_file(store, {}, 9669, str(img))
    data = json.loads(_text(result))
    project = store.get()
    assert data["vid"] in project["project"]["vid_list"]


def test_add_file_resolves_basename_conflict(store, tmp_path):
    dir1 = tmp_path / "a"
    dir2 = tmp_path / "b"
    dir1.mkdir(); dir2.mkdir()
    img1 = dir1 / "photo.jpg"
    img2 = dir2 / "photo.jpg"
    img1.write_bytes(b"\xff\xd8one")
    img2.write_bytes(b"\xff\xd8two")
    registry = {}
    handle_add_file(store, registry, 9669, str(img1))
    handle_add_file(store, registry, 9669, str(img2))
    assert len(registry) == 2
    assert "photo.jpg" in registry
    assert "photo_2.jpg" in registry
