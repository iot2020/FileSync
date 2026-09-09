#!/usr/bin/env python3
"""Test script for FileSync system."""
import os
import sys
import tempfile
import shutil
import subprocess
import time
import threading
import requests
from pathlib import Path

# Add project paths
sys.path.insert(0, str(Path(__file__).parent / "server"))
sys.path.insert(0, str(Path(__file__).parent / "client"))
sys.path.insert(0, str(Path(__file__).parent / "shared"))

from client.sync import FileSyncClient, SyncConfig, GitRepo
from server.main import app
import uvicorn


def run_server(port: int, data_dir: Path, ready_event: threading.Event):
    """Run the FastAPI server in a thread."""
    os.environ["FILESYNC_DATA_DIR"] = str(data_dir)
    
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    
    # Signal ready after startup
    original_run = server.run
    def run_with_ready(*args, **kwargs):
        ready_event.set()
        original_run(*args, **kwargs)
    server.run = run_with_ready
    
    server.run()


def init_git_repo(path: Path):
    """Initialize a git repository with some files."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    
    (path / "README.md").write_text("# Test Repo\n")
    (path / "main.py").write_text("print('hello')\n")
    (path / "config.json").write_text('{"version": "1.0"}\n')
    
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial", "-q"], cwd=path, check=True)


def test_basic_sync():
    """Test basic upload/download sync."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        data_dir = tmpdir / "server_data"
        repo_a = tmpdir / "repo_a"
        repo_b = tmpdir / "repo_b"
        port = 18080
        server_url = f"http://127.0.0.1:{port}"
        
        print("Setting up test environment...")
        
        # Start server
        ready = threading.Event()
        server_thread = threading.Thread(
            target=run_server, args=(port, data_dir, ready), daemon=True
        )
        server_thread.start()
        ready.wait(timeout=10)
        time.sleep(1)  # Extra wait for server to be fully ready
        
        # Verify server
        resp = requests.get(f"{server_url}/health", timeout=5)
        assert resp.status_code == 200, "Server health check failed"
        print("✓ Server started")
        
        # Create repo A
        init_git_repo(repo_a)
        print("✓ Repo A created")
        
        # Clone to repo B
        subprocess.run(["git", "clone", str(repo_a), str(repo_b)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test2@test.com"], cwd=repo_b, check=True)
        subprocess.run(["git", "config", "user.name", "Test2"], cwd=repo_b, check=True)
        print("✓ Repo B cloned")
        
        # Make changes in repo A (uncommitted)
        (repo_a / "main.py").write_text("print('hello from A')\n")
        (repo_a / "new_file.txt").write_text("new content\n")
        print("✓ Changes made in Repo A")
        
        # Use explicit repo ID so both clients sync to same repo
        test_repo_id = "test-repo-123"
        
        # Upload from A
        config_a = SyncConfig(server_url=server_url, repo_path=repo_a, repo_id=test_repo_id)
        client_a = FileSyncClient(config_a)
        assert client_a.upload(), "Upload from A failed"
        print("✓ Upload from A successful")
        
        # Download to B
        config_b = SyncConfig(server_url=server_url, repo_path=repo_b, repo_id=test_repo_id)
        client_b = FileSyncClient(config_b)
        assert client_b.download(), "Download to B failed"
        print("✓ Download to B successful")
        
        # Verify files synced
        assert (repo_b / "main.py").read_text() == "print('hello from A')\n", "main.py not synced"
        assert (repo_b / "new_file.txt").read_text() == "new content\n", "new_file.txt not synced"
        print("✓ Files verified in Repo B")
        
        # Make changes in B and sync back (uncommitted)
        (repo_b / "config.json").write_text('{"version": "2.0"}\n')
        (repo_b / "from_b.txt").write_text("created in B\n")
        print("✓ Changes made in Repo B")
        
        # Upload from B
        assert client_b.upload(), "Upload from B failed"
        print("✓ Upload from B successful")
        
        # Download to A
        assert client_a.download(), "Download to A failed"
        print("✓ Download to A successful")
        
        # Verify bidirectional sync
        assert (repo_a / "config.json").read_text() == '{"version": "2.0"}\n', "config.json not synced back"
        assert (repo_a / "from_b.txt").read_text() == "created in B\n", "from_b.txt not synced back"
        print("✓ Bidirectional sync verified")
        
        print("\n=== All tests passed! ===")
        return True


def test_git_repo_operations():
    """Test GitRepo class operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test_repo"
        init_git_repo(repo_path)
        
        repo = GitRepo(repo_path)
        
        # Test repo ID generation
        repo_id = repo.get_repo_id()
        assert len(repo_id) == 16, "Repo ID should be 16 chars"
        print(f"✓ Repo ID: {repo_id}")
        
        # Test HEAD commit
        head = repo.get_head_commit()
        assert head and len(head) == 40, "HEAD should be 40-char SHA"
        print(f"✓ HEAD commit: {head[:8]}...")
        
        # Test status (should be clean)
        status = repo.get_status()
        assert len(status) == 0, "Clean repo should have no changes"
        print("✓ Clean repo status")
        
        # Make changes
        (repo_path / "modified.txt").write_text("modified")
        (repo_path / "new_file.txt").write_text("new")
        (repo_path / "README.md").write_text("# Modified\n")
        
        # Delete a file
        (repo_path / "config.json").unlink()
        
        status = repo.get_status()
        paths = {p for p, _ in status}
        assert "modified.txt" in paths
        assert "new_file.txt" in paths
        assert "README.md" in paths
        assert "config.json" in paths
        # Check statuses
        status_dict = dict(status)
        assert status_dict["README.md"] == FileStatus.MODIFIED
        assert status_dict["config.json"] == FileStatus.DELETED
        assert status_dict["modified.txt"] == FileStatus.UNTRACKED
        assert status_dict["new_file.txt"] == FileStatus.UNTRACKED
        print(f"✓ Status detection: {len(status)} changes")
        
        # Test file info
        for path, file_status in status:
            info = repo.get_file_info(path, file_status)
            assert info.path == path
            assert info.status == file_status
            if file_status != FileStatus.DELETED:
                assert info.size > 0
                assert info.sha256 is not None
        print("✓ File info generation")
        
        print("\n=== GitRepo tests passed! ===")
        return True


if __name__ == "__main__":
    from shared.models import FileStatus
    
    print("Running FileSync tests...\n")
    
    try:
        test_git_repo_operations()
        print()
        test_basic_sync()
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)