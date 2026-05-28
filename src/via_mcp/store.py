import copy
import json
import threading
import time
import uuid
from pathlib import Path


class ProjectNotFoundError(Exception):
    pass


class ProjectConflictError(Exception):
    pass


class ProjectStore:
    def __init__(self, state_file: Path) -> None:
        self._lock = threading.Lock()
        self._project: dict | None = None
        self._state_file = state_file
        self._load()

    def get(self) -> dict | None:
        with self._lock:
            return copy.deepcopy(self._project)

    def exists(self, pid: str) -> bool:
        with self._lock:
            return (
                self._project is not None
                and self._project["project"]["pid"] == pid
            )

    def get_if_exists(self, pid: str) -> dict | None:
        with self._lock:
            if self._project is not None and self._project["project"]["pid"] == pid:
                return copy.deepcopy(self._project)
            return None

    def create(self, payload: dict) -> dict:
        pid = str(uuid.uuid4())
        rev = "1"
        ts = str(int(time.time() * 1000))
        with self._lock:
            payload = copy.deepcopy(payload)
            payload["project"]["pid"] = pid
            payload["project"]["rev"] = rev
            payload["project"]["rev_timestamp"] = ts
            self._project = payload
            self._persist()
        return {"pid": pid, "rev": rev, "rev_timestamp": ts}

    def update_from_browser(self, pid: str, rev: str, payload: dict) -> dict:
        with self._lock:
            if self._project is None or self._project["project"]["pid"] != pid:
                raise ProjectNotFoundError(pid)
            if self._project["project"]["rev"] != rev:
                raise ProjectConflictError(
                    f"local={rev} remote={self._project['project']['rev']}"
                )
            new_rev = str(int(self._project["project"]["rev"]) + 1)
            ts = str(int(time.time() * 1000))
            payload = copy.deepcopy(payload)
            payload["project"]["pid"] = pid
            payload["project"]["rev"] = new_rev
            payload["project"]["rev_timestamp"] = ts
            self._project = payload
            self._persist()
        return {"pid": pid, "rev": new_rev, "rev_timestamp": ts}

    def set_project(self, project: dict) -> None:
        with self._lock:
            project = copy.deepcopy(project)
            if self._project is not None:
                pid = self._project["project"]["pid"]
                new_rev = str(int(self._project["project"]["rev"]) + 1)
            else:
                pid = str(uuid.uuid4())
                new_rev = "1"
            ts = str(int(time.time() * 1000))
            project["project"]["pid"] = pid
            project["project"]["rev"] = new_rev
            project["project"]["rev_timestamp"] = ts
            self._project = project
            self._persist()

    def _persist(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._project), encoding="utf-8")
        tmp.replace(self._state_file)

    def _load(self) -> None:
        if self._state_file.exists():
            try:
                self._project = json.loads(
                    self._state_file.read_text(encoding="utf-8")
                )
                self._migrate()
            except Exception:
                self._project = None

    def _migrate(self) -> None:
        """Patch persisted projects that predate required VIA fields."""
        p = self._project
        if p is None:
            return
        proj = p.setdefault("project", {})
        if "vid_list" not in proj:
            proj["vid_list"] = list(p.get("view", {}).keys())
        proj.setdefault("data_format_version", "3.1.1")
        p.setdefault("config", {}).setdefault("file", {}).setdefault(
            "loc_prefix", {"1": "", "2": "", "3": "", "4": ""}
        )
