import socket
import time
import os
import threading
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

env_file = Path(__file__).resolve().parent / ".env"
envs = dotenv_values(env_file)
print(f"Loading Environment Variables from {env_file}")
for key, value in envs.items():
    print(f"Loaded: {key} = {value}")
load_dotenv(env_file, override=True)

from mcp_fs.mcp_fs_server import move_file, run_server, write_file, read_file, list_dir
from mcp_fs.config import MCP_FS_ROOT_DIR


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def start_server_in_thread():
    host = '127.0.0.1'
    port = get_free_port()

    server_thread = threading.Thread(
        target=run_server, 
        args=(host, port), 
        daemon=True
    )
    server_thread.start()
    time.sleep(0.5)
    return server_thread, host, port

def test_server_accepts_connections():
    _, host, port = start_server_in_thread()
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(1.0)
        connection_result = client_socket.connect_ex((host, port))
        assert connection_result == 0, f"Failed to connect to server on {host}:{port}"
    finally:
        client_socket.close()

def test_list_dir_functionality():
    start_server_in_thread()
    write_file("a.txt", "File A")
    write_file("b.txt", "File B")
    
    # list_dir returns: tuple[bool, str, list[str]]
    success, message, files = list_dir(".")
    
    assert success is True
    assert "a.txt" in files
    assert "b.txt" in files
    assert ".private" not in files

def test_list_dir_error_handling():
    start_server_in_thread()
    # Fixed: Unpack all 3 elements returned by list_dir
    success, message, files = list_dir("../../")
    assert success is False
    assert files == []

def test_write_file_functionality():
    start_server_in_thread()
    
    # write_file returns: tuple[bool, str]
    success, message = write_file("sample.txt", "Hello Test World")
    assert success is True
    
    expected_path = MCP_FS_ROOT_DIR / "sample.txt"
    assert expected_path.exists()
    assert expected_path.read_text(encoding="utf-8") == "Hello Test World"
    
    nested_success, nested_message = write_file("subfolder/nested.txt", "Nested Content")
    assert nested_success is True
    
    expected_nested_path = MCP_FS_ROOT_DIR / "subfolder/nested.txt"
    assert expected_nested_path.exists()
    assert expected_nested_path.read_text(encoding="utf-8") == "Nested Content"

def test_read_file_functionality():
    start_server_in_thread()
    write_file("test_read.txt", "Read Me!")
    
    # read_file returns: tuple[bool, str]
    success, content = read_file("test_read.txt")
    
    assert success is True
    assert content == "Read Me!"

def test_read_file_error_handling():
    start_server_in_thread()
    # Fixed: Unpack the 2 elements returned by read_file
    success, content = read_file("../../etc/passwd")
    assert success is False
    assert "Error:" in content

def test_overwrite_protection():
    start_server_in_thread()
    write_file("protected.txt", "Initial Content")
    
    protected_path = MCP_FS_ROOT_DIR / "protected.txt"
    protected_path.write_text("Externally Modified Content", encoding="utf-8")
    
    # write_file returns: tuple[bool, str]
    success, message = write_file("protected.txt", "Attempted Overwrite")
    
    assert success is False
    assert "Error:" in message


def test_move_file_functionality():
    start_server_in_thread()
    write_file("move_source.txt", "Move Me")
    
    # move_file returns: tuple[bool, str]
    move_success, move_message = move_file("move_source.txt", "move_destination.txt")
    
    assert move_success is True
    assert not (MCP_FS_ROOT_DIR / "move_source.txt").exists()
    assert (MCP_FS_ROOT_DIR / "move_destination.txt").exists()
    assert (MCP_FS_ROOT_DIR / "move_destination.txt").read_text(encoding="utf-8") == "Move Me"
