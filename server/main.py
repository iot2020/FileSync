"""FileSync Server - Central server for git file synchronization."""
import os
import json
import base64
import hashlib
import time
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import shared models (copied to /app/shared in Docker)
from shared.models import (
    FileInfo, FileStatus, SyncManifest, UploadRequest, UploadResponse,
    DownloadRequest, DownloadResponse, RepoInfo, ServerStats
)


# Configuration
DATA_DIR = Path(os.environ.get("FILESYNC_DATA_DIR", "/tmp/filesync_data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPOS_DIR = DATA_DIR / "repos"
REPOS_DIR.mkdir(parents=True, exist_ok=True)

METADATA_FILE = DATA_DIR / "metadata.json"
SERVER_START_TIME = time.time()


# In-memory metadata cache
metadata: Dict[str, RepoInfo] = {}


def load_metadata():
    """Load metadata from disk."""
    global metadata
    if METADATA_FILE.exists():
        with open(METADATA_FILE, 'r') as f:
            data = json.load(f)
            metadata = {k: RepoInfo(**v) for k, v in data.items()}


def save_metadata():
    """Save metadata to disk."""
    with open(METADATA_FILE, 'w') as f:
        json.dump({k: v.model_dump() for k, v in metadata.items()}, f, indent=2)


def get_repo_path(repo_id: str) -> Path:
    """Get the storage path for a repository."""
    return REPOS_DIR / repo_id


def compute_sha256(content: bytes) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    load_metadata()
    yield
    save_metadata()


app = FastAPI(
    title="FileSync Server",
    description="Central server for git repository file synchronization",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "uptime": time.time() - SERVER_START_TIME}


@app.get("/stats", response_model=ServerStats)
async def get_stats():
    """Get server statistics."""
    total_files = sum(r.file_count for r in metadata.values())
    total_size = sum(r.size_bytes for r in metadata.values())
    return ServerStats(
        total_repos=len(metadata),
        total_files=total_files,
        total_size_bytes=total_size,
        uptime_seconds=time.time() - SERVER_START_TIME
    )


@app.get("/repos", response_model=List[RepoInfo])
async def list_repos():
    """List all repositories."""
    return list(metadata.values())


@app.get("/repos/{repo_id}", response_model=RepoInfo)
async def get_repo(repo_id: str):
    """Get repository information."""
    if repo_id not in metadata:
        raise HTTPException(status_code=404, detail="Repository not found")
    return metadata[repo_id]


@app.post("/repos/{repo_id}/upload", response_model=UploadResponse)
async def upload_files(repo_id: str, request: UploadRequest):
    """Upload files to repository."""
    manifest = request.manifest
    files = request.files
    
    # Verify repo exists or create new
    repo_path = get_repo_path(repo_id)
    repo_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize repo metadata if new
    if repo_id not in metadata:
        metadata[repo_id] = RepoInfo(
            repo_id=repo_id,
            name=manifest.repo_id,
            created_at=time.time(),
            updated_at=time.time()
        )
    
    repo_info = metadata[repo_id]
    uploaded = []
    failed = {}
    
    # Process each file
    for file_info in manifest.files:
        file_path = file_info.path
        
        if file_info.status == FileStatus.DELETED:
            # Handle deletion
            target_path = repo_path / file_path
            if target_path.exists():
                target_path.unlink()
                uploaded.append(file_path)
            else:
                failed[file_path] = "File not found for deletion"
            continue
        
        # For added/modified files, expect content in request
        if file_path not in files:
            failed[file_path] = "File content not provided"
            continue
        
        try:
            # Decode base64 content
            content = base64.b64decode(files[file_path])
            
            # Verify hash if provided
            if file_info.sha256:
                computed_hash = compute_sha256(content)
                if computed_hash != file_info.sha256:
                    failed[file_path] = f"Hash mismatch: expected {file_info.sha256}, got {computed_hash}"
                    continue
            
            # Write file
            target_path = repo_path / file_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            async with aiofiles.open(target_path, 'wb') as f:
                await f.write(content)
            
            uploaded.append(file_path)
            
        except Exception as e:
            failed[file_path] = str(e)
    
    # Update repo metadata
    repo_info.updated_at = time.time()
    repo_info.head_commit = manifest.head_commit
    repo_info.file_count = len(list(repo_path.rglob("*"))) if repo_path.exists() else 0
    repo_info.size_bytes = sum(f.stat().st_size for f in repo_path.rglob("*") if f.is_file())
    
    save_metadata()
    
    return UploadResponse(
        success=len(failed) == 0,
        message=f"Uploaded {len(uploaded)} files, {len(failed)} failed",
        uploaded_files=uploaded,
        failed_files=failed
    )


@app.post("/repos/{repo_id}/download", response_model=DownloadResponse)
async def download_files(repo_id: str, request: DownloadRequest):
    """Download files from repository."""
    if repo_id not in metadata:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    repo_path = get_repo_path(repo_id)
    if not repo_path.exists():
        raise HTTPException(status_code=404, detail="Repository data not found")
    
    repo_info = metadata[repo_id]
    
    # Build manifest of available files
    files_to_send = {}
    file_infos = []
    
    # Determine which files to include
    if request.file_paths:
        # Specific files requested
        for file_path in request.file_paths:
            full_path = repo_path / file_path
            if full_path.exists() and full_path.is_file():
                async with aiofiles.open(full_path, 'rb') as f:
                    content = await f.read()
                files_to_send[file_path] = base64.b64encode(content).decode('utf-8')
                
                stat = full_path.stat()
                file_infos.append(FileInfo(
                    path=file_path,
                    status=FileStatus.UNCHANGED,
                    size=stat.st_size,
                    sha256=compute_sha256(content),
                    mtime=stat.st_mtime
                ))
    else:
        # All files (or since commit - simplified to all for now)
        for full_path in repo_path.rglob("*"):
            if full_path.is_file():
                rel_path = full_path.relative_to(repo_path).as_posix()
                async with aiofiles.open(full_path, 'rb') as f:
                    content = await f.read()
                files_to_send[rel_path] = base64.b64encode(content).decode('utf-8')
                
                stat = full_path.stat()
                file_infos.append(FileInfo(
                    path=rel_path,
                    status=FileStatus.UNCHANGED,
                    size=stat.st_size,
                    sha256=compute_sha256(content),
                    mtime=stat.st_mtime
                ))
    
    manifest = SyncManifest(
        repo_id=repo_id,
        client_id=request.client_id,
        base_commit=request.since_commit,
        head_commit=repo_info.head_commit,
        files=file_infos,
        timestamp=time.time()
    )
    
    return DownloadResponse(
        success=True,
        message=f"Prepared {len(files_to_send)} files for download",
        manifest=manifest,
        files=files_to_send
    )


@app.delete("/repos/{repo_id}")
async def delete_repo(repo_id: str):
    """Delete a repository."""
    if repo_id not in metadata:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    repo_path = get_repo_path(repo_id)
    if repo_path.exists():
        shutil.rmtree(repo_path)
    
    del metadata[repo_id]
    save_metadata()
    
    return {"success": True, "message": f"Repository {repo_id} deleted"}


@app.get("/repos/{repo_id}/files/{file_path:path}")
async def get_file(repo_id: str, file_path: str):
    """Get a single file directly (for simple HTTP access)."""
    if repo_id not in metadata:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    repo_path = get_repo_path(repo_id)
    full_path = repo_path / file_path
    
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(full_path)


# Import aiofiles here to avoid circular import issues
import aiofiles


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)