"""Claw Brain - Brain module

This package re-exports from the main clawbrain module for backward compatibility.
"""

__version__ = "0.3.0"
__author__ = "ClawColab"

# Re-export from the main module (clawbrain.py at package root)
from clawbrain import (
    Brain, Memory, ScoredMemory, UserProfile, Embedder, get_bridge_script_path,
    VALID_MEMORY_KINDS, VALID_DURABILITIES, VALID_SCOPES,
)

__all__ = [
    "Brain", "Memory", "ScoredMemory", "UserProfile", "Embedder",
    "get_bridge_script_path",
    "VALID_MEMORY_KINDS", "VALID_DURABILITIES", "VALID_SCOPES",
    "__version__",
]
