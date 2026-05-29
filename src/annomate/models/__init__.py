"""Local-model assistance layer.

The base `annomate` package stays small. Heavy ML deps (torch, transformers,
sahi) ride in the optional `[ai]` extra. Tools advertised on the MCP surface
return a helpful "install the [ai] extra" error when these deps aren't
present rather than crashing at import time.
"""

from annomate.models.base import (
    Adapter,
    Answer,
    Capability,
    Detection,
    Grade,
    Mask,
    NotInstalledError,
    Verdict,
)
from annomate.models.config import ModelsConfig, default_config, load_config
from annomate.models.registry import ModelRegistry, ai_extra_available

__all__ = [
    "Adapter",
    "Answer",
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
