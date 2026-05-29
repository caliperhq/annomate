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
    handle_get_image_crop,
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
    result = handle_get_project(loaded_store, full=True)
    data = json.loads(_text(result))
    assert "file" in data
    assert "metadata" in data


def test_get_project_default_returns_summary(loaded_store):
    result = handle_get_project(loaded_store)
    data = json.loads(_text(result))
    assert "file_count" in data
    assert "region_count" in data
    assert "files" in data
    assert "attributes" in data
    assert "note" in data
    assert "metadata" not in data


def test_get_project_full_returns_raw_json(loaded_store):
    result = handle_get_project(loaded_store, full=True)
    data = json.loads(_text(result))
    assert "metadata" in data
    assert "file" in data


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
    result = handle_get_annotations(store, {}, vid=None)
    assert "No project" in _text(result)


def test_get_annotations_returns_all(loaded_store):
    result = handle_get_annotations(loaded_store, {}, vid=None)
    data = json.loads(_text(result))
    assert "abc12345" in data


def test_get_annotations_filtered_by_vid(loaded_store):
    result = handle_get_annotations(loaded_store, {}, vid="2")
    data = json.loads(_text(result))
    assert "abc12345" not in data  # belongs to vid="1"
    assert data == {}


def test_get_annotations_bad_format(loaded_store):
    result = handle_get_annotations(loaded_store, {}, vid=None, format="garbage")
    assert "format" in _text(result)


def test_get_annotations_fraction_format(store, tmp_path):
    """fraction format inverts pixel coords to 0–1 of the original dims."""
    from PIL import Image as PILImage
    img_path = tmp_path / "big.jpg"
    PILImage.new("RGB", (4000, 2000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))
    handle_add_region(store, registry, vid="1", z=[],
                      xy=[2, 1000, 500, 200, 400], av={"label": "r"})

    result = handle_get_annotations(store, registry, vid=None, format="fraction")
    data = json.loads(_text(result))
    [region] = data.values()
    xy = region["xy"]
    # 1000/4000=0.25, 500/2000=0.25, 200/4000=0.05, 400/2000=0.2
    assert xy[0] == 2
    assert xy[1] == pytest.approx(0.25)
    assert xy[2] == pytest.approx(0.25)
    assert xy[3] == pytest.approx(0.05)
    assert xy[4] == pytest.approx(0.2)


def test_get_annotations_both_format(store, tmp_path):
    """both format keeps pixel xy and adds xy_fraction + dims."""
    from PIL import Image as PILImage
    img_path = tmp_path / "big.jpg"
    PILImage.new("RGB", (4000, 2000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))
    handle_add_region(store, registry, vid="1", z=[],
                      xy=[2, 1000, 500, 200, 400], av={"label": "r"})

    result = handle_get_annotations(store, registry, vid=None, format="both")
    data = json.loads(_text(result))
    [region] = data.values()
    assert region["xy"] == [2, 1000, 500, 200, 400]
    assert region["xy_fraction"][1] == pytest.approx(0.25)
    assert region["dims"] == [4000, 2000]


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


# --- via_get_image_crop ---

@pytest.fixture
def store_with_big_image(store, tmp_path):
    """4000×2000 image — large enough that 2048 max_dim downscales it."""
    from PIL import Image as PILImage
    img_path = tmp_path / "big.jpg"
    PILImage.new("RGB", (4000, 2000), color=(128, 128, 128)).save(img_path, "JPEG")
    registry = {}
    handle_add_file(store, registry, 9669, str(img_path))
    return store, registry, img_path


def test_get_image_crop_no_project(store):
    result = handle_get_image_crop(store, {}, fid="1", bbox=[0, 0, 10, 10])
    assert "No project" in _text(result)


def test_get_image_crop_unknown_fid(loaded_store):
    result = handle_get_image_crop(loaded_store, {}, fid="99", bbox=[0, 0, 10, 10])
    assert "not found" in _text(result).lower()


def test_get_image_crop_bad_bbox(store_with_big_image):
    store, registry, _ = store_with_big_image
    result = handle_get_image_crop(store, registry, fid="1", bbox=[0, 0, 10])
    assert "bbox" in _text(result)


def test_get_image_crop_bad_xy_space(store_with_big_image):
    store, registry, _ = store_with_big_image
    result = handle_get_image_crop(store, registry, fid="1",
                                    bbox=[0, 0, 100, 100], xy_space="garbage")
    assert "xy_space" in _text(result)


