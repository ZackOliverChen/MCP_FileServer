import socket
import time
import threading
import shutil
from pathlib import Path
from dotenv import load_dotenv, dotenv_values


env_file = Path(__file__).resolve().parent / ".env"
envs = dotenv_values(env_file)
print(f"Loading Environment Variables from {env_file}")
for key, value in envs.items():
    print(f"Loaded: {key} = {value}")
load_dotenv(env_file, override=True)

from mcp_fs.mcp_fs_server import run_server, write_file, read_file, db
from mcp_fs.checksumdb_maintain import (
    checksum_db_deny_overwrite,
    checksum_db_allow_overwrite,
    checksum_db_remove_absent_files,
    db_maintain_cli
)
from mcp_fs.config import MCP_FS_ROOT_DIR, MCP_FS_DB_FILE, MCP_FS_PRIVATE_DIR


_shared_server_started = False

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def clean_test_environment():
    """Wipes out residual test files to ensure a clean state."""
    if MCP_FS_ROOT_DIR.exists():
        for item in MCP_FS_ROOT_DIR.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir() and item.name != ".private":
                shutil.rmtree(item)

def start_server_in_thread():
    global _shared_server_started
    if _shared_server_started:
        return None, '127.0.0.1', None
        
    clean_test_environment()
        
    host = '127.0.0.1'
    port = get_free_port()

    server_thread = threading.Thread(
        target=run_server, 
        args=(host, port), 
        daemon=True
    )
    server_thread.start()
    time.sleep(0.5)
    _shared_server_started = True
    return server_thread, host, port

def test_deny_overwrite_single_file():
    start_server_in_thread()
    test_file = MCP_FS_ROOT_DIR / "deny_single.txt"
    if test_file.exists(): test_file.unlink()
    
    write_success, _ = write_file("deny_single.txt", "Initial Content")
    assert write_success is True

    checksum_db_deny_overwrite("deny_single.txt")

    success, message = write_file("deny_single.txt", "Attempted Overwrite")
    assert success is False
    assert "Error:" in message

def test_deny_overwrite_glob():
    start_server_in_thread()
    log_file = MCP_FS_ROOT_DIR / "logs" / "app.log"
    if log_file.exists(): log_file.unlink()
    
    write_success, _ = write_file("logs/app.log", "Log entry")
    assert write_success is True
    
    checksum_db_deny_overwrite("logs/*.log")
    
    success, message = write_file("logs/app.log", "New entry")
    assert success is False

def test_allow_overwrite_single_file():
    start_server_in_thread()
    write_file("allow_single.txt", "Initial")
    
    checksum_db_allow_overwrite("allow_single.txt")
    
    success, message = write_file("allow_single.txt", "Updated Content")
    assert success is True

def test_allow_overwrite_directory():
    start_server_in_thread()
    write_file("docs/doc1.txt", "Text")
    
    checksum_db_allow_overwrite("docs/")
    
    success, message = write_file("docs/doc1.txt", "New Text")
    assert success is True

def test_remove_absent_files():
    start_server_in_thread()
    absent_path = MCP_FS_ROOT_DIR / "absent.txt"
    if absent_path.exists():
        absent_path.unlink()
        
    db.save_checksum(str(absent_path), "dummychecksum")
    checksum_db_remove_absent_files()
    
    success, content = read_file("absent.txt")
    assert success is False
    assert "Error:" in content

def test_db_maintain_cli_flow(monkeypatch, capsys):
    simulated_inputs = [
        "ls",
        "cd non_existent_folder",
        "exit"
    ]
    input_generator = iter(simulated_inputs)
    monkeypatch.setattr("builtins.input", lambda _: next(input_generator))
    
    db_maintain_cli()
    
    captured = capsys.readouterr()
    output = captured.out
    
    import fnmatch
    # Checks if your pattern maps anywhere inside the output trace lines
    assert any(fnmatch.fnmatch(line, "Checksum Database Maintenance CLI*") for line in output.splitlines())
    non_existent_folder = str(MCP_FS_ROOT_DIR / "non_existent_folder")
    assert f"Directory not found: {non_existent_folder}" in output

if __name__ == "__main__":
    db_maintain_cli()
