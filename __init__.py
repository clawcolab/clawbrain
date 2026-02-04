"""
Claw Brain v3 - Personal AI Memory System for AI Agents

A sophisticated memory and learning system that enables truly personalized AI-human communication.

Features:
- 🎭 Soul/Personality - Evolving personality traits
- 👤 User Profile - Learns preferences, interests, communication style
- 💭 Conversation State - Real-time mood/intent detection
- 📚 Learning Insights - Continuous improvement from interactions
- 🔄 Auto-Refresh - Automatic memory refresh on OpenClaw restart

Install: pip install git+https://github.com/clawcolab/clawbrain.git

OpenClaw Integration:
    cd ~/.openclaw/plugins
    git clone https://github.com/clawcolab/clawbrain.git
    openclaw config set plugins.entries.clawbrain.enabled true
    openclaw hooks enable boot-md
"""

__version__ = "3.0.0"
__author__ = "ClawColab"

# Core exports
from clawbrain import (
    Brain,
    Memory,
    UserProfile,
    Embedder
)

__all__ = ["Brain", "Memory", "UserProfile", "Embedder"]
