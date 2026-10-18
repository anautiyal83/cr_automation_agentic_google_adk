#!/bin/bash
# Start CR Automation ADK Pipeline Server
cd "$(dirname "$0")"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Ensure data directories
mkdir -p data/uploads data/runs data/logs

# Set PYTHONPATH
export PYTHONPATH="$(pwd):D:/my_workspace/cr_automation-agentic"
export PYTHONWARNINGS="ignore"

echo "============================================"
echo "  CR Automation ADK Pipeline Server"
echo "============================================"
echo "  URL:  http://localhost:8000"
echo "============================================"

python -u -W ignore src/ui/app.py
