from pathlib import Path
import json

import pytest
import mcp.types as types

from annotate.store import ProjectStore
from annotate.server import (
    handle_get_project,
    handle_list_files,
    handle_get_annotations,
    handle_add_region,
    handle_update_region,
    handle_delete_region,
    handle_update_project,
    handle_add_file,
    handle_get_image,
    handle_save_project,
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
    result = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10], av={})
    assert "No project" in _text(result)


def test_add_region_unknown_vid(loaded_store):
    result = handle_add_region(loaded_store, {}, vid="99", z=[], xy=[2, 0, 0, 10, 10], av={})
    assert "not found" in _text(result).lower()


def test_add_region_returns_metadata_id(loaded_store):
    result = handle_add_region(loaded_store, {}, vid="1", z=[], xy=[2, 5, 5, 20, 20], av={"label": "new"})
    data = json.loads(_text(result))
    assert "metadata_id" in data
    mid = data["metadata_id"]
    project = loaded_store.get()
    assert mid in project["metadata"]
    assert project["metadata"][mid]["xy"] == [2, 5, 5, 20, 20]


def test_add_region_bumps_revision(loaded_store):
    rev_before = loaded_store.get()["project"]["rev"]
    handle_add_region(loaded_store, {}, vid="1", z=[], xy=[1, 0, 0], av={})
    rev_after = loaded_store.get()["project"]["rev"]
    assert int(rev_after) > int(rev_before)


def test_add_region_coerces_av_to_strings(loaded_store):
    handle_add_region(loaded_store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10], av={"score": 42})
    project = loaded_store.get()
    metas = list(project["metadata"].values())
    new_meta = next(m for m in metas if m.get("av", {}).get("score") is not None)
    assert new_meta["av"]["score"] == "42"


# --- via_update_region ---

def test_update_region_no_project(store):
    result = handle_update_region(store, {}, metadata_id="x", z=[], xy=[], av={})
    assert "No project" in _text(result)


def test_update_region_unknown_id(loaded_store):
    result = handle_update_region(loaded_store, {}, metadata_id="unknown", z=[], xy=[], av={})
    assert "not found" in _text(result).lower()


def test_update_region_modifies_metadata(loaded_store):
    handle_update_region(loaded_store, {}, metadata_id="abc12345",
                         z=[], xy=[2, 1, 2, 3, 4], av={"label": "updated"})
    project = loaded_store.get()
    assert project["metadata"]["abc12345"]["xy"] == [2, 1, 2, 3, 4]
    assert project["metadata"]["abc12345"]["av"]["label"] == "updated"


def test_update_region_bumps_revision(loaded_store):
    rev_before = loaded_store.get()["project"]["rev"]
    handle_update_region(loaded_store, {}, metadata_id="abc12345", z=[], xy=[], av={})
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


# --- default attribute schema ---

def test_new_project_has_default_attributes(store, tmp_path):
    from annotate.server import _minimal_project
    p = _minimal_project()
    assert "1" in p["attribute"]
    assert p["attribute"]["1"]["aname"] == "label"
    assert "2" in p["attribute"]
    assert p["attribute"]["2"]["aname"] == "description"
    assert p["config"]["ui"]["spatial_region_label_attribute_id"] == "1"


def test_add_file_creates_project_with_default_attributes(store, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"\xff\xd8fake")
    handle_add_file(store, {}, 9669, str(img))
    project = store.get()
    assert "1" in project["attribute"]
    assert project["attribute"]["1"]["aname"] == "label"


# --- via_get_image ---

@pytest.fixture
def store_with_image(store, tmp_path):
    from PIL import Image as PILImage
    img_path = tmp_path / "test.jpg"
    PILImage.new("RGB", (200, 100), color=(255, 0, 0)).save(img_path, "JPEG")
    registry = {}
    handle_add_file(store, registry, 9669, str(img_path))
    return store, registry, img_path


def test_get_image_no_project(store):
    result = handle_get_image(store, {}, fid="1")
    assert "No project" in _text(result)


def test_get_image_unknown_fid(loaded_store):
    result = handle_get_image(loaded_store, {}, fid="99")
    assert "not found" in _text(result).lower()


def test_get_image_returns_image_content(store_with_image):
    import mcp.types as types
    store, registry, img_path = store_with_image
    result = handle_get_image(store, registry, fid="1")
    assert len(result) == 2
    assert isinstance(result[0], types.TextContent)
    assert "200×100" in result[0].text
    assert isinstance(result[1], types.ImageContent)
    assert result[1].mimeType == "image/jpeg"
    assert len(result[1].data) > 0


def test_get_image_downscales(store_with_image):
    store, registry, img_path = store_with_image
    result = handle_get_image(store, registry, fid="1", max_dim=50)
    assert "200×100" in result[0].text   # original dims always reported
    assert "returned=50×25" in result[0].text


def test_get_image_no_downscale_when_small(store_with_image):
    store, registry, img_path = store_with_image
    result = handle_get_image(store, registry, fid="1", max_dim=1024)
    assert "returned=" not in result[0].text   # image fits, no resize note


# --- via_save_project ---

def test_save_project_no_project(store, tmp_path):
    result = handle_save_project(store, str(tmp_path / "out.json"))
    assert "No project" in _text(result)


def test_save_project_bad_dir(loaded_store, tmp_path):
    result = handle_save_project(loaded_store, "/nonexistent/dir/out.json")
    assert "not found" in _text(result).lower()


def test_save_project_writes_json(loaded_store, tmp_path):
    out = tmp_path / "out.json"
    result = handle_save_project(loaded_store, str(out))
    assert "Saved" in _text(result)
    data = json.loads(out.read_text())
    assert "metadata" in data
    assert "file" in data


# --- av key validation & aname remapping ---

