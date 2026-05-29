"""Phase 1 tests for the model registry, config loader, and stub tools.

No model weights, no torch — these tests verify scaffolding only.
"""

from __future__ import annotations

import json

import pytest

from annomate.models import (
    Adapter,
    ModelRegistry,
    NotInstalledError,
    ai_extra_available,
    default_config,
    load_config,
)
from annomate.models.config import DEFAULT_CONFIG_TOML
from annomate.models.registry import register_adapter, _FACTORIES


# --- config: loading + auto-generation ---

def test_default_config_parses():
    cfg = default_config()
    assert "detect.default" in cfg.pipelines
    assert cfg.pipelines["detect.default"].model.startswith("IDEA-Research/grounding-dino")
    assert cfg.pipelines["segment.default"].model.startswith("facebook/sam2")
    assert cfg.max_loaded_models >= 1


def test_load_config_auto_generates_when_missing(tmp_path):
    path = tmp_path / "models.toml"
    assert not path.exists()
    cfg = load_config(path)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TOML
    assert "detect.default" in cfg.pipelines
    assert cfg.source_path == path


def test_load_config_respects_user_edits(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        "[detect.default]\nmodel = \"my-org/my-detector\"\ndevice = \"cpu\"\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.pipelines["detect.default"].model == "my-org/my-detector"
    assert cfg.pipelines["detect.default"].device == "cpu"


def test_load_config_env_var_override(monkeypatch, tmp_path):
    path = tmp_path / "elsewhere.toml"
    path.write_text(
        "[detect.default]\nmodel = \"env/picked\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANNOMATE_MODELS_CONFIG", str(path))
    cfg = load_config()
    assert cfg.pipelines["detect.default"].model == "env/picked"


def test_pipeline_for_resolves_in_order():
    cfg = default_config()
    # default
    p = cfg.pipeline_for("detect")
    assert p is not None and p.name == "default"
    # override wins
    p = cfg.pipeline_for("detect", override="dense_scene")
    assert p is not None and p.name == "dense_scene"
    # scene-class routing
    p = cfg.pipeline_for("detect", scene_class="dense_crowd")
    assert p is not None and p.name == "dense_scene"
    # unknown override → None
    assert cfg.pipeline_for("detect", override="no_such_pipeline") is None
    # unknown task → None
    assert cfg.pipeline_for("invented_task") is None


# --- registry: no adapters yet, so everything raises NotInstalledError ---

def test_registry_without_adapters_raises_install_hint(tmp_path):
    reg = ModelRegistry(default_config())
    with pytest.raises(NotInstalledError) as exc:
        reg.acquire("detect", "detect")
    msg = str(exc.value)
    # Either ai-extra missing OR no adapter registered — both are install-hint shaped
    assert "annomate[ai]" in msg or "No adapter registered" in msg


def test_registry_status_snapshot():
    reg = ModelRegistry(default_config())
    s = reg.status()
    assert s["loaded"] == []
    assert s["total_memory_mb"] == 0
    assert "detect.default" in s["configured_pipelines"]
    assert "ai_extra_available" in s
    assert isinstance(s["registered_adapter_prefixes"], list)


def test_registry_lru_eviction_keeps_within_budget():
    """Use a fake adapter to exercise the LRU path without weights."""

    class FakeAdapter(Adapter):
        capabilities = ("detect",)
        weights_mb_estimate = 100

        def load(self) -> None:
            self._loaded = True

        def unload(self) -> None:
            self._loaded = False

        def detect(self, image, prompts, **kwargs):  # pragma: no cover - unused
            return []

    # Build a fresh config with three pipelines, max_loaded = 2
    cfg_toml = """
[detect.a]
model = "test-prefix-a/x"
[detect.b]
model = "test-prefix-b/x"
[detect.c]
model = "test-prefix-c/x"
[registry]
max_loaded_models = 2
"""
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    from annomate.models.config import _parse
    cfg = _parse(tomllib.loads(cfg_toml))
    reg = ModelRegistry(cfg)

    # Stub ai_extra_available so the construction path proceeds, and
    # register a fake factory for each model prefix.
    import annomate.models.registry as r
    original_ai = r.ai_extra_available
    saved_factories = dict(_FACTORIES)
    try:
        r.ai_extra_available = lambda: True
        for prefix in ("test-prefix-a/", "test-prefix-b/", "test-prefix-c/"):
            register_adapter(prefix, lambda mid, device="auto", **kw: FakeAdapter(mid, device, **kw))

        reg.acquire("detect", "detect", pipeline="a")
        reg.acquire("detect", "detect", pipeline="b")
        assert len(reg.status()["loaded"]) == 2
        reg.acquire("detect", "detect", pipeline="c")  # should evict "a"
        loaded_keys = {item["pipeline"] for item in reg.status()["loaded"]}
        assert loaded_keys == {"detect.b", "detect.c"}
    finally:
        r.ai_extra_available = original_ai
        _FACTORIES.clear()
        _FACTORIES.update(saved_factories)
        reg.unload_all()


# --- ai_extra_available is honest about install state ---

def test_ai_extra_available_returns_bool():
    assert isinstance(ai_extra_available(), bool)
    # In the dev venv torch isn't installed, so this should be False.
    # If you've installed torch locally this assertion just confirms it.
    if ai_extra_available():
        import torch  # noqa: F401 — proving the assertion's basis
