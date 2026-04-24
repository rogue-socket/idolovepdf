#!/usr/bin/env bash
# setup.sh – Create the 'lovepdf' virtual environment and install dependencies.
set -euo pipefail

VENV_NAME="lovepdf"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/$VENV_NAME"

echo "======================================"
echo "  pdftool setup"
echo "======================================"
echo

# ── Check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.9 or later and try again." >&2
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 9 ]]; }; then
    echo "Error: Python 3.9+ required (found $PY_VERSION)." >&2
    exit 1
fi
echo "Python $PY_VERSION detected."

# ── Create virtual environment ────────────────────────────────────────────────
if [[ -d "$VENV_PATH" ]]; then
    echo "Virtual environment '$VENV_NAME/' already exists – skipping creation."
else
    echo "Creating virtual environment '$VENV_NAME/'..."
    python3 -m venv "$VENV_PATH"
    echo "Done."
fi

# ── Install dependencies ──────────────────────────────────────────────────────
echo
echo "Installing dependencies from requirements.txt..."
"$VENV_PATH/bin/pip" install --upgrade pip --quiet
"$VENV_PATH/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
echo "Dependencies installed."

# ── Make pdftool.py executable ────────────────────────────────────────────────
chmod +x "$SCRIPT_DIR/pdftool.py"

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "======================================"
echo "  Setup complete!"
echo "======================================"
echo
echo "To use pdftool, activate the virtual environment first:"
echo
echo "    source $VENV_NAME/bin/activate"
echo
echo "Then run:"
echo
echo "    python pdftool.py --help"
echo "    python server.py"
echo "    python pdftool.py merge a.pdf b.pdf -o combined.pdf"
echo "    python pdftool.py split input.pdf -p 1-3,5 -o output.pdf"
echo "    python pdftool.py rotate input.pdf -d 90 -o rotated.pdf"
echo "    python pdftool.py pagenumbers input.pdf -o numbered.pdf"
echo "    python pdftool.py reorder input.pdf -p 3,1,2 -o reordered.pdf"
echo "    python pdftool.py compress input.pdf -o compressed.pdf"
echo "    python pdftool.py watermark input.pdf --text 'DRAFT' -o watermarked.pdf"
echo "    python pdftool.py toimage input.pdf -o images/"
echo "    python pdftool.py topdf img1.jpg img2.png -o output.pdf"
echo
echo "Or run directly (shebang uses system python3 — activate venv first):"
echo
echo "    ./pdftool.py --help"
echo
