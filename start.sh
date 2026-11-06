#!/bin/bash
# Start the Azalio Autonomous Network Platform server
cd "$(dirname "$0")"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Ensure data directories
mkdir -p data/uploads data/runs data/logs logs

# Set PYTHONPATH
export PYTHONPATH="$(pwd)"
export PYTHONWARNINGS="ignore"

echo "============================================"
echo "  Azalio Autonomous Network Platform"
echo "============================================"
echo "  URL:  http://localhost:8000"
echo "============================================"

python -u -W ignore ui/app.py
