#!/bin/bash
# FileSync Server startup script

set -e

DATA_DIR="${FILESYNC_DATA_DIR:-/tmp/filesync_data}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

echo "Starting FileSync Server..."
echo "Data directory: $DATA_DIR"
echo "Listening on: $HOST:$PORT"

export FILESYNC_DATA_DIR="$DATA_DIR"

cd "$(dirname "$0")"
python main.py --host "$HOST" --port "$PORT"