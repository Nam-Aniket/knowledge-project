#!/bin/bash
set -e

echo "🧠 Setting up Psyche RAG Engine..."

# 1. Initialize Virtual Environment if not exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# 2. Activate and install in editable mode
echo "Installing package and dependencies in editable mode..."
.venv/bin/pip install -e .

# 3. Create global symlink
echo "Registering global 'psyche' command..."
if [ -d "/opt/homebrew/bin" ]; then
    ln -sf "$(pwd)/.venv/bin/psyche" "/opt/homebrew/bin/psyche"
    echo "✅ Success! 'psyche' command linked to /opt/homebrew/bin/psyche"
elif [ -d "/usr/local/bin" ]; then
    ln -sf "$(pwd)/.venv/bin/psyche" "/usr/local/bin/psyche"
    echo "✅ Success! 'psyche' command linked to /usr/local/bin/psyche"
elif [ -d "$HOME/.local/bin" ]; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$(pwd)/.venv/bin/psyche" "$HOME/.local/bin/psyche"
    echo "✅ Success! 'psyche' command linked to $HOME/.local/bin/psyche"
else
    echo "⚠️  Could not find a standard global bin directory in your PATH (/opt/homebrew/bin, /usr/local/bin, or ~/.local/bin)."
    echo "You can run psyche using: $(pwd)/.venv/bin/psyche"
fi
