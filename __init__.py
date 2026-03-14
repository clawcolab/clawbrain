"""
ClawBrain - Enterprise AI Memory System for AI Agents

Features:
- 🧠 Hybrid Retrieval - Semantic + keyword + recency + importance scoring
- 📥 Conversation Ingestion - Auto-extract memories with rule-based or LLM extraction
- 🔀 Deduplication - Merge near-duplicate memories on ingest
- 🎭 Soul/Personality - 6-trait evolving personality system
- 👤 User Profile - Learns preferences, interests, communication style
- 📊 Memory Scopes - Private, shared, team, user scoping for multi-agent
- 📝 Audit Log - Track all memory mutations for debugging
- 🗜️ Session Consolidation - Summarize and extract long-term memories
- 🔐 Encrypted Secrets - Fernet encryption for sensitive data
- ⏱️ Retention Policies - TTL, importance decay, auto-cleanup

Install: pip install clawbrain[all]
Setup:   clawbrain setup
"""

__version__ = "0.3.0"
__author__ = "ClawColab"

# Core exports
from clawbrain import (
    Brain,
    Memory,
    ScoredMemory,
    UserProfile,
    Embedder,
    get_bridge_script_path,
    VALID_MEMORY_KINDS,
    VALID_DURABILITIES,
    VALID_SCOPES,
)

__all__ = [
    "Brain", "Memory", "ScoredMemory", "UserProfile", "Embedder",
    "get_bridge_script_path",
    "VALID_MEMORY_KINDS", "VALID_DURABILITIES", "VALID_SCOPES",
]
