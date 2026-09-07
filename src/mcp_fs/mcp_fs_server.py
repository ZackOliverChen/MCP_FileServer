import mimetypes
import base64
from pathlib import Path
import re
import shutil   # object-oriented filesystem paths
from mcp.server import MCPServer
import logging

from .checksumdb import ChecksumDB, calculate_bytes_checksum
from .config import MCP_FS_ROOT_DIR, MCP_FS_DB_FILE, MCP_FS_PRIVATE_DIR



mcp = MCPServer("mcp-fs")
db = ChecksumDB(MCP_FS_DB_FILE)


# resolve() takes a caller-supplied relative path string and returns a safe
# absolute Path that is GUARANTEED to live inside ROOT.
def resolve(rel: str) -> Path:
    # Strip leading slashes to force the path to be relative
    safe_rel = rel.lstrip("/")
    candidate = (MCP_FS_ROOT_DIR / safe_rel).resolve()

    # Guard: is_relative_to() checks whether `candidate` is inside ROOT.
    #    If the caller passed "../../etc/passwd", resolve() would normalize
    #    it to /home/user/etc/passwd and is_relative_to(ROOT) is False.
    #    Refuse before any disk access happens.
    if not candidate.is_relative_to(MCP_FS_ROOT_DIR):
        raise PermissionError(f"Path escapes allowed root: {rel}")

    if candidate == MCP_FS_PRIVATE_DIR or MCP_FS_PRIVATE_DIR in candidate.parents:
        raise PermissionError("Access denied: This system resource is restricted.")
    
    return candidate


# --- Tool #1: read_file ---
@mcp.tool()
def read_file(path: str) -> tuple[bool, str]:
    """
    Read a text file inside the confined folder.
    Args:
        path (str): The relative path from the server root path.
    Returns: tuple[bool, str] 
        bool: A boolean indicating success.
        str: The raw text content if the file is a text file (UTF-8). 
             If the file is binary (like an image, PDF, or archive), 
             it returns a Base64-encoded Data URI string in the format:
             'data:<mime-type>;base64,<data>'
             If an error occurs (the first element of the returned tuple is False), 
                it returns an error explanation string.
     """
    try:
        target_path = resolve(path)
        
        if not target_path.exists():
            return False, f"Error: File '{path}' does not exist."
            
        if not target_path.is_file():
            return False, f"Error: '{path}' is a directory, not a file."

        mime_type, _ = mimetypes.guess_type(target_path)
        is_text = mime_type and (mime_type.startswith("text/") or mime_type in ["application/json", "application/javascript"])

        if is_text or mime_type is None: 
            try:
                return True, target_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Fallback to binary if UTF-8 decoding fails unexpectedly
                pass

        binary_data = target_path.read_bytes()
        encoded_base64 = base64.b64encode(binary_data).decode("utf-8")
        return True, f"data:{mime_type};base64,{encoded_base64}"
        
    except Exception as e:
        logging.error(f"Failed to read file '{path}': {e}")
        return False, f"Error: Failed to read file. {str(e)}"
    

# --- Tool #2: write_file ---
@mcp.tool()
def write_file(path: str, content: str) -> tuple[bool, str]:
    """
    Write a file inside the confined folder, creating directories as needed.
    Accepts raw text or Base64-encoded Data URIs for binary files.
    Args:
        path (str): The relative path from the server root path.
        content (str): The text content or a 'data:<mime>;base64,...' Data URI.
    Returns: tuple[bool, str]
        bool: A boolean indicating success.
        str: The error message if the operation failed.
    """
    try:
        target = resolve(path)

        data_uri_match = re.match(r"^data:[^;]+;base64,(.+)$", content.strip())    
        if data_uri_match:
            # It's a binary file sent as base64
            base64_data = data_uri_match.group(1)
            file_bytes = base64.b64decode(base64_data)
        else:
            # It's standard text content
            file_bytes = content.encode("utf-8")    

        if target.exists():
            last_recorded_checksum = db.get_checksum(str(target)) 
            if last_recorded_checksum is None:
                return False, "Error: Cannot overwrite file. The file may have been created externally."    
            # The file was not created by this server.

            current_actual_content = target.read_bytes()
            current_checksum = calculate_bytes_checksum(current_actual_content)
            if current_checksum != last_recorded_checksum:
                return False, "Error: Cannot overwrite file. The file may have been modified externally."
            # The file was modified externally since the last known checksum.

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_bytes)

        # Log database checksum details
        new_checksum = calculate_bytes_checksum(file_bytes)
        db.save_checksum(str(target), new_checksum)

        return True, "File written successfully."
        
    except Exception as e:
        logging.error(f"Failed to write file '{path}': {e}")
        return False, f"Error: Failed to write file. {str(e)}"


