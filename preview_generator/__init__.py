"""Standalone preview generation; no workbench/backend dependency."""

from .generator import Config, GenerationCancelled, GenerationError, generate

__all__ = ["Config", "GenerationCancelled", "GenerationError", "generate"]
__version__ = "1.0.0"
