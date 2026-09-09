"""FileSync Client - Synchronize git repository changes with server."""
import os
import sys
import json
import base64
import hashlib
import time
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict

import requests
from tqdm import tqdm

sys.path.append(str(Path(__file__).parent.parent / "shared"))
from models import (
    FileInfo, FileStatus, SyncManifest, UploadRequest, UploadResponse,
    DownloadRequest, DownloadResponse, RepoInfo
)


@dataclass
class SyncConfig:
    """Client configuration."""
    server_url: str
    repo_path: Path
    repo_id: Optional[str] = None
    client_id: Optional[str] = None
    auto_commit: bool = False
    dry_run: bool = False


class GitRepo:
    """Wrapper for git operations."""
    
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path.resolve()
        if not (self.repo_path / ".git").exists():
            raise ValueError(f"Not a git repository: {repo_path}")
    
    def get_repo_id(self) -> str:
        """Generate a unique repo ID from remote URL or path."""
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=self.repo_path, capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip()
                # Create a hash from the URL
                return hashlib.sha256(url.encode()).hexdigest()[:16]
        except Exception:
            pass
        # Fallback to path hash
        return hashlib.sha256(str(self.repo_path).encode()).hexdigest()[:16]
    
    def get_head_commit(self) -> Optional[str]:
        """Get current HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_path, capture_output=True, text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None
    
    def get_status(self) -> List[Tuple[str, FileStatus]]:
        """Get git status of all files."""
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-u"],
            cwd=self.repo_path, capture_output=True, text=True
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Git status failed: {result.stderr}")
        
        files = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            # Parse porcelain v1 format
            # Format: XY PATH (where X=index, Y=worktree)
            # For untracked: ?? PATH
            # For single char status: X PATH (e.g., M PATH, D PATH)
            # Note: output may have leading space
            line = line.lstrip()
            parts = line.split(' ', 1)
            if len(parts) != 2:
                continue
            status_code, path = parts[0], parts[1]
            
            # Handle untracked (??)
            if status_code == '??':
                status = FileStatus.UNTRACKED
            else:
                # status_code is 1 or 2 chars
                # Check worktree status (second char) first, then index (first char)
                ws = status_code[1] if len(status_code) > 1 else ' '
                is_ = status_code[0] if len(status_code) > 0 else ' '
                
                if ws == 'M' or is_ == 'M':
                    status = FileStatus.MODIFIED
                elif ws == 'A' or is_ == 'A':
                    status = FileStatus.ADDED
                elif ws == 'D' or is_ == 'D':
                    status = FileStatus.DELETED
                elif ws == 'R' or is_ == 'R':
                    status = FileStatus.MODIFIED
                else:
                    status = FileStatus.MODIFIED
            
            files.append((path, status))
        
        return files
    
    def get_file_info(self, rel_path: str, status: FileStatus) -> FileInfo:
        """Get detailed file information."""
        full_path = self.repo_path / rel_path
        
        if status == FileStatus.DELETED:
            return FileInfo(
                path=rel_path,
                status=status,
                size=0,
                sha256=None
            )
        
        if not full_path.exists():
            return FileInfo(
                path=rel_path,
                status=status,
                size=0,
                sha256=None
            )
        
        stat = full_path.stat()
        content = full_path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        
        return FileInfo(
            path=rel_path,
            status=status,
            size=stat.st_size,
            sha256=sha256,
            mode=oct(stat.st_mode)[-3:],
            mtime=stat.st_mtime
        )
    
    def read_file(self, rel_path: str) -> bytes:
        """Read file content."""
        full_path = self.repo_path / rel_path
        return full_path.read_bytes()
    
    def write_file(self, rel_path: str, content: bytes):
        """Write file content."""
        full_path = self.repo_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
    
    def delete_file(self, rel_path: str):
        """Delete a file."""
        full_path = self.repo_path / rel_path
        if full_path.exists():
            full_path.unlink()
    
    def apply_manifest(self, manifest: SyncManifest, files: Dict[str, str]):
        """Apply downloaded manifest and files to working directory."""
        for file_info in manifest.files:
            if file_info.status == FileStatus.DELETED:
                self.delete_file(file_info.path)
            elif file_info.path in files:
                content = base64.b64decode(files[file_info.path])
                # Verify hash
                if file_info.sha256:
                    computed = hashlib.sha256(content).hexdigest()
                    if computed != file_info.sha256:
                        print(f"Warning: Hash mismatch for {file_info.path}")
                        continue
                self.write_file(file_info.path, content)
    
    def get_base_commit(self) -> Optional[str]:
        """Get the base commit for incremental sync (last synced commit)."""
        sync_file = self.repo_path / ".filesync" / "last_sync.json"
        if sync_file.exists():
            try:
                data = json.loads(sync_file.read_text())
                return data.get("head_commit")
            except Exception:
                pass
        return None
    
    def save_sync_state(self, head_commit: str):
        """Save the sync state."""
        sync_dir = self.repo_path / ".filesync"
        sync_dir.mkdir(parents=True, exist_ok=True)
        sync_file = sync_dir / "last_sync.json"
        sync_file.write_text(json.dumps({
            "head_commit": head_commit,
            "timestamp": time.time()
        }))


class FileSyncClient:
    """Main client for synchronizing with server."""
    
    def __init__(self, config: SyncConfig):
        self.config = config
        self.repo = GitRepo(config.repo_path)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Set repo_id and client_id
        self.repo_id = config.repo_id or self.repo.get_repo_id()
        self.client_id = config.client_id or f"client-{hashlib.sha256(str(config.repo_path).encode()).hexdigest()[:8]}"
        
        print(f"Repo ID: {self.repo_id}")
        print(f"Client ID: {self.client_id}")
        print(f"Server: {config.server_url}")
    
    def _make_url(self, endpoint: str) -> str:
        """Build full URL for endpoint."""
        return f"{self.config.server_url.rstrip('/')}{endpoint}"
    
    def check_server(self) -> bool:
        """Check if server is reachable."""
        try:
            resp = self.session.get(self._make_url("/health"), timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(f"Server unreachable: {e}")
            return False
    
    def upload(self) -> bool:
        """Upload changed files to server."""
        print("\n=== Scanning for changes ===")
        changes = self.repo.get_status()
        
        if not changes:
            print("No changes detected.")
            return True
        
        print(f"Found {len(changes)} changed file(s):")
        for path, status in changes:
            print(f"  {status.value}: {path}")
        
        if self.config.dry_run:
            print("\n[DRY RUN] Skipping actual upload")
            return True
        
        # Build file info list
        file_infos = []
        file_contents = {}
        
        for path, status in changes:
            file_info = self.repo.get_file_info(path, status)
            file_infos.append(file_info)
            
            if status != FileStatus.DELETED:
                content = self.repo.read_file(path)
                file_contents[path] = base64.b64encode(content).decode('utf-8')
        
        # Create manifest
        manifest = SyncManifest(
            repo_id=self.repo_id,
            client_id=self.client_id,
            base_commit=self.repo.get_base_commit(),
            head_commit=self.repo.get_head_commit(),
            files=file_infos,
            timestamp=time.time()
        )
        
        # Upload
        print("\n=== Uploading files ===")
        request = UploadRequest(manifest=manifest, files=file_contents)
        
        try:
            resp = self.session.post(
                self._make_url(f"/repos/{self.repo_id}/upload"),
                json=request.model_dump(),
                timeout=60
            )
            resp.raise_for_status()
            response = UploadResponse(**resp.json())
        except Exception as e:
            print(f"Upload failed: {e}")
            return False
        
        if response.success:
            print(f"✓ Upload successful: {len(response.uploaded_files)} files")
            # Save sync state
            if manifest.head_commit:
                self.repo.save_sync_state(manifest.head_commit)
            return True
        else:
            print(f"✗ Upload partial: {response.message}")
            for f, err in response.failed_files.items():
                print(f"  Failed: {f} - {err}")
            return False
    
    def download(self) -> bool:
        """Download files from server."""
        print("\n=== Downloading from server ===")
        
        # Get base commit for incremental sync
        since_commit = self.repo.get_base_commit()
        
        request = DownloadRequest(
            repo_id=self.repo_id,
            client_id=self.client_id,
            since_commit=since_commit
        )
        
        try:
            resp = self.session.post(
                self._make_url(f"/repos/{self.repo_id}/download"),
                json=request.model_dump(),
                timeout=60
            )
            resp.raise_for_status()
            response = DownloadResponse(**resp.json())
        except Exception as e:
            print(f"Download failed: {e}")
            return False
        
        if not response.success or not response.manifest:
            print(f"Download failed: {response.message}")
            return False
        
        manifest = response.manifest
        files = response.files
        
        print(f"Received manifest with {len(manifest.files)} file(s)")
        print(f"Server HEAD: {manifest.head_commit}")
        
        if self.config.dry_run:
            print("\n[DRY RUN] Files that would be synced:")
            for fi in manifest.files:
                print(f"  {fi.status.value}: {fi.path}")
            return True
        
        # Apply changes
        print("\n=== Applying changes ===")
        self.repo.apply_manifest(manifest, files)
        
        # Save sync state
        if manifest.head_commit:
            self.repo.save_sync_state(manifest.head_commit)
        
        print("✓ Download and apply complete")
        return True
    
    def sync(self) -> bool:
        """Full sync: upload then download."""
        print("=== Starting sync ===")
        
        if not self.check_server():
            print("Cannot connect to server")
            return False
        
        # Upload local changes
        if not self.upload():
            return False
        
        # Download remote changes
        if not self.download():
            return False
        
        print("\n=== Sync complete ===")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="FileSync Client - Synchronize git repository with server"
    )
    parser.add_argument(
        "--repo", "-r", required=True,
        help="Path to git repository"
    )
    parser.add_argument(
        "--server", "-s", default="http://localhost:8000",
        help="Server URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--repo-id", help="Custom repository ID (auto-generated if not provided)"
    )
    parser.add_argument(
        "--client-id", help="Custom client ID (auto-generated if not provided)"
    )
    parser.add_argument(
        "--upload-only", "-u", action="store_true",
        help="Only upload local changes"
    )
    parser.add_argument(
        "--download-only", "-d", action="store_true",
        help="Only download remote changes"
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--auto-commit", action="store_true",
        help="Auto-commit after download (not implemented yet)"
    )
    
    args = parser.parse_args()
    
    repo_path = Path(args.repo).resolve()
    if not repo_path.exists():
        print(f"Error: Repository path does not exist: {repo_path}")
        return 1
    
    config = SyncConfig(
        server_url=args.server,
        repo_path=repo_path,
        repo_id=args.repo_id,
        client_id=args.client_id,
        auto_commit=args.auto_commit,
        dry_run=args.dry_run
    )
    
    client = FileSyncClient(config)
    
    try:
        if args.upload_only:
            success = client.upload()
        elif args.download_only:
            success = client.download()
        else:
            success = client.sync()
        
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())