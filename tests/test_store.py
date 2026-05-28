import copy
import json
import tempfile
from pathlib import Path

import pytest

from via_mcp.store import ProjectStore, ProjectNotFoundError, ProjectConflictError


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
