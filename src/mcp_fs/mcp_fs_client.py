import argparse
import ast
import asyncio
import base64
import json
import mimetypes
import os
import posixpath
import shlex
import subprocess
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


RED = "\033[0;31m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[0;33m"
RESET = "\033[0m"
MAX_INLINE_PUT_BYTES = 2 * 1024 * 1024


def _normalize_rel_path(path: str) -> str:
    if path in ("", "."):
        return "."
    cleaned = path.strip().replace("\\", "/")
    if cleaned.startswith("/"):
        cleaned = cleaned.lstrip("/")
    norm = posixpath.normpath(cleaned)
    return "." if norm == "." else norm


def _join_rel_path(base: str, child: str) -> str:
    base_norm = _normalize_rel_path(base)
    child_norm = _normalize_rel_path(child)
    if child_norm == ".":
        return base_norm
    if base_norm == ".":
        return child_norm
    return posixpath.normpath(posixpath.join(base_norm, child_norm))


def _remote_path(base: str, path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    candidate = cleaned.lstrip("/") if cleaned.startswith("/") else posixpath.join(base, cleaned)
    normalized = _normalize_rel_path(candidate)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("Remote path escapes the server root")
    return normalized


def _local_path(base: str, path: str) -> str:
    return os.path.abspath(os.path.join(base, path))


def _format_tool_result(result: Any) -> str:
    if result is None:
        return ""
    if hasattr(result, "content"):
        content = getattr(result, "content")
        if isinstance(content, list):
            pieces = []
            for item in content:
                if hasattr(item, "text"):
                    pieces.append(item.text)
                elif isinstance(item, dict):
                    pieces.append(json.dumps(item, separators=(",", ":")))
                else:
                    pieces.append(str(item))
            return "\n".join(pieces)
        return str(content)
    return str(result)


def _tool_items(result: Any) -> list[str]:
    if hasattr(result, "content") and isinstance(result.content, list):
        return [item.text if hasattr(item, "text") else str(item) for item in result.content]
    return _format_tool_result(result).splitlines()


def _read_result(result: Any) -> tuple[bool, str]:
    payload = _decode_tool_payload(result)
    if isinstance(payload, tuple) and len(payload) == 2:
        return bool(payload[0]), str(payload[1])

    items = _tool_items(result)
    if len(items) >= 2 and items[0].strip().lower() in {"true", "1", "yes"}:
        return True, "\n".join(items[1:])
    return False, _format_tool_result(result)


def _local_file_content(path: str) -> str:
    with open(path, "rb") as input_file:
        data = input_file.read()
    mime_type, _ = mimetypes.guess_type(path)
    if mime_type and (mime_type.startswith("text/") or mime_type in {"application/json", "application/javascript"}):
        return data.decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type or 'application/octet-stream'};base64,{encoded}"


async def _put_file_chunked(url: str, local_path: str, remote_path: str, chunk_size: int = 512 * 1024) -> tuple[bool, str]:
    offset = 0
    with open(local_path, "rb") as input_file:
        while True:
            chunk = input_file.read(chunk_size)
            if not chunk:
                result = await _connect_and_call(
                    url,
                    "write_file_chunk",
                    path=remote_path,
                    chunk_base64="",
                    offset=offset,
                    is_last=True,
                )
                return _read_result(result)

            result = await _connect_and_call(
                url,
                "write_file_chunk",
                path=remote_path,
                chunk_base64=base64.b64encode(chunk).decode("ascii"),
                offset=offset,
                is_last=False,
            )
            success, message = _read_result(result)
            if not success:
                return False, message
            offset += len(chunk)


def _decode_tool_payload(result: Any) -> Any:
    text = _format_tool_result(result).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            lines = text.splitlines()
            if not lines:
                return text

            head = lines[0].strip().lower()
            if head in {"true", "false", "1", "0", "yes", "no"}:
                ok = head in {"true", "1", "yes"}
                tail = "\n".join(lines[1:]).strip()
                if not tail:
                    return ok, ""
                try:
                    return ok, json.loads(tail)
                except json.JSONDecodeError:
                    try:
                        return ok, ast.literal_eval(tail)
                    except (ValueError, SyntaxError):
                        return ok, tail

            return text


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return False


