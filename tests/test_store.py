import copy
import json
import tempfile
from pathlib import Path

import pytest

from annomate.store import ProjectStore, ProjectNotFoundError, ProjectConflictError


def _minimal_project(pid="__VIA_PROJECT_ID__", rev="__VIA_PROJECT_REV_ID__"):
    return {
        "project": {"pid": pid, "rev": rev, "rev_timestamp": "0", "pname": "test"},
        "config": {},
        "attribute": {},
        "file": {"1": {"fid": 1, "fname": "img.jpg", "type": 2, "loc": 3, "src": "/img.jpg"}},
        "view": {"1": {"fid_list": [1]}},
        "metadata": {},
    }


@pytest.fixture
def store(tmp_path):
    return ProjectStore(state_file=tmp_path / "state.json")


def test_get_returns_none_when_empty(store):
    assert store.get() is None


def test_create_assigns_pid_and_rev(store):
    result = store.create(_minimal_project())
    assert "pid" in result
    assert result["pid"] != "__VIA_PROJECT_ID__"
    assert result["rev"] == "1"
    assert "rev_timestamp" in result


def test_create_stores_project(store):
    store.create(_minimal_project())
    project = store.get()
    assert project is not None
    assert project["project"]["rev"] == "1"


def test_get_returns_deep_copy(store):
    store.create(_minimal_project())
    p1 = store.get()
    p1["metadata"]["injected"] = {}
    p2 = store.get()
    assert "injected" not in p2["metadata"]


def test_exists_false_when_empty(store):
    assert store.exists("some-pid") is False


def test_exists_true_after_create(store):
    result = store.create(_minimal_project())
    assert store.exists(result["pid"]) is True


def test_update_from_browser_bumps_revision(store):
    result = store.create(_minimal_project())
    pid, rev = result["pid"], result["rev"]
    payload = store.get()
    update = store.update_from_browser(pid, rev, payload)
    assert update["rev"] == "2"
    assert update["pid"] == pid


def test_update_from_browser_wrong_pid_raises(store):
    store.create(_minimal_project())
    with pytest.raises(ProjectNotFoundError):
        store.update_from_browser("bad-pid", "1", _minimal_project())


def test_update_from_browser_wrong_rev_raises(store):
    result = store.create(_minimal_project())
    payload = store.get()
    with pytest.raises(ProjectConflictError):
        store.update_from_browser(result["pid"], "999", payload)


def test_update_from_browser_repairs_stripped_polyline_prefix(store):
    """Regression for 10-deposition: browser drag of a polyline can strip the
    leading shape-ID `6`, leaving xy as raw coords. Repair on push by
    consulting the prior version's shape."""
    proj = _minimal_project()
    proj["metadata"] = {
        "mid1": {"vid": "1", "flg": 0, "z": [], "xy": [6, 10, 20, 30, 40], "av": {}},
    }
    result = store.create(proj)
    pid, rev = result["pid"], result["rev"]

    payload = store.get()
    # simulate browser stripping the shape prefix on drag
    payload["metadata"]["mid1"]["xy"] = [15.0, 25.0, 35.0, 45.0]
    store.update_from_browser(pid, rev, payload)

    restored = store.get()["metadata"]["mid1"]["xy"]
    assert restored == [6, 15.0, 25.0, 35.0, 45.0]


def test_update_from_browser_new_region_without_prefix_defaults_to_polyline(store):
    """If there's no prior version to recover the shape from, treat as polyline
    (matches the long-standing SKILL.md convention for in-progress draws)."""
    result = store.create(_minimal_project())
    pid, rev = result["pid"], result["rev"]

    payload = store.get()
    payload["metadata"]["new_mid"] = {
        "vid": "1", "flg": 0, "z": [], "xy": [11.0, 22.0, 33.0, 44.0], "av": {},
    }
    store.update_from_browser(pid, rev, payload)

    repaired = store.get()["metadata"]["new_mid"]["xy"]
    assert repaired[0] == 6
    assert repaired[1:] == [11.0, 22.0, 33.0, 44.0]


def test_update_from_browser_leaves_valid_shape_prefix_alone(store):
    """Valid integer shape prefixes 1–7 should never be rewritten."""
    result = store.create(_minimal_project())
    pid, rev = result["pid"], result["rev"]

    payload = store.get()
    payload["metadata"]["rect"] = {
        "vid": "1", "flg": 0, "z": [], "xy": [2, 10, 20, 30, 40], "av": {},
    }
    store.update_from_browser(pid, rev, payload)
    assert store.get()["metadata"]["rect"]["xy"] == [2, 10, 20, 30, 40]


def test_custom_file_entry_fields_survive_round_trip(store):
    """Phase-1 prerequisite for local-model assistance: a custom field on a
    file entry (e.g. scene_class) added by the server / by an LLM tool must
    survive a browser push/pull round-trip. abs_path already does this in
    practice, but explicit coverage for new fields.
    """
    proj = _minimal_project()
    proj["file"]["1"]["scene_class"] = "painting"
    proj["file"]["1"]["scene_class_confidence"] = 0.83
    result = store.create(proj)
    pid, rev = result["pid"], result["rev"]

    # round-trip via update_from_browser, mimicking VIA's behaviour of
    # serialising the whole project back and forth
    payload = store.get()
    store.update_from_browser(pid, rev, payload)

    persisted = store.get()["file"]["1"]
    assert persisted["scene_class"] == "painting"
    assert persisted["scene_class_confidence"] == 0.83


def test_set_project_bumps_revision(store):
    store.create(_minimal_project())
    project = store.get()
    store.set_project(project)
    assert store.get()["project"]["rev"] == "2"


def test_set_project_preserves_pid(store):
    result = store.create(_minimal_project())
    pid = result["pid"]
    project = store.get()
    store.set_project(project)
    assert store.get()["project"]["pid"] == pid


def test_set_project_on_empty_store_assigns_pid(store):
    store.set_project(_minimal_project())
    project = store.get()
    assert project["project"]["pid"] != "__VIA_PROJECT_ID__"
    assert project["project"]["rev"] == "1"


def test_persists_to_disk(tmp_path):
    state_file = tmp_path / "state.json"
    store1 = ProjectStore(state_file=state_file)
    store1.create(_minimal_project())
    pid = store1.get()["project"]["pid"]

    store2 = ProjectStore(state_file=state_file)
    assert store2.get()["project"]["pid"] == pid


def test_survives_corrupt_state_file(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("not json", encoding="utf-8")
    store = ProjectStore(state_file=state_file)
    assert store.get() is None


def test_get_if_exists_returns_project(store):
    result = store.create(_minimal_project())
    pid = result["pid"]
    project = store.get_if_exists(pid)
    assert project is not None
    assert project["project"]["pid"] == pid


def test_get_if_exists_wrong_pid_returns_none(store):
    store.create(_minimal_project())
    assert store.get_if_exists("wrong-pid") is None


def test_get_if_exists_empty_store_returns_none(store):
    assert store.get_if_exists("any-pid") is None
