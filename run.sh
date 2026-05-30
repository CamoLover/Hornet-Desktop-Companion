#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

VENV=".venv"

if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

echo "Installing requirements..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "Launching Hornet..."
python companion.py
