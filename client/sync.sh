#!/bin/bash
# FileSync Client startup script

set -e

REPO_PATH="${1:-$(pwd)}"
SERVER_URL="${FILESYNC_SERVER:-http://localhost:8000}"
REPO_ID="${FILESYNC_REPO_ID:-}"
CLIENT_ID="${FILESYNC_CLIENT_ID:-}"

echo "FileSync Client"
echo "Repository: $REPO_PATH"
echo "Server: $SERVER_URL"

cd "$(dirname "$0")"
python sync.py \
    --repo "$REPO_PATH" \
    --server "$SERVER_URL" \
    ${REPO_ID:+--repo-id "$REPO_ID"} \
    ${CLIENT_ID:+--client-id "$CLIENT_ID"} \
    "$@"