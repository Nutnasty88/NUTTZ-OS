#!/bin/bash

echo
echo "========================================="
echo "      NUTTZ OS Core v0.2"
echo "========================================="
echo

cd ~/NUTTZ-OS/core/backend || exit

echo "[1/4] Activating Python environment..."
source venv/bin/activate

echo "[2/4] Checking Python..."
python --version

echo "[3/4] Checking FastAPI..."
python -c "import fastapi"

echo "[4/4] Starting NUTTZ Core..."
echo
echo "Dashboard:"
echo "http://127.0.0.1:8000/docs"
echo
echo "API:"
echo "http://127.0.0.1:8000/api/system"
echo

fastapi dev app/main.py