@mcp.tool()
def write_file_chunk(path: str, chunk_base64: str, offset: int, is_last: bool = False) -> tuple[bool, str]:
    """
    Write a file in base64-encoded chunks to support large uploads.
    Args:
        path (str): The relative path from the server root path.
        chunk_base64 (str): Base64-encoded chunk content.
        offset (int): Byte offset where this chunk should be written.
        is_last (bool): True when this is the last chunk.
    Returns: tuple[bool, str]
        bool: A boolean indicating success.
        str: Result message or error details.
    """
    try:
        target = resolve(path)

        if offset < 0:
            return False, "Error: Invalid chunk offset."

        try:
            file_bytes = base64.b64decode(chunk_base64, validate=True)
        except Exception:
            return False, "Error: Invalid base64 chunk payload."

        if offset == 0:
            if target.exists():
                last_recorded_checksum = db.get_checksum(str(target))
                if last_recorded_checksum is None:
                    return False, "Error: Cannot overwrite file. The file may have been created externally."

                current_actual_content = target.read_bytes()
                current_checksum = calculate_bytes_checksum(current_actual_content)
                if current_checksum != last_recorded_checksum:
                    return False, "Error: Cannot overwrite file. The file may have been modified externally."

            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output:
                output.write(file_bytes)
        else:
            if not target.exists():
                return False, "Error: Chunk write state is missing. Start upload from offset 0."
            with target.open("r+b") as output:
                output.seek(offset)
                output.write(file_bytes)

        if is_last:
            final_bytes = target.read_bytes()
            db.save_checksum(str(target), calculate_bytes_checksum(final_bytes))
            return True, "File written successfully."

        return True, "Chunk written successfully."
    except Exception as e:
        logging.error(f"Failed to write chunk for '{path}': {e}")
        return False, f"Error: Failed to write file chunk. {str(e)}"


# --- Tool #4: move_file ---
@mcp.tool()
def move_file(source_path: str, destination_path: str) -> tuple[bool, str]:
    """
    Move or rename a file inside the confined folder.
    If the destination is an existing directory, the file is moved inside it.
    Args:
        source_path (str): The relative path of the file to move.
        destination_path (str): The relative destination path or directory.
    Returns: tuple[bool, str]
        bool: True if the file was moved successfully, False otherwise.
        str: The error message if the operation failed.
    """
    try:
        source = resolve(source_path)
        destination = resolve(destination_path)

        if not source.exists() or not source.is_file():
            return False, "Error: Source file does not exist or is not a file."

        if destination.exists():
            if destination.is_dir():
                destination = destination / source.name
                if not destination.is_relative_to(MCP_FS_ROOT_DIR):
                    return False, "Error: Destination is outside the confined folder."
                if destination == MCP_FS_PRIVATE_DIR or MCP_FS_PRIVATE_DIR in destination.parents:
                    return False, "Error: Cannot move file to private directory."

            if destination.exists():
                last_recorded_checksum = db.get_checksum(str(destination))
                if last_recorded_checksum is None:
                    return False, "Error: Can not move file. The destination file can not be overwritten."

                current_actual_content = destination.read_bytes()
                current_checksum = calculate_bytes_checksum(current_actual_content)
                if current_checksum != last_recorded_checksum:
                    return False, "Error: Can not move file. The destination file may have been created or modified externally."

        file_bytes = source.read_bytes()
        current_source_checksum = calculate_bytes_checksum(file_bytes)

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))

        db.delete_checksum(str(source))
        db.save_checksum(str(destination), current_source_checksum)
        return True, "File moved successfully."

    except Exception as e:
        logging.error(f"Failed to move file from '{source_path}' to '{destination_path}': {e}")
        return False, f"Error: Failed to move file. {str(e)}"

