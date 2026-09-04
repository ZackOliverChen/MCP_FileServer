# --- Standard library imports ---
import os
from pathlib import Path   # object-oriented filesystem paths

# --- Third-party import: FastMCP ---
# FastMCP is a high-level wrapper around the raw MCP SDK. It turns ordinary
# Python functions into MCP "tools" automatically and handles all the
# JSON-RPC wire protocol, the initialize handshake, tools/list, tools/call,
# and the HTTP server plumbing for us. Install:  pip install "mcp[cli]"
from mcp.server.fastmcp import FastMCP


# ROOT is the folder this file lives in. We use __file__ so it works no matter
# where the file is copied. .resolve() turns it into an absolute path
# (no ".." or symlink surprises).
ROOT = Path(__file__).resolve().parent


# Create the FastMCP server object.
#   - name: identifier shown to the client
#   - host/port: bind to loopback (127.0.0.1) only, port 8123. Loopback means
#     only THIS machine can reach it — that's fine because the openclaw
#     client runs on the same box.
mcp = FastMCP(
    "scoped-fs",
    host="127.0.0.1",
    port=8123,
)


# --- The single most important function: the scoping gate ---
# resolve() takes a caller-supplied relative path string and returns a safe
# absolute Path that is GUARANTEED to live inside ROOT.
# This is the only place in the whole file where we touch user input paths,
# so it's the only bottleneck we need to get right.
def resolve(rel: str) -> Path:
    # 1. Join the relative path onto ROOT and normalize (remove any ../ etc.)
    #    with .resolve().
    candidate = (ROOT / rel).resolve()

    # 2. Guard: is_relative_to() checks whether `candidate` is inside ROOT.
    #    If the caller passed "../../etc/passwd", resolve() would normalize
    #    it to /home/zack/etc/passwd and is_relative_to(ROOT) is False.
    #    We refuse before any disk access happens.
    if not candidate.is_relative_to(ROOT):
        raise PermissionError(f"Path escapes allowed root: {rel}")

    # 3. Safe — hand back the resolved path.
    return candidate


# --- Tool #1: read_file ---
# The @mcp.tool() decorator registers this function as an MCP tool. FastMCP
# reads the Python type hint `path: str` and builds the JSON inputSchema the
# client needs.
@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file inside the confined folder and return its contents."""
    # resolve() enforces the boundary; .read_text() reads the whole file
    # as a string. If the file doesn't exist it raises, which FastMCP turns
    # into an error response to the client.
    return resolve(path).read_text()


# --- Tool #2: write_file ---
@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write a file inside the confined folder, creating directories as needed."""
    # resolve() to get the safe location...
    target = resolve(path)

    # ...then make sure the parent directories exist (mkdir parents=True,
    # exist_ok=True means "don't error if they're already there"). This lets
    # a caller write src/sub/file.txt even if src/sub doesn't exist yet.
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write the text. Returns a short confirmation string.
    target.write_text(content)
    return f"wrote {path}"


# --- Tool #3: list_dir ---
@mcp.tool()
def list_dir(path: str = ".") -> list[str]:
    """List every file/folder under a path inside the confined folder."""
    # Default to ROOT itself if no path given (".").
    directory = resolve(path)

    # rglob("*") recursively walks everything under `directory`.
    # For each match, we compute its path relative to ROOT so the caller gets
    # clean relative names, then sort them alphabetically.
    return sorted(str(item.relative_to(ROOT)) for item in directory.rglob("*"))


def run_server():
    ROOT = os.getenv("MCP_FS_ROOT_PATH", os.path.expanduser("~"))

    print(f"scoped-fs serving {ROOT} -> http://127.0.0.1:8123/mcp")
    mcp.run(transport="streamable-http")