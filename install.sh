#!/bin/bash
set -e

echo "🧠 Installing Psyche RAG Engine..."

INSTALL_DIR="$HOME/.psyche"

if [ -d "$INSTALL_DIR" ]; then
    echo "Existing installation found at $INSTALL_DIR. Updating..."
    cd "$INSTALL_DIR"
    git pull
else
    echo "Cloning Psyche repository to $INSTALL_DIR..."
    git clone https://github.com/Nam-Aniket/knowledge-project.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Run the Python-based setup script
python3 cli.py setup
