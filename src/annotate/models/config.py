"""TOML config for the model registry.

Default location: ``~/.config/annotate/models.toml`` (XDG-respecting via
``platformdirs``). Auto-generated on first run with the small-default
pipeline picks. Override the path via the ``ANNOTATE_MODELS_CONFIG``
env var or ``--models-config`` CLI flag.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - covered by the conditional dep
    import tomli as tomllib


# Default config string. Written verbatim to disk on first run. Keep it
# readable — users will hand-edit this. Comments survive the read because
# we never round-trip via the parser; we write the string, then read what
# we need.
DEFAULT_CONFIG_TOML = """\
# annotate model registry — auto-generated on first run.
# Edit pipelines, swap models, add finetunes. Reload by restarting the
# server. See docs/design/2026-05-28-local-model-assistance.md for the
# full schema.

# ---- detection (open-vocabulary) ----
[detect.default]
model = "IDEA-Research/grounding-dino-tiny"
device = "auto"

[detect.dense_scene]
model = "google/owlv2-large-patch14-ensemble"
device = "auto"

# YOLO-World — Apache 2.0, ~10x faster than GroundingDINO on CPU.
# Needs the [yolo] extra: pip install 'annotate[yolo]'.
[detect.fast]
model = "ultralytics:yolov8s-worldv2"
device = "auto"

# ---- segmentation (promptable, for region tightening) ----
[segment.default]
model = "facebook/sam2-hiera-tiny"
device = "auto"

[segment.high_quality]
model = "facebook/sam2-hiera-base-plus"
device = "auto"

# YOLOE native instance segmentation — AGPL-3.0, opt-in.
# [segment.yoloe]
# model = "ultralytics:yoloe-11s-seg"
# device = "auto"

# ---- visual-prompt one-shot detection (YOLOE) ----
# AGPL-3.0; commented out by default. Uncomment to enable via_find_similar.
# [find_similar.default]
# model = "ultralytics:yoloe-11s-seg"
# device = "auto"

# ---- free-form Q&A about images (chat VLM) ----
# Needs the [ai] + [chat] extras: pip install 'annotate[ai,chat]'.
# Default is Qwen2.5-VL-3B (~6 GB on disk, Apache 2.0). Smaller picks:
#   HuggingFaceTB/SmolVLM-Instruct   (~2 GB)
#   HuggingFaceTB/SmolVLM-256M-Instruct  (~500 MB, weaker)
[ask.default]
model = "Qwen/Qwen2.5-VL-3B-Instruct"
device = "auto"

# ---- verification (VLM crop-and-verify) ----
[verify.default]
model = "microsoft/Florence-2-base"
device = "auto"

# ---- grading (CLIP-cosine rubric until a real grader checkpoint ships) ----
[grade.default]
model = "openai/clip-vit-base-patch32"
device = "auto"

# ---- scene classification (drives auto-routing across pipelines) ----
[classify.default]
model = "openai/clip-vit-base-patch32"
device = "auto"
labels = [
    "dense_crowd",
    "single_subject",
    "painting",
    "aerial_or_satellite",
    "document",
    "diagram",
    "interior_scene",
    "landscape",
]

# ---- saliency (drives adaptive tiling) ----
# Tiny default — classical OpenCV spectral residual, no weights to download.
[saliency.default]
model = "opencv:spectral_residual"
device = "cpu"

# ---- scene-class → pipeline routing ----
# Pipelines named here override the .default of each task when the
# scene classifier returns that label.
[routing]
dense_crowd         = { detect = "dense_scene" }
aerial_or_satellite = { detect = "dense_scene" }

# ---- registry-wide settings ----
[registry]
max_loaded_models = 3
max_gpu_memory_gb = 8
"""


@dataclass
class PipelineConfig:
    """One named pipeline entry, e.g. ``[detect.default]``."""
    task: str            # "detect", "segment", "verify", ...
    name: str            # "default", "dense_scene", ...
    model: str
    device: str = "auto"
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.task}.{self.name}"


@dataclass
class ModelsConfig:
    """Parsed config: pipelines + routing + registry settings."""
    pipelines: dict[str, PipelineConfig]      # keyed by "task.name"
    routing: dict[str, dict[str, str]]        # scene_class → {task: pipeline_name}
    max_loaded_models: int = 3
    max_gpu_memory_gb: float = 8.0
    source_path: Path | None = None

    def pipeline_for(self, task: str, scene_class: str | None = None,
                     override: str | None = None) -> PipelineConfig | None:
        """Resolve which pipeline to use for a given task.

        Priority: explicit override → scene-class routing → "default" → None.
        """
        if override:
            return self.pipelines.get(f"{task}.{override}")
        if scene_class and scene_class in self.routing:
            mapped = self.routing[scene_class].get(task)
            if mapped:
                return self.pipelines.get(f"{task}.{mapped}")
        return self.pipelines.get(f"{task}.default")


# Task keys that map to top-level TOML tables.
_TASK_TABLES = ("detect", "segment", "verify", "grade", "classify",
                "saliency", "ask", "find_similar")


def default_config_path() -> Path:
    """The on-disk location where defaults are written / read from."""
    return Path(platformdirs.user_config_dir("annotate", appauthor=False)) / "models.toml"


def _parse(raw: dict, source: Path | None = None) -> ModelsConfig:
    pipelines: dict[str, PipelineConfig] = {}
    for task in _TASK_TABLES:
        block = raw.get(task) or {}
        if not isinstance(block, dict):
            continue
        for name, entry in block.items():
            if not isinstance(entry, dict):
                continue
            model = entry.get("model")
            if not model:
                continue
            extra = {k: v for k, v in entry.items() if k not in {"model", "device"}}
            pipelines[f"{task}.{name}"] = PipelineConfig(
                task=task,
                name=name,
                model=model,
                device=entry.get("device", "auto"),
                extra=extra,
            )

    routing_raw = raw.get("routing") or {}
    routing: dict[str, dict[str, str]] = {}
    for scene, mapping in routing_raw.items():
        if isinstance(mapping, dict):
            routing[scene] = {str(k): str(v) for k, v in mapping.items()}

    reg = raw.get("registry") or {}
    return ModelsConfig(
        pipelines=pipelines,
        routing=routing,
        max_loaded_models=int(reg.get("max_loaded_models", 3)),
        max_gpu_memory_gb=float(reg.get("max_gpu_memory_gb", 8.0)),
        source_path=source,
    )


def default_config() -> ModelsConfig:
    """The in-memory default config, parsed from the embedded TOML string."""
    return _parse(tomllib.loads(DEFAULT_CONFIG_TOML))


def load_config(path: Path | str | None = None, *, create_if_missing: bool = True) -> ModelsConfig:
    """Load models.toml from disk (or the override path).

    Resolution order:
      1. Explicit ``path`` argument.
      2. ``ANNOTATE_MODELS_CONFIG`` environment variable.
      3. ``default_config_path()`` (~/.config/annotate/models.toml).

    If the resolved path doesn't exist and ``create_if_missing`` is true,
    the default TOML is written there and parsed.
    """
    if path is None:
        env_path = os.environ.get("ANNOTATE_MODELS_CONFIG")
        path = Path(env_path) if env_path else default_config_path()
    else:
        path = Path(path)

    if not path.exists():
        if not create_if_missing:
            return default_config()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return _parse(raw, source=path)
