# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ClawBrain - Personal AI Memory System for AI agents. Python 3.10+, MIT licensed.

**Install:**
```bash
pip install clawbrain                          # basic
pip install clawbrain[encryption]              # with Fernet encryption
pip install clawbrain[embeddings]              # with sentence-transformers
pip install clawbrain[postgres,redis]          # with PostgreSQL + Redis
pip install clawbrain[all]                     # everything
```

**Develop:**
```bash
pip install -e .  # editable install from source
```

**No test suite or linter is currently configured.**

## Architecture

ClawBrain is a single-module library with one source of truth:

- **`clawbrain.py`** - The main module (~800 lines), contains all classes and logic
- **`__init__.py`** - Re-exports from `clawbrain` module
- **`brain/__init__.py`** - Also re-exports from root `clawbrain` module (for backward compatibility)

Exports: `Brain`, `Memory`, `UserProfile`, `Embedder`, `get_bridge_script_path`

### Brain class

The central class. Key design decisions:

- **Storage abstraction via context manager** (`_get_cursor()`): transparently switches between SQLite and PostgreSQL cursors
- **Auto-detection of backends**: tries PostgreSQL first, falls back to SQLite. Redis used as optional cache layer
- **Thread safety**: uses `threading.Lock` for concurrent access
- **Encryption support**: Fernet encryption for sensitive memory content (opt-in via `content_encrypted=True`)
- **Optional dependencies**: `sentence-transformers`, `psycopg2`, `redis`, `cryptography` are all try/imported with feature flags

### Key methods

- `remember(agent_id, content, memory_type, key, ...)` - Store a memory
- `recall(agent_id, query, memory_type, limit, ...)` - Retrieve memories
- `forget(memory_id)` - Delete a memory
- `get_full_context(session_key, user_id, agent_id, message)` - Assemble full context for LLM prompts
- `refresh_on_startup(agent_id)` - Called on agent startup to refresh state
- `import_personality(agent_id, soul_path, identity_path, user_path, memory_path)` - Import personality files

### Data flow

`get_full_context()` assembles:
1. Soul/personality traits (from `souls` table)
2. User profile (from `user_profiles` table)
3. Conversation state (mood/intent detection)
4. Relevant memories (from `memories` table, optionally with embeddings)
5. Learning insights (from `learning_insights` table)

Returns a JSON-serializable dict for LLM prompt injection.

### Database tables

SQLite/PostgreSQL: `conversations`, `memories`, `todos`, `souls`, `bonds`, `goals`, `user_profiles`, `learning_insights`, `topic_clusters`.

### Key dataclasses

- `Memory` - Typed, keyword-indexed memory entries with optional embeddings and encryption
- `UserProfile` - User preferences, interests, expertise, communication style

## File Structure

```
clawbrain/
├── clawbrain.py          # Single source of truth - all logic here
├── __init__.py           # Re-exports from clawbrain
├── pyproject.toml        # Package config, dependencies, CLI entry points
├── brain/
│   ├── __init__.py       # Re-exports from root clawbrain (backward compat)
│   └── scripts/
│       └── brain_bridge.py   # Bridge script for Node.js integration
├── hooks/
│   └── clawbrain-startup/
│       └── handler.js    # ClawdBot gateway:startup hook
└── scripts/
    └── brain_bridge.py   # Bridge script (source location)
```

## CLI Commands

```bash
clawbrain init              # Initialize encryption key
clawbrain import-personality --agent-id <id> [--soul PATH] [--identity PATH] [--user PATH] [--memory PATH]
```

## Integration with ClawdBot

The `handler.js` hook runs on `gateway:startup` event:
1. Locates Python and `brain_bridge.py` script
2. Calls `Brain.refresh_on_startup(agent_id)`
3. Logs startup context to ClawdBot

`get_bridge_script_path()` finds the bridge script in pip-installed packages.

## Environment Variables

- `CLAWBRAIN_DB_PATH` - Custom SQLite path (default: `./brain_data.db`)
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string

## Security Notes

- Encryption key stored at `~/.config/clawbrain/.brain_key`
- Use `content_encrypted=True` when storing sensitive memories
- Never commit `.brain_key` or database files to git
