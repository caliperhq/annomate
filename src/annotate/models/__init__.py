"""Local-model assistance layer.

The base `annotate` package stays small. Heavy ML deps (torch, transformers,
sahi) ride in the optional `[ai]` extra. Tools advertised on the MCP surface
return a helpful "install the [ai] extra" error when these deps aren't
present rather than crashing at import time.
"""

from annotate.models.base import (
    Adapter,
    Capability,
    Detection,
    Grade,
    Mask,
    NotInstalledError,
    Verdict,
)
from annotate.models.config import ModelsConfig, default_config, load_config
from annotate.models.registry import ModelRegistry, ai_extra_available

__all__ = [
    "Adapter",
    "Capability",
    "Detection",
    "Grade",
    "Mask",
    "ModelRegistry",
    "ModelsConfig",
    "NotInstalledError",
    "Verdict",
    "ai_extra_available",
    "default_config",
    "load_config",
]
