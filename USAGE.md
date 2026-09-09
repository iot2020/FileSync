# FileSync - Usage Guide

## Overview

FileSync is a system for synchronizing modified files from git repositories between multiple clients via a central server.

```
┌─────────────┐     Upload      ┌─────────┐     Download      ┌─────────────┐
│  Client A   │ ──────────────► │ Server  │ ────────────────► │  Client B   │
│  (git repo) │                 │         │                   │  (git repo) │
└─────────────┘                 └─────────┘                   └─────────────┘
       ▲                                                        │
       │                                                        ▼
       └──────────────── Download ◄────────────────────────────┘
                    (bidirectional)
```

## Quick Start

### 1. Start the Server

```bash
# Using Docker (recommended)
cd /home/quant/work/fileshare
docker-compose up -d

# Or run directly
cd server
source ../venv/bin/activate
PORT=8000 python main.py
```

The server will be available at `http://localhost:8000`

### 2. Configure a Repository

```bash
# Navigate to your git repository
cd /path/to/your/git/repo

# Upload local changes to server
python /home/quant/work/fileshare/client/sync.py \
    --repo . \
    --server http://localhost:8000 \
    --repo-id my-project \
    --upload-only
```

### 3. Sync on Another Machine

```bash
# Clone the repository (or copy it)
git clone <repo-url> /path/to/repo
cd /path/to/repo

# Download changes from server
python /home/quant/work/fileshare/client/sync.py \
    --repo . \
    --server http://localhost:8000 \
    --repo-id my-project \
    --download-only
```

### 4. Full Bidirectional Sync

```bash
# Upload local changes AND download remote changes
python /home/quant/work/fileshare/client/sync.py \
    --repo . \
    --server http://localhost:8000 \
    --repo-id my-project
```

## Command Line Options

```
usage: sync.py [-h] --repo REPO [--server SERVER] [--repo-id REPO_ID]
               [--client-id CLIENT_ID] [--upload-only] [--download-only]
               [--dry-run] [--auto-commit]

Options:
  --repo, -r REPO          Path to git repository (required)
  --server, -s SERVER      Server URL (default: http://localhost:8000)
  --repo-id REPO_ID        Custom repository ID (auto-generated if not provided)
  --client-id CLIENT_ID    Custom client ID (auto-generated if not provided)
  --upload-only, -u        Only upload local changes
  --download-only, -d      Only download remote changes
  --dry-run, -n            Show what would be done without making changes
  --auto-commit            Auto-commit after download (not implemented yet)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FILESYNC_SERVER` | Default server URL | `http://localhost:8000` |
| `FILESYNC_REPO_ID` | Default repository ID | Auto-generated |
| `FILESYNC_CLIENT_ID` | Default client ID | Auto-generated |
| `FILESYNC_DATA_DIR` | Server data directory | `/tmp/filesync_data` |

## How It Works

### Change Detection

The client uses `git status --porcelain=v1 -u` to detect:
- **Modified files** - Tracked files with changes
- **Added files** - New files staged with `git add`
- **Deleted files** - Tracked files that were removed
- **Untracked files** - New files not yet added to git

### Upload Process

1. Scan repository for changes using `git status`
2. Compute SHA256 hash for each changed file
3. Create manifest with file metadata (path, status, size, hash)
4. Send manifest + file contents (base64 encoded) to server
5. Server stores files and updates repository metadata
6. Client saves sync state (HEAD commit) for incremental sync

### Download Process

1. Request files from server (optionally since last sync commit)
2. Server returns manifest + file contents
3. Client applies changes to working directory:
   - Creates/updates files
   - Deletes files marked as deleted
   - Verifies SHA256 hashes
4. Client updates sync state

### Conflict Handling

- Server stores files from multiple clients independently
- Last upload wins for the same file
- Clients should sync frequently to avoid conflicts
- Use `--dry-run` to preview changes before applying

## Repository ID

The repository ID identifies which repository on the server to sync with. By default, it's generated from the git remote URL. For cloned repositories, use `--repo-id` to ensure both clients use the same ID:

```bash
# On first machine
python sync.py --repo . --repo-id my-project --upload-only

# On second machine (after cloning)
python sync.py --repo . --repo-id my-project --download-only
```

## Server API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/stats` | Server statistics |
| GET | `/repos` | List all repositories |
| GET | `/repos/{repo_id}` | Get repository info |
| POST | `/repos/{repo_id}/upload` | Upload files |
| POST | `/repos/{repo_id}/download` | Download files |
| DELETE | `/repos/{repo_id}` | Delete repository |
| GET | `/repos/{repo_id}/files/{path}` | Direct file access |

## Data Storage

Server stores data in `FILESYNC_DATA_DIR`:
```
data/
├── metadata.json          # Repository metadata
└── repos/
    └── {repo_id}/         # Repository files
        ├── file1.txt
        ├── subdir/
        │   └── file2.js
        └── .filesync/     # Client sync state (per client)
```

## Docker Deployment

```bash
# Build and start
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Stop and remove data
docker-compose down -v
```

## Example Workflow

### Team Collaboration

```bash
# Developer A: Initial setup
cd ~/projects/myapp
python sync.py --repo . --server http://sync.company.com --repo-id myapp --upload-only

# Developer B: Join project
git clone git@github.com:company/myapp.git
cd myapp
python sync.py --repo . --server http://sync.company.com --repo-id myapp --download-only

# Developer A: Daily work
# ... make changes ...
python sync.py --repo . --server http://sync.company.com --repo-id myapp

# Developer B: Get latest
python sync.py --repo . --server http://sync.company.com --repo-id myapp
```

### Backup/Sync Personal Projects

```bash
# Laptop: Push changes before leaving work
python sync.py --repo ~/projects/personal --server http://home-server:8000 --repo-id personal

# Home desktop: Pull changes
python sync.py --repo ~/projects/personal --server http://home-server:8000 --repo-id personal
```

## Troubleshooting

### "No changes detected"
- Changes must be uncommitted (not staged or committed)
- Use `git status` to verify

### "Repository not found"
- Check `--repo-id` matches on both client and server
- Verify server has the repository: `curl http://server:8000/repos`

### "Hash mismatch"
- File was modified during transfer
- Re-run sync to retry

### "Connection refused"
- Verify server is running: `curl http://server:8000/health`
- Check firewall/network settings

## Limitations

- Only syncs uncommitted changes (working directory)
- Does not sync git history/commits
- No built-in conflict resolution (last write wins)
- Large files stored in memory during transfer
- No authentication/authorization (add reverse proxy for production)

## Future Improvements

- [ ] Git commit history sync
- [ ] Conflict detection and resolution
- [ ] Authentication and access control
- [ ] Web UI for repository management
- [ ] Incremental sync using commit history
- [ ] File locking for concurrent edits
- [ ] Compression for large files
- [ ] Webhook notifications