"""Lazy adapter registry with LRU eviction.

The registry has *no knowledge* of any specific model — it just owns
``Adapter`` instances keyed by pipeline name and an LRU policy for when
to evict. Adapters are constructed lazily from the config when first
requested, then ``load()``-ed (which is where heavy weights actually
come into memory).

Phase 1 ships the registry with **zero adapter classes registered**.
Tools that need a capability call ``registry.acquire(task, capability)``;
when no adapter is available (because the ``[ai]`` extra isn't installed
or no adapter class implements the configured model), the registry
raises ``NotInstalledError`` which the tool surfaces to the LLM as a
helpful install hint.
"""

from __future__ import annotations

import importlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from annomate.models.base import Adapter, Capability, NotInstalledError
from annomate.models.config import ModelsConfig, PipelineConfig


# Adapter class registry — populated by Phase 2+ as adapters are added.
# A factory takes (model_id, device, **extra) → Adapter instance.
AdapterFactory = Callable[..., Adapter]
_FACTORIES: dict[str, AdapterFactory] = {}


def register_adapter(model_id_prefix: str, factory: AdapterFactory) -> None:
    """Register an adapter factory for any model id starting with the prefix.

    Adapters lookup is prefix-match: longest-prefix wins. e.g. an adapter
    registered for ``"facebook/sam2-"`` handles both ``sam2-hiera-tiny``
    and ``sam2-hiera-base-plus``.
    """
    _FACTORIES[model_id_prefix] = factory


def _factory_for(model_id: str) -> AdapterFactory | None:
    matches = [(p, f) for p, f in _FACTORIES.items() if model_id.startswith(p)]
    if not matches:
        return None
    matches.sort(key=lambda pf: len(pf[0]), reverse=True)
    return matches[0][1]


def ai_extra_available() -> bool:
    """True iff the ``[ai]`` extra's runtime deps are importable."""
    try:
        importlib.import_module("torch")
        importlib.import_module("transformers")
        return True
    except ImportError:
        return False


@dataclass
class _LoadedEntry:
    adapter: Adapter
    last_used: float


class ModelRegistry:
    """Owns Adapter instances. Thread-safe."""

    def __init__(self, config: ModelsConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._loaded: OrderedDict[str, _LoadedEntry] = OrderedDict()

    @property
    def config(self) -> ModelsConfig:
        return self._config

    def reload_config(self, config: ModelsConfig) -> None:
        """Swap the active config. Currently loaded adapters keep running
        until they fall out of LRU or are explicitly unloaded; new
        acquisitions resolve against the new config."""
        with self._lock:
            self._config = config

    def acquire(
        self,
        task: str,
        capability: Capability,
        *,
        scene_class: str | None = None,
        pipeline: str | None = None,
    ) -> Adapter:
        """Return a ready-to-use adapter for the requested capability.

        Raises ``NotInstalledError`` with a useful message if no adapter
        can be provided (extra not installed, no factory registered,
        config missing, or the resolved adapter doesn't advertise the
        capability).
        """
        cfg = self._config.pipeline_for(task, scene_class=scene_class, override=pipeline)
        if cfg is None:
            raise NotInstalledError(
                f"No pipeline configured for task={task!r} "
                f"(scene_class={scene_class!r}, override={pipeline!r}). "
                f"Check your models.toml."
            )

        with self._lock:
            entry = self._loaded.get(cfg.key)
            if entry is not None:
                entry.last_used = time.monotonic()
                self._loaded.move_to_end(cfg.key)
                if capability not in entry.adapter.capabilities:
                    raise NotInstalledError(
                        f"Pipeline {cfg.key!r} (model {cfg.model!r}) does not "
                        f"advertise the {capability!r} capability "
                        f"(supports: {entry.adapter.capabilities})."
                    )
                return entry.adapter

        # Need to construct + load. Do this outside the lock so concurrent
        # acquisitions of *different* adapters don't serialise on load.
        adapter = self._construct(cfg)
        if capability not in adapter.capabilities:
            raise NotInstalledError(
                f"Pipeline {cfg.key!r} (model {cfg.model!r}) does not "
                f"advertise the {capability!r} capability "
                f"(supports: {adapter.capabilities})."
            )
        try:
            adapter.load()
        except ImportError as e:
            raise NotInstalledError(
                f"Adapter for {cfg.model!r} needs a dep that isn't installed: {e}. "
                f"Try: pip install 'annomate[ai]'"
            ) from e

        with self._lock:
            self._loaded[cfg.key] = _LoadedEntry(adapter, time.monotonic())
            self._loaded.move_to_end(cfg.key)
            self._evict_if_over_budget()
        return adapter

    def _construct(self, cfg: PipelineConfig) -> Adapter:
        if not ai_extra_available():
            raise NotInstalledError(
                "Local-model assistance requires the [ai] extra. Install with:\n"
                "    pip install 'annomate[ai]'\n"
                f"(needed to load {cfg.model!r} for {cfg.task}.{cfg.name})"
            )
        factory = _factory_for(cfg.model)
        if factory is None:
            raise NotInstalledError(
                f"No adapter registered for model_id {cfg.model!r}. "
                f"Phase 1 ships the registry with no adapter classes; "
                f"detection/segmentation/etc. adapters land in phases 2+."
            )
        return factory(cfg.model, device=cfg.device, **cfg.extra)

    def _evict_if_over_budget(self) -> None:
        # Called with self._lock held.
        while len(self._loaded) > self._config.max_loaded_models:
            _, entry = self._loaded.popitem(last=False)  # oldest
            try:
                entry.adapter.unload()
            except Exception:
                pass

    def unload_all(self) -> None:
        """Force-evict every loaded adapter. Useful for clean shutdown."""
        with self._lock:
            for _, entry in self._loaded.items():
                try:
                    entry.adapter.unload()
                except Exception:
                    pass
            self._loaded.clear()

    def status(self) -> dict:
        """Snapshot for the via_model_status tool."""
        with self._lock:
            loaded = []
            total_mb = 0
            for key, entry in self._loaded.items():
                mb = entry.adapter.memory_mb()
                total_mb += mb
                loaded.append({
                    "pipeline": key,
                    "model_id": entry.adapter.model_id,
                    "device": entry.adapter.device,
                    "memory_mb": mb,
                    "capabilities": list(entry.adapter.capabilities),
                    "last_used_seconds_ago": round(time.monotonic() - entry.last_used, 1),
                })
            return {
                "ai_extra_available": ai_extra_available(),
                "loaded": loaded,
                "total_memory_mb": total_mb,
                "max_loaded": self._config.max_loaded_models,
                "max_gpu_memory_gb": self._config.max_gpu_memory_gb,
                "configured_pipelines": sorted(self._config.pipelines.keys()),
                "config_source": str(self._config.source_path) if self._config.source_path else "(builtin defaults)",
                "registered_adapter_prefixes": sorted(_FACTORIES.keys()),
            }
