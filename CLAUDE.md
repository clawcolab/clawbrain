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

Running on Raspberry Pi 5 (headless, Linux/ARM64) as user `mccargo`. SSH via `192.168.1.155` (wired) or `192.168.1.160` (wifi).

## OpenClaw Platform (`~/.openclaw/`)

AI agent platform running locally. The active agent is **Molty** (sharp, challenging sparring partner).

### Session startup protocol (AGENTS.md)

Every session reads in order: `SOUL.md` → `USER.md` → `memory/YYYY-MM-DD.md` (today + yesterday) → `MEMORY.md` (main sessions only).

### Workspace layout

```
~/.openclaw/workspace/
├── SOUL.md              # Core identity & operating principles
├── IDENTITY.md          # Molty's identity (name, vibe, emoji)
├── USER.md              # Adam McCargo's profile
├── MEMORY.md            # Curated long-term memory
├── AGENTS.md            # Session startup protocol
├── HEARTBEAT.md         # Periodic check-in rules
├── TOOLS.md             # Local infrastructure notes
├── SETUP-GUIDE.md       # Pi5 headless setup
├── config/
│   ├── model-routing.md # Model selection by task type
│   ├── zai-models.md    # GLM pricing reference
│   ├── mcporter.json    # Zapier MCP config (secrets, chmod 600)
│   └── mcporter-activepieces.json
├── skills/
│   ├── mission-control/   # Kanban task management (GitHub Pages + webhooks)
│   └── marketing-skills/  # 23 marketing modules (copywriting, SEO, ads, etc.)
├── memory/
│   └── YYYY-MM-DD.md      # Daily notes (raw logs)
└── scripts/
    └── check-git-status.sh
```

### Models & providers

- **Primary:** GLM-4.7 via Z.ai ($0.60/$2.20 per 1M input/output)
- **Fast/cheap:** GLM-4.7-Flash ($0.07/$0.40 — 90% cheaper, use for admin/quick Q&A)
- **Creative:** GLM-4.5 (social media, image prompts, branding)
- **Vision:** GLM-4.6V (screenshot analysis)
- **Fallback:** Google Gemini 3 Flash/Pro via Antigravity
- Rate limit: 600 prompts/5h, supports 20+ concurrent sub-agents

### Model routing strategy

- Quick/admin/Q&A → glm-4.7-flash
- Copywriting/proposals/strategy/coding → glm-4.7
- Social media/creative → glm-4.5
- Vision/screenshots → glm-4.6v

### Tools & integrations

- **Zapier MCP:** 90 tools via mcporter (`config/mcporter.json`)
- **ActivePieces:** Automation platform
- **Telegram:** Primary chat channel (bot token + group allowlist)
- **Mission Control:** Kanban board with GitHub Pages dashboard, Tailscale Funnel webhooks
- **Marketing skills:** 23 reference modules at `skills/marketing-skills/references/` (ab-test-setup, analytics-tracking, competitor-alternatives, copy-editing, copywriting, email-sequence, form-cro, free-tool-strategy, launch-strategy, marketing-ideas, marketing-psychology, onboarding-cro, page-cro, paid-ads, paywall-upgrade-cro, popup-cro, pricing-strategy, programmatic-seo, referral-program, schema-markup, seo-audit, signup-flow-cro, social-content)

### Safety rules (from AGENTS.md)

- **Do freely:** Read files, explore, organize, search web, work within workspace
- **Ask first:** Sending emails/tweets/public posts, anything leaving the machine
- **Never:** Exfiltrate private data
- Use `trash` over `rm` (recoverable > gone forever)

### Memory system

- **Daily notes** (`memory/YYYY-MM-DD.md`): Raw session logs, auto-created
- **Long-term memory** (`MEMORY.md`): Manually curated, loaded only in main sessions (not group chats)
- **Key rule:** Write everything to files — mental notes don't survive session restarts

### Heartbeat protocol

- Batch periodic checks (git status, calendar, emails)
- Quiet hours: 23:00–08:00 unless urgent
- Proactive work allowed: organize memory, update docs, commit own changes
- Memory maintenance: periodically distill daily notes into MEMORY.md
