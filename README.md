# FileSync - Git Repository File Synchronization System

A system for synchronizing modified files from git repositories between clients via a central server.

## Architecture

```
Client A (git repo) → Server ← Client B (git repo)
     ↑                    ↓
  Upload              Download
```

## Components

- **Server**: FastAPI-based HTTP server for file storage and metadata
- **Client**: CLI tool for detecting git changes, uploading, and syncing

## Features

- Detect modified/new/deleted files in git repository
- Upload only changed files (incremental sync)
- File integrity verification (SHA256)
- Conflict detection
- Cross-platform support

## Quick Start

### Server
```bash
cd server
pip install -r requirements.txt
python main.py
```

### Client
```bash
cd client
pip install -r requirements.txt
python sync.py --repo /path/to/git/repo --server http://localhost:8000
```