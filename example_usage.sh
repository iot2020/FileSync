#!/bin/bash
# Example usage script for FileSync system

set -e

echo "=== FileSync Example ==="
echo

# Create a test git repository
TEST_DIR="/tmp/filesync_test"
REPO_A="$TEST_DIR/repo_a"
REPO_B="$TEST_DIR/repo_b"
SERVER_URL="http://localhost:8000"

cleanup() {
    echo "Cleaning up..."
    docker-compose down -v 2>/dev/null || true
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

echo "1. Starting server with docker-compose..."
docker-compose up -d

echo "2. Waiting for server to be ready..."
for i in {1..30}; do
    if curl -s "$SERVER_URL/health" > /dev/null; then
        echo "   Server ready!"
        break
    fi
    sleep 1
done

echo "3. Creating test repository A..."
mkdir -p "$REPO_A"
cd "$REPO_A"
git init -q
git config user.email "test@example.com"
git config user.name "Test User"

echo "# Project README" > README.md
echo "console.log('Hello from FileSync');" > app.js
mkdir -p src
echo "export const VERSION = '1.0.0';" > src/version.ts

git add .
git commit -m "Initial commit" -q

echo "4. Cloning to repository B..."
git clone "$REPO_A" "$REPO_B" -q
cd "$REPO_B"
git config user.email "test2@example.com"
git config user.name "Test User 2"

echo "5. Making changes in repository A..."
cd "$REPO_A"
echo "// New feature" >> app.js
echo "export const FEATURE = true;" >> src/version.ts
echo "New file" > NEW_FILE.txt
git add .
git commit -m "Add new feature" -q

echo "6. Syncing repository A to server..."
cd "$REPO_A"
python client/sync.py --repo "$REPO_A" --server "$SERVER_URL" --upload-only

echo "7. Syncing repository B from server..."
cd "$REPO_B"
python client/sync.py --repo "$REPO_B" --server "$SERVER_URL" --download-only

echo "8. Verifying sync..."
echo "   Repository A files:"
ls -la "$REPO_A"
echo
echo "   Repository B files:"
ls -la "$REPO_B"
echo
echo "   Checking app.js content in B:"
cat "$REPO_B/app.js"
echo
echo "   Checking NEW_FILE.txt in B:"
cat "$REPO_B/NEW_FILE.txt"

echo
echo "=== Example completed successfully! ==="