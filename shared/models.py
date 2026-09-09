"""Shared data models for FileSync system."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
import hashlib
import json


class FileStatus(str, Enum):
    """File status in git repository."""
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    UNTRACKED = "untracked"
    UNCHANGED = "unchanged"


class FileInfo(BaseModel):
    """Information about a file in the repository."""
    path: str = Field(..., description="Relative path from repo root")
    status: FileStatus = Field(..., description="Git status of the file")
    size: int = Field(0, description="File size in bytes")
    sha256: Optional[str] = Field(None, description="SHA256 hash of file content")
    mode: Optional[str] = Field(None, description="File mode/permissions")
    mtime: Optional[float] = Field(None, description="Modification timestamp")


class SyncManifest(BaseModel):
    """Manifest of files to sync."""
    repo_id: str = Field(..., description="Unique repository identifier")
    client_id: str = Field(..., description="Unique client identifier")
    base_commit: Optional[str] = Field(None, description="Base commit hash for incremental sync")
    head_commit: Optional[str] = Field(None, description="Current HEAD commit hash")
    files: List[FileInfo] = Field(default_factory=list, description="List of files to sync")
    timestamp: float = Field(..., description="Manifest creation timestamp")


class UploadRequest(BaseModel):
    """Request to upload files."""
    manifest: SyncManifest
    files: Dict[str, str] = Field(..., description="Base64 encoded file contents keyed by path")


class UploadResponse(BaseModel):
    """Response from upload."""
    success: bool
    message: str
    uploaded_files: List[str] = Field(default_factory=list)
    failed_files: Dict[str, str] = Field(default_factory=dict)


class DownloadRequest(BaseModel):
    """Request to download files."""
    repo_id: str
    client_id: str
    since_commit: Optional[str] = None
    file_paths: Optional[List[str]] = None


class DownloadResponse(BaseModel):
    """Response from download."""
    success: bool
    message: str
    manifest: Optional[SyncManifest] = None
    files: Dict[str, str] = Field(default_factory=dict, description="Base64 encoded file contents")


class RepoInfo(BaseModel):
    """Repository information stored on server."""
    repo_id: str
    name: str
    created_at: float
    updated_at: float
    head_commit: Optional[str] = None
    size_bytes: int = 0
    file_count: int = 0


class ServerStats(BaseModel):
    """Server statistics."""
    total_repos: int
    total_files: int
    total_size_bytes: int
    uptime_seconds: float