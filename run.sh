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

SPRITES=(assets/sprites/idle/hornet_idle.png assets/sprites/fast_fall/hornet_fast_fall.png assets/sprites/fast_fall/hornet_fast_fall_wrong.png assets/sprites/sit/hornet_sit_0.png)
NEED_EXTRACT=0
if [ "$1" = "--force" ]; then
    NEED_EXTRACT=1
else
    for f in "${SPRITES[@]}"; do
        [ ! -f "$f" ] && NEED_EXTRACT=1 && break
    done
fi

if [ "$NEED_EXTRACT" = "1" ]; then
    echo "Extracting sprites..."
    python extract_sprite.py ${1:+--force}
else
    echo "Sprites already extracted. (Pass --force to re-extract)"
fi

echo "Launching Hornet..."
python companion.py
