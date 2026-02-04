#!/bin/bash
# ClawBrain Remote Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/clawcolab/clawbrain/main/remote-install.sh | bash

set -e

echo "🧠 ClawBrain Remote Installer"
echo "=============================="

# Detect skills directory - check for existing skills directories first
SKILLS_DIR=""

# Check existing skills directories in priority order
for dir in "$HOME/clawd/skills" "$HOME/.openclaw/skills" "$HOME/.clawdbot/skills"; do
    if [ -d "$dir" ]; then
        SKILLS_DIR="$dir"
        break
    fi
done

# If no skills dir found, create one based on config
if [ -z "$SKILLS_DIR" ]; then
    if [ -d "$HOME/clawd" ]; then
        mkdir -p "$HOME/clawd/skills"
        SKILLS_DIR="$HOME/clawd/skills"
    elif [ -d "$HOME/.openclaw" ]; then
        mkdir -p "$HOME/.openclaw/skills"
        SKILLS_DIR="$HOME/.openclaw/skills"
    elif [ -d "$HOME/.clawdbot" ]; then
        mkdir -p "$HOME/.clawdbot/skills"
        SKILLS_DIR="$HOME/.clawdbot/skills"
    else
        # Default fallback
        mkdir -p "$HOME/clawd/skills"
        SKILLS_DIR="$HOME/clawd/skills"
    fi
fi

echo "📁 Installing to: $SKILLS_DIR/clawbrain"

# Clone or update
if [ -d "$SKILLS_DIR/clawbrain" ]; then
    echo "📥 Updating existing installation..."
    cd "$SKILLS_DIR/clawbrain"
    git fetch --all
    git checkout feature/openclaw-plugin-integration 2>/dev/null || git checkout main
    git pull
else
    echo "📥 Cloning clawbrain..."
    git clone -b feature/openclaw-plugin-integration https://github.com/clawcolab/clawbrain.git "$SKILLS_DIR/clawbrain" 2>/dev/null || \
    git clone https://github.com/clawcolab/clawbrain.git "$SKILLS_DIR/clawbrain"
    cd "$SKILLS_DIR/clawbrain"
fi

# Run local installer
chmod +x install.sh
./install.sh