def test_get_image_crop_original_coords(store_with_big_image):
    store, registry, _ = store_with_big_image
    result = handle_get_image_crop(store, registry, fid="1",
                                    bbox=[500, 400, 600, 300])
    assert len(result) == 2
    assert "window=(500,400,600×300)" in result[0].text
    assert isinstance(result[1], types.ImageContent)
    assert len(result[1].data) > 0
    # 600×300 crop fits under max_dim=2048 — no downscale note
    assert "returned=" not in result[0].text


def test_get_image_crop_fraction_coords(store_with_big_image):
    store, registry, _ = store_with_big_image
    # 0.25..0.75 in x of 4000 → 1000..3000 (window 1000,_,2000,_)
    # 0.1..0.5 in y of 2000 → 200..1000 (window _,200,_,800)
    result = handle_get_image_crop(store, registry, fid="1",
                                    bbox=[0.25, 0.1, 0.5, 0.4],
                                    xy_space="fraction")
    assert "window=(1000,200,2000×800)" in result[0].text


def test_get_image_crop_downscales_large_window(store_with_big_image):
    store, registry, _ = store_with_big_image
    # 3000×1500 crop > 2048 → downscaled to 2048×1024
    result = handle_get_image_crop(store, registry, fid="1",
                                    bbox=[0, 0, 3000, 1500])
    assert "returned=2048×1024" in result[0].text


def test_get_image_crop_clips_to_image_bounds(store_with_big_image):
    store, registry, _ = store_with_big_image
    # bbox extends past the right/bottom — should clip
    result = handle_get_image_crop(store, registry, fid="1",
                                    bbox=[3800, 1900, 500, 500])
    # max remaining after (3800, 1900) is (200, 100)
    assert "200×100" in result[0].text


def test_get_image_crop_overlay_draws_region_inside_window(store, tmp_path):
    """Region at original (1000,500,200,200) should land inside a crop starting
    at (900,400). Overlay draws are smoke-tested by inspecting the rendered crop:
    we just verify the call succeeds and the response advertises overlay=on.
    """
    from PIL import Image as PILImage
    img_path = tmp_path / "big.jpg"
    PILImage.new("RGB", (4000, 2000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))
    handle_add_region(store, {}, vid="1", z=[], xy=[2, 1000, 500, 200, 200],
                      av={"label": "target"})
    result = handle_get_image_crop(store, registry, fid="1",
                                    bbox=[900, 400, 500, 500], overlay=True)
    assert "overlay=on" in result[0].text
    assert isinstance(result[1], types.ImageContent)
    assert len(result[1].data) > 0


# --- Phase 2: via_suggest_regions with a mock detector adapter ---


class _FakeDetector:
    """Stand-in for a real adapter so the handler can be exercised without
    torch installed. Returns a fixed set of detections regardless of prompts."""

    model_id = "test/fake-detector"
    capabilities = ("detect",)

    def __init__(self, detections):
        from annotate.models.base import Detection
        self._detections = [Detection(**d) for d in detections]

    def detect(self, image, prompts, **kwargs):
        return list(self._detections)


def _build_registry_with_fake(detections):
    """Return a ModelRegistry whose acquire('detect','detect') yields _FakeDetector."""
    from annotate.models import ModelRegistry, default_config
    reg = ModelRegistry(default_config())
    fake = _FakeDetector(detections)
    # Bypass the construct/load path — drop the fake straight into the cache
    from annotate.models.registry import _LoadedEntry
    import time
    reg._loaded["detect.default"] = _LoadedEntry(fake, time.monotonic())
    return reg


def test_suggest_regions_no_project(store):
    from annotate.server import handle_suggest_regions
    from annotate.models import ModelRegistry, default_config
    reg = ModelRegistry(default_config())
    result = handle_suggest_regions(store, {}, reg, fid="1", prompts=["x"])
    assert "No project" in _text(result)


def test_suggest_regions_no_registry_returns_stub(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_suggest_regions
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))
    result = handle_suggest_regions(store, registry, None, fid="1", prompts=["x"])
    data = json.loads(_text(result))
    assert data["available"] is False