def _format_exception_message(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        messages: list[str] = []
        stack: list[BaseExceptionGroup] = [exc]
        while stack:
            current = stack.pop()
            for sub_exc in current.exceptions:
                if isinstance(sub_exc, BaseExceptionGroup):
                    stack.append(sub_exc)
                else:
                    messages.append(f"{type(sub_exc).__name__}: {sub_exc}")
        if messages:
            return "; ".join(messages)
    return f"{type(exc).__name__}: {exc}"


async def _connect_and_call(url: str, tool_name: str, **arguments: Any) -> Any:
    async with streamable_http_client(url) as streams:
        read, write = streams[:2]
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


def _style_name(name: str, is_dir: bool = False, allow_overwrite: bool | None = None) -> str:
    if is_dir:
        return f"{BLUE}{name}{RESET}"
    if allow_overwrite is None:
        return name
    if allow_overwrite:
        return f"{GREEN}{name}{RESET}"
    return f"{RED}{name}{RESET}"


async def _query_file_status(url: str, rel_path: str) -> tuple[bool, bool, dict]:
    try:
        result = await _connect_and_call(url, "query_file", path=rel_path)
        payload = _decode_tool_payload(result)
        if isinstance(payload, tuple) and len(payload) == 2:
            ok, info = payload
        elif isinstance(payload, dict):
            ok = True
            info = payload
        else:
            lines = _tool_items(result)
            ok = bool(lines) and lines[0].strip().lower() in {"true", "1", "yes"}
            info = {}
            if ok and len(lines) >= 3:
                try:
                    info = json.loads("\n".join(lines[2:]))
                except json.JSONDecodeError:
                    try:
                        info = ast.literal_eval("\n".join(lines[2:]))
                    except (ValueError, SyntaxError):
                        info = {}

        if ok and isinstance(info, dict):
            return True, _is_truthy(info.get("allow_overwrite", False)), info
        return False, False, {"message": "Unknown file status"}
    except Exception:
        return False, False, {"message": "Failed to query file"}


async def _list_directory(url: str, rel_path: str, recursive: bool = False) -> list[str]:
    result = await _connect_and_call(url, "list_dir", path=rel_path, recursive=recursive)
    payload = _decode_tool_payload(result)
    if isinstance(payload, tuple) and len(payload) == 3:
        success, message, entries = payload
        if success and isinstance(entries, list):
            return [str(item) for item in entries]
    elif isinstance(payload, dict):
        items = payload.get("items") or payload.get("entries") or payload.get("result")
        if isinstance(items, list):
            return [str(item) for item in items]

    lines = _format_tool_result(result).splitlines()
    if len(lines) >= 2 and lines[0].strip().lower() in {"true", "1", "yes"}:
        return [line.strip() for line in lines[2:] if line.strip()]
    return []


async def _build_remote_tree(url: str) -> tuple[set[str], set[str]]:
    paths = [_normalize_rel_path(p) for p in await _list_directory(url, ".", recursive=True)]
    directories = {"."}
    files = set()

    for path in paths:
        if path == ".":
            continue
        parts = path.split("/")
        for i in range(1, len(parts)):
            directories.add("/".join(parts[:i]))
        file_exists, _, _ = await _query_file_status(url, path)
        if file_exists:
            files.add(path)
        elif any(other.startswith(f"{path}/") for other in paths):
            directories.add(path)
        else:
            # A recursive listing exposes leaf paths as files when query_file
            # is unavailable on an older server.
            files.add(path)

    return directories, files


def _add_remote_file(
    remote_path: str, directories: set[str], files: set[str]
) -> None:
    normalized = _normalize_rel_path(remote_path)
    parts = normalized.split("/")
    for index in range(1, len(parts)):
        directories.add("/".join(parts[:index]))
    directories.discard(normalized)
    files.add(normalized)


def _move_remote_file(
    source_path: str,
    destination_path: str,
    directories: set[str],
    files: set[str],
) -> None:
    source = _normalize_rel_path(source_path)
    destination = _normalize_rel_path(destination_path)
    files.discard(source)
    _add_remote_file(destination, directories, files)


async def _handle_rls(url: str, remote_dir: str, directories: set[str], files: set[str]) -> None:
    prefix = "" if remote_dir == "." else f"{remote_dir}/"
    children: dict[str, bool] = {".": True, "..": True}

    for path in directories | files:
        if not path.startswith(prefix):
            continue
        remainder = path[len(prefix):]
        if remainder and "/" not in remainder:
            children[remainder] = path in directories

    printitems = ""
    for name in sorted(children):
        is_dir = children[name]
        allow_overwrite = None
        if not is_dir:
            try:
                path = _remote_path(remote_dir, name)
            except ValueError:
                path = None
            if path is not None:
                _, allow_overwrite, _ = await _query_file_status(url, path)
        printitems += _style_name(name, is_dir=is_dir, allow_overwrite=allow_overwrite) + "  "
    print(printitems)


def _handle_ls(local_dir: str) -> None:
    try:
        entries = sorted(os.scandir(local_dir), key=lambda entry: entry.name)
    except OSError as exc:
        print(f"{RED}ls: {exc}{RESET}")
        return
    printitems = ""
    for entry in entries:
        printitems += _style_name(entry.name, is_dir=entry.is_dir(), allow_overwrite=None) + "  "
    print(printitems)


def _print_usage() -> None:
    try:
        app_version = version("mcp_fs")
    except PackageNotFoundError:
        app_version = "unknown"
    print(f"MCP File Server Client {app_version}:")
    print("  cd <dir>                Change the current local directory")
    print("  ls                      List contents of the current local directory")
    print("  rcd <dir>               Change the current remote directory")
    print("  rls                     List contents of the current remote directory")
    print("  tools                   List available tools on the remote server")
    print("  get <rpath> <lpath>     Read a file from the remote server, and save it locally")
    print("  put <lpath> <rpath>     Read a file from the local system and write it to the remote server")
    print("  query <path>            Query a file on the remote server")
    print("  move <src> <dst>        Move a file on the remote server")
    print("  exec <shell command>    Execute a shell command locally")
    print("  help, ?                 Show this help message")
    print("  exit                    Exit the client")

async def _run_command(
    url: str,
    remote_dir: str,
    local_dir: str,
    directories: set[str],
    files: set[str],
    command: str,
    args: list[str],
) -> tuple[str, str]:
    if command in {"help", "?"}:
        _print_usage()
        return remote_dir, local_dir

    if command in {"cd", "chdir"}:
        if not args:
            print("Usage: cd <directory>")
            return remote_dir, local_dir
        target = _local_path(local_dir, args[0])
        if not os.path.isdir(target):
            print(f"{RED}cd: not a directory: {args[0]}{RESET}")
            return remote_dir, local_dir
        local_dir = target
        return remote_dir, local_dir

    if command in {"ls", "list"}:
        _handle_ls(local_dir)
        return remote_dir, local_dir

    if command in {"rcd"}:
        if not args:
            print("Usage: rcd <directory>")
            return remote_dir, local_dir
        try:
            target = _remote_path(remote_dir, args[0])
        except ValueError as exc:
            print(f"{RED}rcd: {exc}{RESET}")
            return remote_dir, local_dir
        if target not in directories:
            print(f"{RED}rcd: remote directory does not exist: {args[0]}{RESET}")
            return remote_dir, local_dir
        remote_dir = target
        return remote_dir, local_dir

    if command == "rls":
        await _handle_rls(url, remote_dir, directories, files)
        return remote_dir, local_dir

    if command in {"list-tools", "tools"}:
        # there is no such a tool to list all tools
        # mcp standard method should be called
        result = await _connect_and_call(url, "list_tools")
        print(_format_tool_result(result))
        return remote_dir, local_dir

    if command == "get":
        if len(args) != 2:
            print("Usage: get <remote-path> <local-path>")
            return remote_dir, local_dir
        try:
            remote_path = _remote_path(remote_dir, args[0])
            local_path = _local_path(local_dir, args[1])
        except ValueError as exc:
            print(f"{RED}get: {exc}{RESET}")
            return remote_dir, local_dir
        result = await _connect_and_call(url, "read_file", path=remote_path)
        success, content = _read_result(result)
        if not success:
            print(content)
            return remote_dir, local_dir
        if os.path.isdir(local_path):
            local_path = os.path.join(local_path, os.path.basename(remote_path))
        try:
            if content.startswith("data:") and ";base64," in content:
                encoded = content.split(",", 1)[1]
                with open(local_path, "wb") as output:
                    output.write(base64.b64decode(encoded))
            else:
                with open(local_path, "w", encoding="utf-8") as output:
                    output.write(content)
            print(f"File saved to {local_path}")
        except (OSError, ValueError) as exc:
            print(f"{RED}get: {exc}{RESET}")
        return remote_dir, local_dir

    if command == "put":
        if len(args) != 2:
            print("Usage: put <local-path> <remote-path>")
            return remote_dir, local_dir
        local_path = _local_path(local_dir, args[0])
        if not os.path.isfile(local_path):
            print(f"{RED}put: local file does not exist: {args[0]}{RESET}")
            return remote_dir, local_dir
        try:
            remote_path = _remote_path(remote_dir, args[1])
            local_size = os.path.getsize(local_path)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"{RED}put: {exc}{RESET}")
            return remote_dir, local_dir

        if local_size > MAX_INLINE_PUT_BYTES:
            try:
                success, message = await _put_file_chunked(url, local_path, remote_path)
            except Exception as exc:
                print(f"{RED}put: {_format_exception_message(exc)}{RESET}")
                return remote_dir, local_dir
        else:
            try:
                content = _local_file_content(local_path)
                result = await _connect_and_call(url, "write_file", path=remote_path, content=content)
                success, message = _read_result(result)
            except Exception as exc:
                print(f"{RED}put: {_format_exception_message(exc)}{RESET}")
                return remote_dir, local_dir

        if success:
            _add_remote_file(remote_path, directories, files)
        else:
            print(message)
        return remote_dir, local_dir

    if command == "query":
        if len(args) != 1:
            print("Usage: query <path>")
            return remote_dir, local_dir
        path = _remote_path(remote_dir, args[0])
        result = await _connect_and_call(url, "query_file", path=path)
        payload = _decode_tool_payload(result)
        if isinstance(payload, tuple) and len(payload) == 2:
            success, info = payload
            if success and isinstance(info, dict):
                allow = info.get("allow_overwrite", False)
                print(json.dumps(info, indent=2, sort_keys=True))
                print(f"overwrite: {'allowed' if allow else 'denied'}")
        else:
            print(_format_tool_result(result))
        return remote_dir, local_dir

    if command == "move":
        if len(args) != 2:
            print("Usage: move <source> <destination>")
            return remote_dir, local_dir
        src = _remote_path(remote_dir, args[0])
        dst = _remote_path(remote_dir, args[1])
        result = await _connect_and_call(url, "move_file", source_path=src, destination_path=dst)
        success, message = _read_result(result)
        if success:
            _move_remote_file(src, dst, directories, files)
        else:
            print(message)
        return remote_dir, local_dir

    if command == "exec":
        if not args:
            print("Usage: exec <shell command>")
            return remote_dir, local_dir
        try:
            completed = subprocess.run(" ".join(args), shell=True, cwd=local_dir, text=True)
            if completed.returncode:
                print(f"{RED}exec exited with status {completed.returncode}{RESET}")
        except OSError as exc:
            print(f"{RED}exec: {exc}{RESET}")
        return remote_dir, local_dir

    if command == "exit":
        raise SystemExit(0)

    print(f"Unknown command: {command}. Type 'help'.")
    return remote_dir, local_dir


