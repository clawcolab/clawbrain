# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ClawBrain - Personal AI Memory System for AI agents. Python 3.10+, MIT licensed, v3.0.0.

**Install & develop:**
```bash
pip install -e .                    # editable install from source
pip install psycopg2-binary redis   # optional: PostgreSQL + Redis support
```

**No test suite or linter is currently configured.**

## Architecture

ClawBrain is a single-module library (`clawbrain.py`, ~600 lines) with two package entry points:
- Top-level `__init__.py` imports from `clawbrain` module directly
- `brain/__init__.py` uses `importlib` to load `brain/clawbrain.py` (a copy of the same module)

Both export: `Brain`, `Memory`, `UserProfile`, `Embedder`.

### Brain class

The central class. Key design decisions:
- **Storage abstraction via context manager** (`_get_cursor()`): transparently switches between SQLite and PostgreSQL cursors
- **Auto-detection of backends**: tries PostgreSQL first, falls back to SQLite. Redis used as optional cache layer. Can be forced via `storage_backend` config key.
- **Thread safety**: uses `threading.Lock` for concurrent access
- **Optional dependencies**: `sentence-transformers`, `psycopg2`, `redis` are all try/imported with feature flags (`EMBEDDINGS_AVAILABLE`, `POSTGRES_AVAILABLE`, `REDIS_AVAILABLE`)

### Data flow

`get_full_context(session_key, user_id, agent_id, message)` is the primary API. It assembles:
1. Soul/personality traits (6 evolving traits stored in `souls` table)
2. User profile (preferences, interests, communication style from `user_profiles`)
3. Conversation state (mood/intent detection via keyword matching)
4. Relevant memories (from `memories` table, optionally with embeddings)
5. Learning insights (from `learning_insights` table)

Returns a JSON-serializable dict intended to be injected into LLM prompts.

### Database tables

SQLite/PostgreSQL: `conversations`, `memories`, `todos`, `souls`, `bonds`, `goals`, `user_profiles`, `learning_insights`, `topic_clusters`.

### Key dataclasses

- `Memory` - typed, keyword-indexed memory entries with optional embeddings
- `UserProfile` - user preferences, interests, expertise, communication style

## Environment

Running on Raspberry Pi 5 (headless, Linux/ARM64). OpenClaw agent platform config lives at `~/.openclaw/`.