@pytest.fixture
def store_with_schema(store, tmp_path):
    from PIL import Image as PILImage
    img_path = tmp_path / "x.jpg"
    PILImage.new("RGB", (1000, 500), color=(0, 0, 0)).save(img_path, "JPEG")
    handle_add_file(store, {}, 9669, str(img_path))  # populates default schema
    return store, img_path


def test_add_region_remaps_aname_to_id(store_with_schema):
    store, _ = store_with_schema
    result = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10],
                               av={"label": "cat", "description": "tabby"})
    data = json.loads(_text(result))
    assert data["label"] == "cat"
    mid = data["metadata_id"]
    project = store.get()
    av = project["metadata"][mid]["av"]
    assert av == {"1": "cat", "2": "tabby"}  # remapped to numeric IDs


def test_add_region_rejects_unknown_av_key(store_with_schema):
    store, _ = store_with_schema
    result = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10],
                               av={"bogus": "value"})
    text = _text(result)
    assert "Unknown" in text
    assert "bogus" in text
    # nothing should have been written
    project = store.get()
    assert project["metadata"] == {}


def test_add_region_accepts_numeric_id_directly(store_with_schema):
    store, _ = store_with_schema
    result = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10],
                               av={"1": "fish"})
    data = json.loads(_text(result))
    assert data["label"] == "fish"


def test_update_region_remaps_aname(store_with_schema):
    store, _ = store_with_schema
    add = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10],
                            av={"label": "first"})
    mid = json.loads(_text(add))["metadata_id"]
    handle_update_region(store, {}, metadata_id=mid, z=[], xy=[2, 1, 1, 5, 5],
                         av={"label": "second"})
    project = store.get()
    assert project["metadata"][mid]["av"]["1"] == "second"


# --- xy_space: fraction ---

def test_add_region_fraction_scales_rect(store_with_schema):
    store, _ = store_with_schema  # image is 1000×500
    add = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0.25, 0.5, 0.5, 0.25],
                            av={"label": "rect"}, xy_space="fraction")
    mid = json.loads(_text(add))["metadata_id"]
    project = store.get()
    # 0.25*1000=250, 0.5*500=250, 0.5*1000=500, 0.25*500=125
    assert project["metadata"][mid]["xy"] == [2, 250.0, 250.0, 500.0, 125.0]


def test_add_region_fraction_scales_polygon(store_with_schema):
    store, _ = store_with_schema  # 1000×500
    add = handle_add_region(store, {}, vid="1", z=[],
                            xy=[7, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                            av={"label": "poly"}, xy_space="fraction")
    mid = json.loads(_text(add))["metadata_id"]
    project = store.get()
    assert project["metadata"][mid]["xy"] == [7, 100.0, 100.0, 300.0, 200.0, 500.0, 300.0]


def test_add_region_bad_xy_space(store_with_schema):
    store, _ = store_with_schema
    result = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 1, 1],
                               av={"label": "x"}, xy_space="garbage")
    assert "xy_space" in _text(result)


# --- echo label in via_add_region response ---

def test_add_region_echoes_label(store_with_schema):
    store, _ = store_with_schema
    result = handle_add_region(store, {}, vid="1", z=[], xy=[2, 0, 0, 10, 10],
                               av={"label": "jaguar"})
    data = json.loads(_text(result))
    assert data["label"] == "jaguar"


# --- via_get_image overlay ---

def test_get_image_overlay_draws_without_error(store_with_schema):
    store, _ = store_with_schema
    handle_add_region(store, {}, vid="1", z=[], xy=[2, 10, 10, 50, 50], av={"label": "box"})
    handle_add_region(store, {}, vid="1", z=[], xy=[1, 200, 100], av={"label": "pt"})
    handle_add_region(store, {}, vid="1", z=[],
                      xy=[7, 300, 300, 400, 350, 350, 400], av={"label": "poly"})
    result = handle_get_image(store, {}, fid="1", overlay=True)
    assert len(result) == 2
    assert "overlay=on" in result[0].text
    assert isinstance(result[1], types.ImageContent)
    assert len(result[1].data) > 0


def test_get_image_no_overlay_by_default(store_with_schema):
    store, _ = store_with_schema
    result = handle_get_image(store, {}, fid="1")
    assert "overlay=on" not in result[0].text


# --- stale src URL heal ---

def test_main_heals_stale_src_urls(tmp_path, monkeypatch):
    """Simulate a persisted project whose file entries reference a dead port,
    then verify the heal step rewrites src to the current port without touching abs_path.
    """
    from PIL import Image as PILImage
    from annotate.store import ProjectStore

    img = tmp_path / "stale.jpg"
    PILImage.new("RGB", (32, 32), color=(0, 255, 0)).save(img, "JPEG")

    state_file = tmp_path / "state.json"
    s1 = ProjectStore(state_file=state_file)
    handle_add_file(s1, {}, 11111, str(img))  # bogus old port
    persisted = s1.get()
    assert persisted["file"]["1"]["src"] == "http://localhost:11111/img/stale.jpg"

    # Simulate the heal block in main() running with a new port
    import re as _re
    actual_port = 22222
    existing = s1.get()
    changed = False
    for entry in existing.get("file", {}).values():
        src = entry.get("src", "") or ""
        fname = entry.get("fname", "")
        if entry.get("loc") == 2 and fname and _re.match(r"^http://localhost:\d+/img/", src):
            expected = f"http://localhost:{actual_port}/img/{fname}"
            if src != expected:
                entry["src"] = expected
                changed = True
    assert changed
    s1.set_project(existing)

    after = s1.get()
    assert after["file"]["1"]["src"] == "http://localhost:22222/img/stale.jpg"
    assert after["file"]["1"]["abs_path"] == str(img)  # preserved
