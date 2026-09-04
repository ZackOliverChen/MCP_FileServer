import socket
import time
import threading
import pytest
from mcp_fs.mcp_fs_server import run_server

def get_free_port():
    """Dynamically find an open port on the host machine to avoid conflicts."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def test_server_accepts_connections():
    host = '127.0.0.1'
    port = get_free_port()

    # 1. Start the server inside a background thread so it doesn't freeze pytest
    server_thread = threading.Thread(
        target=run_server, 
        args=(host, port), 
        daemon=True  # Daemon ensures the thread dies when the test finishes
    )
    server_thread.start()
    
    # Give the background server a split second to boot up and bind to the port
    time.sleep(0.5)

    # 2. Act as a client and try to connect to your live running server
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(1.0) # Don't hang if connection fails
        
        # Connect to the exact socket the background thread is running
        connection_result = client_socket.connect_ex((host, port))
        
        # 3. Assert that the connection was successful (0 means success in socket terms)
        assert connection_result == 0, f"Failed to connect to server on {host}:{port}"
        
    finally:
        client_socket.close()