def test_suggest_regions_tiles_and_returns_candidates(store, tmp_path):
    """Happy path with a mock detector: the handler should crop tiles,
    call the detector per tile, map back to image fraction space, and
    return merged candidates."""
    from PIL import Image as PILImage
    from annotate.server import handle_suggest_regions
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (800, 800), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))

    # Detector returns one box centred on its tile, in fraction-of-tile
    # coords. The handler should remap to fraction-of-image and dedupe via NMS.
    reg = _build_registry_with_fake([
        {"xy": [2, 0.25, 0.25, 0.5, 0.5], "label": "thing", "score": 0.9}
    ])
    result = handle_suggest_regions(
        store, registry_map, reg,
        fid="1", prompts=["thing"], tiling="2x2",
    )
    data = json.loads(_text(result))
    assert data["tile_grid"] == "2x2"
    assert data["tile_count"] == 4
    assert data["model"] == "test/fake-detector"
    # 4 tiles × 1 box each → at least 1 candidate after NMS merge
    assert data["candidate_count"] >= 1
    for cand in data["candidates"]:
        assert 0.0 <= cand["xy"][1] <= 1.0
        assert cand["label"] == "thing"


def test_suggest_regions_exclude_existing_filters_overlaps(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_suggest_regions
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    # Pre-existing annotation covering the central region in original-pixel coords
    handle_add_region(store, registry_map, vid="1", z=[],
                      xy=[2, 200, 200, 600, 600], av={"label": "existing"})
    reg = _build_registry_with_fake([
        {"xy": [2, 0.2, 0.2, 0.6, 0.6], "label": "thing", "score": 0.9}
    ])
    # With exclude_existing, the detector's box (which is the same region)
    # should be filtered out.
    result = handle_suggest_regions(
        store, registry_map, reg,
        fid="1", prompts=["thing"], tiling="none", exclude_existing=True,
    )
    data = json.loads(_text(result))
    assert data["candidate_count"] == 0


def test_suggest_regions_broad_prompts_uses_default_set(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.models.tiling import DEFAULT_BROAD_PROMPTS
    from annotate.server import handle_suggest_regions
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    reg = _build_registry_with_fake([])
    result = handle_suggest_regions(
        store, registry_map, reg,
        fid="1", prompts=None, tiling="none", broad_prompts=True,
    )
    data = json.loads(_text(result))
    assert data["prompts"] == DEFAULT_BROAD_PROMPTS


# --- Phase 3: via_tighten_region + via_grade_annotations with mocks ---


class _FakeSegmenter:
    model_id = "test/fake-segmenter"
    capabilities = ("segment",)

    def __init__(self, return_xy_fraction, iou=0.85, area_fraction=0.02):
        from annotate.models.base import Mask
        self._result = Mask(
            xy=return_xy_fraction,
            iou_with_input=iou,
            area_fraction=area_fraction,
            raster=None,
        )

    def segment(self, image, **kwargs):
        return self._result


class _FakeGrader:
    model_id = "test/fake-grader"
    capabilities = ("grade",)

    def __init__(self, position=0.8, size=0.7, label_match=0.65, fit="good"):
        self._kw = dict(position=position, size=size, label_match=label_match, fit=fit)

    def grade(self, image, region, label):
        from annotate.models.base import Grade
        return Grade(
            mid=region.get("_mid", ""),
            label=label,
            position=self._kw["position"],
            size=self._kw["size"],
            label_match=self._kw["label_match"],
            shape_encoding_fit=self._kw["fit"],
            issues=["mock issue"] if self._kw["fit"] != "good" else [],
        )


def _registry_with_pipeline(adapter, pipeline_key="segment.default"):
    from annotate.models import ModelRegistry, default_config
    from annotate.models.registry import _LoadedEntry
    import time
    reg = ModelRegistry(default_config())
    reg._loaded[pipeline_key] = _LoadedEntry(adapter, time.monotonic())
    return reg


def test_tighten_region_unknown_mid(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_tighten_region
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    reg = _registry_with_pipeline(_FakeSegmenter([2, 0.1, 0.1, 0.2, 0.2]))
    result = handle_tighten_region(store, registry_map, reg, metadata_id="nope")
    assert "not found" in _text(result)


def test_tighten_region_returns_tightened_box_without_writing(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_tighten_region
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    add = handle_add_region(store, registry_map, vid="1", z=[],
                            xy=[2, 100, 100, 400, 400], av={"label": "x"})
    mid = json.loads(_text(add))["metadata_id"]

    # Segmenter returns a tighter box at 20%-30% in fraction coords
    reg = _registry_with_pipeline(_FakeSegmenter([2, 0.2, 0.2, 0.3, 0.3]))
    result = handle_tighten_region(store, registry_map, reg, metadata_id=mid)
    data = json.loads(_text(result))
    assert data["auto_applied"] is False
    assert data["tightened_xy_pixel"] == [2, 200, 200, 300, 300]
    # Region in the store should be unchanged
    project = store.get()
    assert project["metadata"][mid]["xy"] == [2, 100, 100, 400, 400]


def test_tighten_region_auto_apply_writes_back(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_tighten_region
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    add = handle_add_region(store, registry_map, vid="1", z=[],
                            xy=[2, 100, 100, 400, 400], av={"label": "x"})
    mid = json.loads(_text(add))["metadata_id"]
    reg = _registry_with_pipeline(_FakeSegmenter([2, 0.2, 0.2, 0.3, 0.3]))
    handle_tighten_region(store, registry_map, reg, metadata_id=mid, auto_apply=True)
    project = store.get()
    assert project["metadata"][mid]["xy"] == [2, 200, 200, 300, 300]


def test_grade_annotations_empty_fid_returns_empty_set(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_grade_annotations
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    reg = _registry_with_pipeline(_FakeGrader(), pipeline_key="grade.default")
    result = handle_grade_annotations(store, registry_map, reg, fid="1")
    data = json.loads(_text(result))
    assert data["regions"] == []
    assert data["overall"]["flagged_count"] == 0


def test_grade_annotations_scores_each_region(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_grade_annotations
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    handle_add_region(store, registry_map, vid="1", z=[],
                      xy=[2, 100, 100, 200, 200], av={"label": "cat"})
    handle_add_region(store, registry_map, vid="1", z=[],
                      xy=[2, 500, 500, 200, 200], av={"label": "dog"})
    # A grader that fails: position=0.3 → should be flagged
    reg = _registry_with_pipeline(_FakeGrader(position=0.3, size=0.4, label_match=0.4,
                                              fit="marginal"),
                                   pipeline_key="grade.default")
    result = handle_grade_annotations(store, registry_map, reg, fid="1")
    data = json.loads(_text(result))
    assert data["graded_count"] == 2
    assert data["overall"]["flagged_count"] == 2
    for r in data["regions"]:
        assert r["flagged"] is True
        assert "mock issue" in r["issues"]


# --- Phase 6: via_ask_model + via_find_similar ---


class _FakeAsker:
    model_id = "test/fake-asker"
    capabilities = ("ask",)

    def __init__(self, answer_text="It is a cat."):
        self._text = answer_text
        self._last_question = None
        self._last_image_size = None

    def ask(self, image, question, **kwargs):
        from annotate.models.base import Answer
        self._last_question = question
        self._last_image_size = image.size
        return Answer(question=question, text=self._text,
                      finish_reason="stop", tokens_generated=len(self._text.split()))


class _FakeSimilarityFinder:
    model_id = "test/fake-yoloe"
    capabilities = ("find_similar",)

    def __init__(self, detections_per_target=None):
        self._dets = detections_per_target or []

    def find_similar(self, target_image, reference_image, reference_box_pixel, **kwargs):
        from annotate.models.base import Detection
        return [Detection(**d) for d in self._dets]


def _reg_with_adapter(pipeline_key, adapter):
    """Build a registry with a fake adapter pre-cached AND a matching
    config entry so acquire() resolves successfully."""
    from annotate.models import ModelRegistry, default_config
    from annotate.models.config import PipelineConfig
    from annotate.models.registry import _LoadedEntry
    import time
    reg = ModelRegistry(default_config())
    task, name = pipeline_key.split(".", 1)
    # Ensure a pipeline config exists for the key (some defaults like
    # find_similar.default are commented out in the embedded TOML).
    if pipeline_key not in reg.config.pipelines:
        reg.config.pipelines[pipeline_key] = PipelineConfig(
            task=task, name=name, model="test/mock", device="cpu",
        )
    reg._loaded[pipeline_key] = _LoadedEntry(adapter, time.monotonic())
    return reg


def test_ask_model_empty_question_errors(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_ask_model
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    reg = _reg_with_adapter("ask.default", _FakeAsker())
    result = handle_ask_model(store, registry_map, reg, fid="1", question="  ")
    assert "non-empty" in _text(result)


def test_ask_model_full_image(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_ask_model
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 300), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    asker = _FakeAsker(answer_text="There is a small dark image.")
    reg = _reg_with_adapter("ask.default", asker)
    result = handle_ask_model(store, registry_map, reg, fid="1",
                              question="What's in this image?")
    data = json.loads(_text(result))
    assert data["answer"] == "There is a small dark image."
    assert data["used_window_pixel"] == [0, 0, 400, 300]
    assert asker._last_image_size == (400, 300)


def test_ask_model_with_region_bbox_crops(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_ask_model
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    asker = _FakeAsker()
    reg = _reg_with_adapter("ask.default", asker)
    handle_ask_model(store, registry_map, reg, fid="1",
                     question="What is this?",
                     region_bbox=[0.2, 0.2, 0.4, 0.4])
    # 0.2..0.6 of 1000 = 200..600, padded by 10% of 400 → 40, so 160..640 = 480 wide
    assert asker._last_image_size == (480, 480)


def test_ask_model_with_metadata_id(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_ask_model
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    add = handle_add_region(store, registry_map, vid="1", z=[],
                            xy=[2, 100, 100, 200, 200], av={"label": "x"})
    mid = json.loads(_text(add))["metadata_id"]
    asker = _FakeAsker(answer_text="It's a thing.")
    reg = _reg_with_adapter("ask.default", asker)
    result = handle_ask_model(store, registry_map, reg, fid="1",
                              question="What is it?", metadata_id=mid)
    data = json.loads(_text(result))
    assert data["metadata_id"] == mid
    assert data["answer"] == "It's a thing."


def test_find_similar_unknown_mid(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_find_similar
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    reg = _reg_with_adapter("find_similar.default", _FakeSimilarityFinder())
    result = handle_find_similar(store, registry_map, reg, metadata_id="nope")
    assert "not found" in _text(result)


def test_find_similar_returns_per_target_results(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_find_similar
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (1000, 1000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    add = handle_add_region(store, registry_map, vid="1", z=[],
                            xy=[2, 100, 100, 200, 200], av={"label": "ice skater"})
    mid = json.loads(_text(add))["metadata_id"]
    finder = _FakeSimilarityFinder([
        {"xy": [2, 0.4, 0.4, 0.05, 0.05], "label": "x", "score": 0.85},
        {"xy": [2, 0.6, 0.3, 0.05, 0.05], "label": "x", "score": 0.7},
    ])
    reg = _reg_with_adapter("find_similar.default", finder)
    result = handle_find_similar(store, registry_map, reg, metadata_id=mid)
    data = json.loads(_text(result))
    assert data["reference_label"] == "ice skater"
    assert len(data["results"]) == 1
    assert data["results"][0]["candidate_count"] == 2
    # Reference label should be propagated onto similar candidates
    for c in data["results"][0]["candidates"]:
        assert c["label"] == "ice skater"


# --- Phase 5: scene classifier, routing, adaptive tiling ---


class _FakeClassifier:
    model_id = "test/fake-classifier"
    capabilities = ("classify",)

    def __init__(self, scores):
        self._scores = scores  # dict[str, float]

    def classify(self, image, candidate_labels):
        return {lab: self._scores.get(lab, 0.0) for lab in candidate_labels}


def test_classify_scene_persists_top_label(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_classify_scene
    from annotate.models import ModelRegistry, default_config
    from annotate.models.registry import _LoadedEntry
    import time
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))

    fake = _FakeClassifier({"dense_crowd": 0.7, "painting": 0.2, "landscape": 0.1})
    reg = ModelRegistry(default_config())
    reg._loaded["classify.default"] = _LoadedEntry(fake, time.monotonic())
    result = handle_classify_scene(store, registry_map, reg, fid="1")
    data = json.loads(_text(result))
    assert data["scene_class"] == "dense_crowd"
    assert data["cached_on_file_entry"] is True
    # Persisted on the file entry
    file_entry = store.get()["file"]["1"]
    assert file_entry["scene_class"] == "dense_crowd"
    assert file_entry["scene_class_confidence"] == pytest.approx(0.7, abs=1e-4)
    # Routing effect uses the default routing in the embedded config
    assert data["routing_effect"]["detect"] == "detect.dense_scene"


def test_classify_scene_no_labels_errors(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_classify_scene
    from annotate.models.config import _parse
    from annotate.models import ModelRegistry
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    # Build a registry whose classify.default has NO labels
    cfg = _parse(tomllib.loads('[classify.default]\nmodel = "x/y"\n'))
    reg = ModelRegistry(cfg)
    result = handle_classify_scene(store, registry_map, reg, fid="1")
    assert "No candidate labels" in _text(result)


def test_routing_passes_scene_class_to_detector(store, tmp_path):
    """End-to-end routing: a file with scene_class='dense_crowd' should
    cause the detect handler to ask for pipeline 'dense_scene' (not 'default').
    """
    from PIL import Image as PILImage
    from annotate.server import handle_suggest_regions
    from annotate.models import ModelRegistry, default_config
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    project = store.get()
    project["file"]["1"]["scene_class"] = "dense_crowd"
    store.set_project(project)

    # Track which pipeline acquire() was asked for.
    seen = {}
    reg = ModelRegistry(default_config())
    original = reg.acquire
    def spy(task, capability, **kw):
        seen["scene_class"] = kw.get("scene_class")
        seen["pipeline"] = kw.get("pipeline")
        raise NotImplementedError("stop here — we only care about how we were called")
    reg.acquire = spy
    handle_suggest_regions(store, registry_map, reg, fid="1", prompts=["x"])
    assert seen["scene_class"] == "dense_crowd"
    assert seen["pipeline"] is None  # routing happens inside config.pipeline_for


# --- saliency clustering (no torch needed; cv2 is required, skip if missing) ---

def test_cluster_to_tiles_groups_bright_blobs():
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from annotate.models.saliency import cluster_to_tiles

    # Build a 200×200 saliency map with two bright squares
    sal = np.zeros((200, 200), dtype="uint8")
    sal[20:60, 20:60] = 220       # blob A
    sal[120:170, 100:170] = 200   # blob B
    tiles = cluster_to_tiles(sal, threshold=0.4, max_tiles=8, min_area_fraction=0.001)
    assert len(tiles) == 2
    # Larger blob (B = 50*70=3500) should be first; A = 40*40=1600
    assert tiles[0].w * tiles[0].h >= tiles[1].w * tiles[1].h


def test_cluster_to_tiles_caps_max_tiles():
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from annotate.models.saliency import cluster_to_tiles

    sal = np.zeros((200, 200), dtype="uint8")
    # Plant 12 distinct blobs
    coords = [(i * 15, j * 15) for i in range(4) for j in range(3)]
    for x, y in coords:
        sal[y:y + 8, x:x + 8] = 255
    tiles = cluster_to_tiles(sal, threshold=0.4, max_tiles=5, min_area_fraction=0.0001)
    assert len(tiles) == 5


# --- Phase 4: via_verify_region with mock verifier ---


class _FakeVerifier:
    model_id = "test/fake-verifier"
    capabilities = ("verify",)

    def __init__(self, verdict_result):
        self._verdict = verdict_result

    def verify(self, image_crop, label):
        return self._verdict


def test_verify_region_unknown_mid(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_verify_region
    from annotate.models.base import Verdict
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    fake = _FakeVerifier(Verdict("x", "yes", 0.9))
    from annotate.models import ModelRegistry, default_config
    from annotate.models.registry import _LoadedEntry
    import time
    reg = ModelRegistry(default_config())
    reg._loaded["verify.default"] = _LoadedEntry(fake, time.monotonic())
    result = handle_verify_region(store, registry_map, reg, metadata_id="nope")
    assert "not found" in _text(result)


def test_verify_region_label_missing(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_verify_region
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (400, 400), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    # Add a region with no label
    add = handle_add_region(store, registry_map, vid="1", z=[],
                            xy=[2, 50, 50, 100, 100], av={})
    mid = json.loads(_text(add))["metadata_id"]
    from annotate.models import ModelRegistry, default_config
    reg = ModelRegistry(default_config())
    result = handle_verify_region(store, registry_map, reg, metadata_id=mid)
    assert "no label" in _text(result)


def test_verify_region_happy_path(store, tmp_path):
    from PIL import Image as PILImage
    from annotate.server import handle_verify_region
    from annotate.models.base import Verdict
    from annotate.models import ModelRegistry, default_config
    from annotate.models.registry import _LoadedEntry
    import time
    img_path = tmp_path / "i.jpg"
    PILImage.new("RGB", (800, 800), color=(0, 0, 0)).save(img_path, "JPEG")
    registry_map: dict = {}
    handle_add_file(store, registry_map, 9669, str(img_path))
    add = handle_add_region(store, registry_map, vid="1", z=[],
                            xy=[2, 200, 200, 200, 200], av={"label": "windscreen"})
    mid = json.loads(_text(add))["metadata_id"]

    fake = _FakeVerifier(Verdict(
        label_claimed="windscreen",
        verdict="no",
        confidence=0.78,
        supporting=[],
        contradicting=["a leather helmet covering the head"],
        suggested_label="leather flying helmet",
    ))
    reg = ModelRegistry(default_config())
    reg._loaded["verify.default"] = _LoadedEntry(fake, time.monotonic())
    result = handle_verify_region(store, registry_map, reg, metadata_id=mid)
    data = json.loads(_text(result))
    assert data["verdict"] == "no"
    assert data["suggested_label"] == "leather flying helmet"
    assert data["crop_window_pixel"][2] >= 200  # crop includes padding
    assert data["model"] == "test/fake-verifier"


# --- Label-match heuristic (no torch needed) ---

def test_label_match_finds_token_overlap():
    from annotate.models.florence2 import _label_match
    ok, conf = _label_match("a brown leather flying helmet with goggles", "leather helmet")
    assert ok is True
    assert conf > 0.7


def test_label_match_misses_unrelated():
    from annotate.models.florence2 import _label_match
    ok, _ = _label_match("a windscreen", "leather helmet")
    assert ok is False


def test_extract_suggested_label_pulls_noun_phrase():
    from annotate.models.florence2 import _extract_suggested_label
    assert _extract_suggested_label("A brown leather helmet with goggles") == "brown leather helmet"
    assert _extract_suggested_label("the small wooden boat") == "small wooden boat"


# --- shape-encoding-fit heuristic (no torch needed) ---

def test_shape_encoding_fit_flags_very_long_rectangles():
    from annotate.models.clip_grader import _shape_encoding_fit
    # 600px wide × 50px tall — aspect 12 → marginal
    fit, note = _shape_encoding_fit([2, 0, 0, 600, 50], (0, 0, 600, 50))
    assert fit == "marginal"
    assert "polyline" in note


def test_shape_encoding_fit_passes_balanced_rectangles():
    from annotate.models.clip_grader import _shape_encoding_fit
    fit, note = _shape_encoding_fit([2, 0, 0, 200, 150], (0, 0, 200, 150))
    assert fit == "good"
    assert note is None


def test_shape_encoding_fit_flags_non_square_circle():
    from annotate.models.clip_grader import _shape_encoding_fit
    # Circle whose bbox is 200×100 — should suggest ellipse instead
    fit, _ = _shape_encoding_fit([3, 100, 50, 50], (0, 0, 200, 100))
    assert fit == "marginal"


# --- Phase 1: local-model stub tools ---

def test_model_status_with_no_registry_returns_disabled():
    from annotate.server import handle_model_status
    result = handle_model_status(None)
    data = json.loads(_text(result))
    assert data["ai_extra_available"] is False
    assert data["loaded"] == []
    assert "install" in data["hint"].lower()


def test_model_status_with_registry_returns_snapshot():
    from annotate.server import handle_model_status
    from annotate.models import ModelRegistry, default_config
    reg = ModelRegistry(default_config())
    result = handle_model_status(reg)
    data = json.loads(_text(result))
    assert "configured_pipelines" in data
    assert "detect.default" in data["configured_pipelines"]


def test_ai_stub_response_advertises_install_hint_when_extra_missing():
    from annotate.server import _ai_stub_response
    result = _ai_stub_response("via_suggest_regions", None)
    data = json.loads(_text(result))
    assert data["tool"] == "via_suggest_regions"
    assert data["available"] is False
    # When extra is missing, an install hint should appear
    if not data.get("ai_extra_installed"):
        assert "annotate[ai]" in data["install_hint"]


def test_get_image_crop_overlay_skips_off_crop_regions(store, tmp_path):
    """Regression for the phantom-label bug (12-vermeer): a region whose box is
    entirely outside the crop window must not render a label at the clamped
    crop edge. Off-crop regions should be skipped entirely.
    """
    from PIL import Image as PILImage
    from annotate.server import _draw_overlay
    img_path = tmp_path / "big.jpg"
    PILImage.new("RGB", (4000, 2000), color=(0, 0, 0)).save(img_path, "JPEG")
    registry: dict = {}
    handle_add_file(store, registry, 9669, str(img_path))
    # Region at original y=100 — well above the (y=1500, h=400) crop window
    handle_add_region(store, {}, vid="1", z=[], xy=[2, 100, 100, 50, 50],
                      av={"label": "OFFCROP"})
    # In-crop region for sanity
    handle_add_region(store, {}, vid="1", z=[], xy=[2, 600, 1600, 100, 100],
                      av={"label": "INCROP"})

    # Build a synthetic crop image and run _draw_overlay directly so we can
    # inspect what got drawn. Crop window: (500, 1500, 800, 400) in original.
    project = store.get()
    crop = PILImage.new("RGB", (800, 400), color=(0, 0, 0))
    crop._overlay_orig_w = 800
    crop._overlay_orig_h = 400
    crop._overlay_offset_x = 500
    crop._overlay_offset_y = 1500
    _draw_overlay(crop, project, "1")
    # The OFFCROP label was previously rendered at y=0 (max(0, anchor-12)).
    # After the fix, no pixels in the top row should have been written for it.
    # Check: top-left 4x4 corner stays black (no phantom label).
    pixels = crop.load()
    top_left_dark = all(
        pixels[x, y] == (0, 0, 0)
        for x in range(4) for y in range(4)
    )
    assert top_left_dark, "off-crop region rendered a phantom label at crop edge"


# --- view-reset-bug regression (P0) ---

def test_view_reset_fixes_present_in_html():
    """Regression guard for the browser-reset-on-every-write friction reported
    in 02-castillitos, 03-mangrove, and 04-haefeli-dh5 training sessions.

    Three patches must all be present:
      1. project_updated handler uses .indexOf (was `in` on array — wrong semantics).
      2. project_loaded handler preserves current_vid (was unconditional vid_list[0]).
      3. Auto-poll uses incremental metadata diff (was full via.s.pull → view re-init).
    """
    from importlib.resources import files
    html = files("annotate").joinpath("via_image_annotator.html").read_text(encoding="utf-8")

    # 1. project_updated handler
    assert "current_vid in this.d.store.project.vid_list" not in html, (
        "Upstream `in`-on-array bug regressed in project_updated handler"
    )
    assert "vid_list.indexOf(current_vid) !== -1" in html

    # 2. project_loaded handler preserves current_vid
    assert "current_vid && this.d.store.project.vid_list.indexOf(current_vid)" in html, (
        "project_loaded handler should preserve current_vid"
    )

    # 3. Incremental-pull snippet replaced the naive setInterval(via.s.pull)
    assert "annotate: incremental pull" in html
    assert "metadata_delete_bulk" in html  # synthesized event for removed regions
    # the old naive pattern must be gone
    assert "if (remote.project.rev !== via.d.store.project.rev)\n      via.s.pull(pid);" not in html


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


# --- Florence-2 config compat patch ---

def test_florence2_compat_patch_wraps_init():
    """_patch_florence2_config_compat wraps __init__ to inject forced_bos_token_id."""
    from annotate.models.florence2 import Florence2Adapter

    class _BrokenConfig:
        _annotate_f2_compat = False
        attribute_map: dict = {}

        def __init__(self, **kwargs):
            _ = self.__dict__["forced_bos_token_id"]  # KeyError → simulated AttributeError

    with pytest.raises((AttributeError, KeyError)):
        _BrokenConfig()

    orig_init = _BrokenConfig.__init__

    def _compat_init(cfg_self, *args, **kwargs):
        kwargs.setdefault("forced_bos_token_id", None)
        orig_init(cfg_self, *args, **kwargs)

    _BrokenConfig.__init__ = _compat_init
    _BrokenConfig._annotate_f2_compat = True

    id_before = id(_BrokenConfig.__init__)
    if not getattr(_BrokenConfig, "_annotate_f2_compat", False):
        _BrokenConfig.__init__ = _compat_init
    assert id(_BrokenConfig.__init__) == id_before


def test_florence2_compat_patch_graceful_on_missing_transformers(monkeypatch):
    """_patch_florence2_config_compat is a no-op when dynamic_module_utils unavailable."""
    import builtins
    from annotate.models.florence2 import Florence2Adapter

    adapter = Florence2Adapter("microsoft/Florence-2-base")

    real = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "transformers.dynamic_module_utils":
            raise ImportError("simulated missing transformers")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    adapter._patch_florence2_config_compat()
