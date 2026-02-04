# Claw Brain 🧠

**Personal AI Memory System for AI Agents**

A sophisticated memory and learning system that enables truly personalized AI-human communication.

## Features

- 🎭 **Soul/Personality** - 6 evolving traits (humor, empathy, curiosity, creativity, helpfulness, honesty)
- 👤 **User Profile** - Learns user preferences, interests, communication style
- 💭 **Conversation State** - Real-time mood detection and context tracking
- 📚 **Learning Insights** - Continuously learns from interactions and corrections
- 🧠 **get_full_context()** - Everything for personalized responses

## Installation

### For ClawdBot / OpenClaw (Recommended)

**One-liner install:**
```bash
curl -fsSL https://raw.githubusercontent.com/clawcolab/clawbrain/main/remote-install.sh | bash
```

**Or step-by-step:**
```bash
# Clone to your skills directory
cd ~/.openclaw/skills  # or ~/clawd/skills or ~/.clawdbot/skills
git clone https://github.com/clawcolab/clawbrain.git

# Run the install script
cd clawbrain
./install.sh
```

The install script will:
- Detect your platform (ClawdBot or OpenClaw)
- Install the startup hook automatically
- Check Python dependencies
- Show you how to configure environment variables

**Configure your agent ID** (add to systemd service):
```bash
sudo mkdir -p /etc/systemd/system/clawdbot.service.d  # or openclaw.service.d
sudo tee /etc/systemd/system/clawdbot.service.d/brain.conf << EOF
[Service]
Environment="BRAIN_AGENT_ID=your-agent-name"
EOF
sudo systemctl daemon-reload
sudo systemctl restart clawdbot  # or openclaw
```

### For Python Projects

```bash
pip install git+https://github.com/clawcolab/clawbrain.git
```

## Quick Start

```bash
pip install git+https://github.com/clawcolab/clawbrain.git
```

```python
from clawbrain import Brain

brain = Brain()
context = brain.get_full_context(
    session_key="chat_123",
    user_id="user",
    agent_id="assistant",
    message="Hey, how's it going?"
)
```

## Storage Options

### Option 1: SQLite (Zero Setup) ✅ Recommended for development

```python
from clawbrain import Brain

# Automatically uses SQLite
brain = Brain({"storage_backend": "sqlite"})
```

**Requirements:** Python 3.10+, no external dependencies

**Best for:**
- Development and testing
- Single-user deployments
- Quick prototyping

---

### Option 2: PostgreSQL + Redis (Production) 🚀

```python
from clawbrain import Brain

# Auto-detects PostgreSQL and Redis
brain = Brain()
```

**Requirements:**
- PostgreSQL 14+ (port 5432)
- Redis 6+ (port 6379)
- Python packages: `psycopg2-binary`, `redis`

**Install dependencies:**
```bash
pip install psycopg2-binary redis
```

**Environment variables (optional):**
```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=brain_db
export POSTGRES_USER=brain_user
export POSTGRES_PASSWORD=your_password

export REDIS_HOST=localhost
export REDIS_PORT=6379
```

**Best for:**
- Production deployments
- High-concurrency environments
- Distributed AI agents
- Multi-user platforms

---

### Auto-Detection Order

1. PostgreSQL (if available)
2. Redis (if available, used as cache)
3. SQLite (fallback)

You can also force a specific backend:
```python
brain = Brain({"storage_backend": "postgresql"})  # Force PostgreSQL
brain = Brain({"storage_backend": "sqlite"})      # Force SQLite
```

## Installation Methods

### From GitHub (Recommended)

```bash
pip install git+https://github.com/clawcolab/clawbrain.git
```

### From Local Development

```bash
cd /root/clawd/brain/public_package
pip install -e .
```

### For ClawDBot

```bash
# Install as skill
git clone https://github.com/clawcolab/clawbrain.git ClawBrain
```

Then in your bot:
```python
import sys
sys.path.insert(0, "ClawBrain")
from clawbrain import Brain

brain = Brain()
```

## API Reference

### Core Class

```python
from clawbrain import Brain

brain = Brain()
```

**Methods:**

| Method | Description |
|--------|-------------|
| `get_full_context()` | Get all context for personalized responses |
| `remember()` | Store a memory |
| `recall()` | Retrieve memories |
| `learn_user_preference()` | Learn user preferences |
| `get_user_profile()` | Get user profile |
| `detect_user_mood()` | Detect current mood |
| `detect_user_intent()` | Detect message intent |
| `generate_personality_prompt()` | Generate personality guidance |
| `health_check()` | Check backend connections |
| `close()` | Close connections |

### Data Classes

```python
from clawbrain import Memory, UserProfile

# Memory
memory = Memory(
    id="...",
    agent_id="assistant",
    memory_type="fact",
    key="job",
    content="User works at Walmart",
    importance=0.8
)

# User Profile
profile = UserProfile(
    user_id="user",
    name="Alex",
    interests=["AI", "crypto"],
    communication_preferences={"style": "casual"}
)
```

## Repository Structure

```
clawbrain/
├── clawbrain.py      ← Main module
├── __init__.py       ← Exports
├── SKILL.md          ← ClawDBot skill docs
├── skill.json        ← ClawdHub metadata
└── README.md         ← This file
```

## For ClawDBot

Install as a skill via ClawdHub or manually:

```bash
git clone https://github.com/clawcolab/clawbrain.git ClawBrain
```

Usage in your bot:
```python
import sys
sys.path.insert(0, "ClawBrain")
from clawbrain import Brain

brain = Brain()

# Get context for responses
context = brain.get_full_context(
    session_key=session_id,
    user_id=user_id,
    agent_id=agent_id,
    message=user_message
)
```

## License

MIT
