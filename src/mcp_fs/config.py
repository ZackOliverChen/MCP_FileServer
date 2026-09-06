import os
from pathlib import Path

# ROOT: the root path of the confined filesystem. All user-supplied paths are resolved
# relative to this path. If a user tries to escape ROOT, we refuse the request.
env_MCP_FS_ROOT_DIR = os.getenv("MCP_FS_ROOT_DIR", os.path.expanduser("~"))
env_MCP_FS_DB_FILE = os.getenv("MCP_FS_DB_FILE", None)
env_MCP_FS_PRIVATE_DIR = os.getenv("MCP_FS_PRIVATE_DIR", None)
MCP_FS_ROOT_DIR = Path(env_MCP_FS_ROOT_DIR).resolve()
if not MCP_FS_ROOT_DIR or not MCP_FS_ROOT_DIR.is_dir():
    raise ValueError(f"Root path is invalid: {env_MCP_FS_ROOT_DIR}.")
MCP_FS_DB_FILE = Path(env_MCP_FS_DB_FILE).resolve() if env_MCP_FS_DB_FILE else None
if not MCP_FS_DB_FILE or MCP_FS_DB_FILE.is_dir():
    raise ValueError(f"Database path is invalid: {env_MCP_FS_DB_FILE}.")
MCP_FS_PRIVATE_DIR = Path(env_MCP_FS_PRIVATE_DIR).resolve() if env_MCP_FS_PRIVATE_DIR else None
if not MCP_FS_PRIVATE_DIR or not MCP_FS_PRIVATE_DIR.is_dir():
    raise ValueError(f"Private path is invalid: {env_MCP_FS_PRIVATE_DIR}.")