async def _interactive(url: str) -> None:
    remote_dir = "."
    local_dir = os.getcwd()
    try:
        directories, files = await _build_remote_tree(url)
    except Exception as exc:
        print(f"{RED}Unable to build remote directory tree: {exc}{RESET}")
        directories, files = {"."}, set()
    print(f"Connected to {url}")
    print(f"Local directory: {local_dir}")
    print("Remote directory: .")
    print("Type 'help' for commands, or 'exit' to quit.")
    while True:
        try:
            raw = input(f"{local_dir} | {BLUE}{remote_dir}{RESET}> ").strip()
        except EOFError:
            print()
            break
        if not raw:
            continue
        if raw.lower() in {"exit", "quit"}:
            break
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            print(f"Parse error: {exc}")
            continue
        try:
            remote_dir, local_dir = await _run_command(
                url, remote_dir, local_dir, directories, files, parts[0], parts[1:]
            )
        except Exception as exc:
            print(f"{RED}{parts[0]}: {_format_exception_message(exc)}{RESET}")


async def _run_once(url: str, command: str | None, args: list[str]) -> None:
    if command is None:
        await _interactive(url)
        return
    try:
        directories, files = await _build_remote_tree(url)
        await _run_command(url, ".", os.getcwd(), directories, files, command, args)
    except Exception as exc:
        print(f"{RED}{command}: {_format_exception_message(exc)}{RESET}")


def client() -> None:
    parser = argparse.ArgumentParser(description="Client for the MCP File Server.")
    parser.add_argument("host", help="Server hostname or IP address")
    parser.add_argument("port", type=int, help="Server port")
    parser.add_argument("command", nargs="?", help="One-shot command to run instead of interactive mode")
    parser.add_argument("arguments", nargs="*", help="Arguments for the single command")
    args = parser.parse_args()

    if args.host is None or args.port is None:
        parser.error("Usage: --host and --port must be specified.")
        return

    url = f"http://{args.host}:{args.port}/mcp"
    try:
        asyncio.run(_run_once(url, args.command, args.arguments))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except SystemExit:
        pass


if __name__ == "__main__":
    client()

