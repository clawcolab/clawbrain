---
name: clawbrain
description: "Claw Brain - Personal AI Memory System for ClawDBot. Provides memory, personality, bonding, and learning capabilities."
metadata: {"clawdbot":{"emoji":"🧠","requires":{"files":["brain_v3.py"]},"install":[{"id":"git","kind":"git","url":"https://github.com/clawcolab/clawbrain.git","label":"Install Claw Brain (git)"}]}}
---

# Claw Brain Skill 🧠

Personal AI Memory System with Soul, Bonding, and Learning.

## Features

- 🎭 **Soul/Personality** - 6 evolving traits (humor, empathy, curiosity, creativity, helpfulness, honesty)
- 👤 **User Profile** - Learns user preferences, interests, communication style
- 💭 **Conversation State** - Real-time mood detection and context tracking
- 📚 **Learning Insights** - Continuously learns from interactions and corrections
- 🧠 **get_full_context()** - Everything for personalized responses

## Setup

```bash
# Install via git
git clone https://github.com/clawcolab/clawbrain.git
```

## Usage

```python
import sys
sys.path.insert(0, "clawbrain")

from brain import Brain

# Initialize (uses PostgreSQL + Redis, falls back to SQLite)
brain = Brain()

# Get full context for personalized responses
context = brain.get_full_context(
    session_key="chat_123",
    user_id="pranab",
    agent_id="jarvis",
    message="Hey, how's it going?"
)

# Returns:
# - User profile (name, preferences, interests)
# - Current mood (happy, neutral, frustrated...)
# - Detected intent (question, command, casual...)
# - Recent memories
# - Response guidance
```

## Files

- `brain_v3.py` - Main Brain class
- `__init__.py` - Exports and skill registration

## Storage Backends

| Backend | Usage |
|---------|-------|
| PostgreSQL | Production - auto-detected |
| Redis | Caching - auto-detected |
| SQLite | Fallback - works out of the box