# --- Tool #5: list_dir ---
@mcp.tool()
def list_dir(path: str = ".", recursive: bool = False) -> tuple[bool, str, list[str]]:
    """
    List files and folders under a path inside the confined folder.
    Args:
        path (str): The relative path from the server root path. Defaults to ".".
        recursive (bool): True to list everything deeply; False to list only immediate contents.
    Returns: tuple[bool, str, list[str]]
        bool: True if the operation was successful, False otherwise.
        str: A message describing the result.
        list[str]: Alphabetically sorted relative paths from the server root.
                   If an error occurs or path is invalid, returns an empty list.
    """
    try:
        directory = resolve(path)
        if not directory.is_dir():
            return False, "Error: Invalid directory path.", []

        iterator = directory.rglob("*") if recursive else directory.glob("*")

        results = []
        for item in iterator:
            if item == MCP_FS_PRIVATE_DIR or MCP_FS_PRIVATE_DIR in item.parents:
                continue
            try:
                # Always ensure the output is clean and relative to the ROOT
                rel_path = str(item.relative_to(MCP_FS_ROOT_DIR))
                results.append(rel_path)
            except ValueError:
                continue

        results.sort()
        return True, "Directory listed successfully.", results

    except Exception as e:
        return False, f"Error: Failed to list directory. {str(e)}", []

# --- Tool #6: query_file ---
@mcp.tool()
def query_file(path: str) -> tuple[bool, dict]:
    """
    Query information about a file inside the confined folder.
    Args:
        path (str): The relative path from the server root path.
    Returns: tuple[bool, dict]
        bool: True if the operation was successful, False otherwise.
        dict: A dictionary containing file information or error details.
             {"message": "<description of the result>", "filesize": <int or None>, "allow_overwrite": <bool>}
    """
    try:
        file = resolve(path)
        if not file.exists():
            return False, {"message": "File does not exist.", "filesize": None, "allow_overwrite": False}
        if not file.is_file():
            return False, {"message": "Path is not a file.", "filesize": None, "allow_overwrite": False}
        if file == MCP_FS_PRIVATE_DIR or MCP_FS_PRIVATE_DIR in file.parents:
            return False, {"message": "Query to private directory is denied.", "filesize": None, "allow_overwrite": False}
        filesize = file.stat().st_size
        current_actual_content = file.read_bytes()
        current_checksum = calculate_bytes_checksum(current_actual_content)
        last_recorded_checksum = db.get_checksum(str(file)) 
        if current_checksum != last_recorded_checksum:
            file_allow_overwrite = False
        else:
            file_allow_overwrite = True
        return True, {"message": "Queried successfully.", "filesize": filesize, "allow_overwrite": file_allow_overwrite}
    except Exception as e:
        return False, {"message": "Failed to query file.", "filesize": None, "allow_overwrite": False}


import sys

def run_server(host: str, port: int) -> None:
    global MCP_FS_ROOT_DIR
    # Validate the host and port
    if not isinstance(host, str) or not host:
        raise ValueError(f"Invalid host: {host}")
    if not isinstance(port, int) or not (0 < port < 65536):
        raise ValueError(f"Invalid port: {port}")   
    print(f"mcp-fileserver serving {MCP_FS_ROOT_DIR} -> http://{host}:{port}/mcp")

    kwargs = {"host": host, "port": port}
    mcp.run(transport="streamable-http", **kwargs)

def main() -> None:
    # Get all command line arguments excluding the script name
    raw_args = sys.argv[1:]
    
    # Strip away '--host' or '--port' flags if passed by systemd
    clean_args = [arg for arg in raw_args if arg not in ("--host", "--port")]
    
    if len(clean_args) != 2:
        print("Usage: mcp-file-server <host> <port>")
        print("Alternatively: mcp-file-server --host <host> --port <port>")
        print("host=127.0.0.1 to bind to localhost, 0.0.0.0 to bind to all interfaces.")
        sys.exit(1)
        
    host = clean_args[0]
    try:
        port = int(clean_args[1])
    except ValueError:
        print(f"Error: Invalid port '{clean_args[1]}'. Port must be an integer.")
        sys.exit(1)
        
    run_server(host, port)

if __name__ == "__main__":
    main()
