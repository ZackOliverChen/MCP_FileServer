import subprocess
from pathlib import Path
import fnmatch
import shutil
from dotenv import load_dotenv, dotenv_values
from importlib.metadata import version

env_file = Path(__file__).resolve().parent / ".env"
envs = dotenv_values(env_file)
print(f"Loading Environment Variables from {env_file}")
for key, value in envs.items():
    print(f"Loaded: {key} = {value}")
load_dotenv(env_file, override=True)

from .checksumdb import ChecksumDB, calculate_bytes_checksum
from .config import MCP_FS_ROOT_DIR, MCP_FS_DB_FILE, MCP_FS_PRIVATE_DIR


db = ChecksumDB(MCP_FS_DB_FILE)


def checksum_db_deny_overwrite(path: str):
    """
    Remove entries from the checksumdb to prevent overwrite from clients.
    Args:
        path (str): The file path for which to remove the checksum entry.
        It could be a single file, a directory or a glob expression.
        It's relative to the server root path.
    """
    root_resolved = Path(MCP_FS_ROOT_DIR).resolve()

    if any(char in path for char in ("*", "?", "[")):
        full_pattern = (root_resolved / path).as_posix()
        
        for file_path in db.get_all_file_paths():
            try:
                p_resolved = Path(file_path).resolve()
                
                if not p_resolved.is_relative_to(root_resolved):
                    continue
                    
                if fnmatch.fnmatch(p_resolved.as_posix(), full_pattern):
                    db.delete_checksum(file_path)
            except Exception:
                continue
        return

    normalized_path = (root_resolved / path).resolve()
    
    if not normalized_path.is_relative_to(root_resolved):
        print("Access Denied: Path escapes allowed root boundary.")
        return

    if normalized_path.is_dir():
        for file_path in db.get_all_file_paths():
            try:
                if Path(file_path).resolve().is_relative_to(normalized_path):
                    db.delete_checksum(file_path)
            except Exception:
                continue
    else:
        db.delete_checksum(str(normalized_path))
        print(str(normalized_path))


def checksum_db_allow_overwrite(path: str):
    """
    Add entries to the checksumdb to allow overwrite from clients.
    Args:
        path (str): The file path for which to add the checksum entry.
        It could be a single file, a directory or a glob expression.
        It's relative to the server root path.
    """
    root_resolved = Path(MCP_FS_ROOT_DIR).resolve()
    private_resolved = Path(MCP_FS_PRIVATE_DIR).resolve()

    if any(char in path for char in ("*", "?", "[")):
        full_pattern = (root_resolved / path).as_posix()
        for p in root_resolved.rglob("*"):
            try:
                p_resolved = p.resolve()
                if not p_resolved.is_relative_to(root_resolved):
                    continue
                if not fnmatch.fnmatch(p_resolved.as_posix(), full_pattern):
                    continue
                if not p_resolved.is_file():
                    continue
                if p_resolved == private_resolved or private_resolved in p_resolved.parents:
                    continue
                db.save_checksum(str(p_resolved), calculate_bytes_checksum(p_resolved.read_bytes()))
            except Exception:
                continue
        return

    normalized_path = (root_resolved / path).resolve()
    
    if not normalized_path.is_relative_to(root_resolved):
        print("Access Denied: Path escapes allowed root boundary.")
        return
    
    if normalized_path.is_dir():
        for p in normalized_path.rglob("*"):
            try:
                p_resolved = p.resolve()
                if not p_resolved.is_file():
                    continue
                if p_resolved == private_resolved or private_resolved in p_resolved.parents:
                    continue
                db.save_checksum(str(p_resolved), calculate_bytes_checksum(p_resolved.read_bytes()))
            except Exception:
                continue
    else:
        if not normalized_path.exists():
            print(f"Error: Target file target '{path}' does not exist on disk.")
            return
            
        if normalized_path == private_resolved or private_resolved in normalized_path.parents:
            print("Error: Reserved configurations cannot be flagged for overwrite permissions.")
            return
            
        try:
            db.save_checksum(str(normalized_path), calculate_bytes_checksum(normalized_path.read_bytes()))
        except Exception as e:
            print(f"Failed to record file authorization track state records: {e}")


def checksum_db_remove_absent_files() -> None:
    """
    Remove entries from the checksumdb for files that no longer exist.
    It clears up the database for the whole root path. 
    """
    for file_path in db.get_all_file_paths():
        if not Path(file_path).exists():
            db.delete_checksum(file_path)

def checksum_db_list_entries() -> None:
    """
    List all entries in the checksumdb.
    It prints the file paths and their corresponding checksums.
    """
    all_items = db.get_all_file_paths()
    for file_path in all_items:
        checksum = db.get_checksum(file_path)
        print(f"{file_path}: {checksum}")
    print(f"Total {len(all_items)} items in database.")

BOLD_BLUE='\033[1;34m'
GREEN='\033[0;32m'  # for files overwrite allowed
RED='\033[0;31m'    # for files overwrite denied
YELLOW='\033[0;33m' # for files absent in the checksum database but present in the filesystem
NC='\033[0m'        # for directories

def deal_cmd_list_dir(current_path: Path) -> None:
    """
    List files and directories in the current directory.
    Args:
        current_path (Path): The absolute current directory path.
    It prints the files and directories in a sequential string with color coding:
        - Green: Files with overwrite allowed (present in the checksum database).
        - Red: Files with overwrite denied (not present in the checksum database).
        - Yellow: Files absent in the checksum database but present in the filesystem.
        - No color: Directories.
    """
    try:
        if not current_path.exists() or not current_path.is_dir():
            print(f"Directory not found: {current_path}")
            return

        # Sort so directories appear first, then files alphabetically
        entries = sorted(current_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

        display_items: list[str] = []
        for item in entries:
            if item == MCP_FS_PRIVATE_DIR or MCP_FS_PRIVATE_DIR in item.parents:
                display_items.append(f"{RED}{item.name}{NC}")
                continue
            if item.is_dir():
                display_items.append(f"{item.name}")
                continue

            try:
                checksum = db.get_checksum(str(item.resolve()))
            except Exception:
                checksum = None

            if checksum is not None:
                try:
                    current_checksum = calculate_bytes_checksum(item.read_bytes())
                    if current_checksum == checksum:
                        display_items.append(f"{GREEN}{item.name}{NC}")
                    else:
                        display_items.append(f"{RED}{item.name}{NC}")
                except Exception:
                    display_items.append(f"{RED}{item.name}{NC}")
                    print(f"Error reading file {item}: {e}")
            else:
                display_items.append(f"{YELLOW}{item.name}{NC}")

        if display_items:
            print(" ".join(display_items))

    except Exception as e:
        print(f"Error reading directory: {e}")

def deal_cmd_deny_write(current_path: Path, cmd_parms: str) -> None:
    """
    Deny overwrite for a specified path relative to the current directory.
    Args:
        current_path (Path): The current absolute directory path.
    """
    rel_target = cmd_parms 
    combined_path = current_path / rel_target
    relative_path = combined_path.relative_to(MCP_FS_ROOT_DIR)
    checksum_db_deny_overwrite(str(relative_path))

def deal_cmd_allow_write(current_path: Path, cmd_parms: str) -> None:
    """
    Allow overwrite for a specified path relative to the current directory.
    Args:
        current_path (Path): The current directory path relative to the confined filesystem root.
    """
    rel_target = cmd_parms #command[len("allow-write "):].strip()
    combined_path = current_path / rel_target
    relative_path = combined_path.relative_to(MCP_FS_ROOT_DIR)
    checksum_db_allow_overwrite(str(relative_path))


def deal_cmd_move_file(current_path: Path, cmd_parms: list[str]) -> None:
    """
    Move or rename files, directories, or glob selections within the confined filesystem,
    while cleanly translating database checksum tracks for all affected resources.
    
    If resources enter the private directory layout, their tracking traces are 
    permanently wiped from the tracking ledger to protect them.
    """
    parts = cmd_parms
    if len(parts) != 2:
        print("Invalid mv command. Usage: mv <source_or_glob> <destination>")
        return
        
    source_raw, dest_raw = parts
    root_resolved = Path(MCP_FS_ROOT_DIR).resolve()
    private_resolved = Path(MCP_FS_PRIVATE_DIR).resolve()
    
    dest_real = (current_path / dest_raw).resolve()
    if not dest_real.is_relative_to(root_resolved):
        print("Access denied: Destination escapes server root boundaries.")
        return

    matched_sources = []
    if any(char in source_raw for char in ("*", "?", "[")):
        matched_sources = list(current_path.glob(source_raw))
    else:
        single_target = (current_path / source_raw).resolve()
        if single_target.exists():
            matched_sources.append(single_target)

    if not matched_sources:
        print(f"Error: No files matched source target rule '{source_raw}'.")
        return

    for source_real in matched_sources:
        if not source_real.is_relative_to(root_resolved):
            print(f"Skipping '{source_real.name}': Escapes server root boundaries.")
            continue

        try:
            source_was_dir = source_real.is_dir()

            db_records_to_migrate = {}
            if source_was_dir:
                for registered_path in db.get_all_file_paths():
                    p = Path(registered_path).resolve()
                    if p.is_relative_to(source_real):
                        checksum = db.get_checksum(registered_path)
                        if checksum:
                            db_records_to_migrate[registered_path] = checksum
            else:
                checksum = db.get_checksum(str(source_real))
                if checksum:
                    db_records_to_migrate[str(source_real)] = checksum

            if dest_real.is_dir():
                final_dest_item = dest_real / source_real.name
            else:
                if len(matched_sources) > 1:
                    print(f"Error: Cannot move multiple matches into a single file path '{dest_raw}'")
                    return
                final_dest_item = dest_real

            shutil.move(str(source_real), str(final_dest_item))

            for old_path_str, checksum_val in db_records_to_migrate.items():
                db.delete_checksum(old_path_str)
                
                old_path_obj = Path(old_path_str).resolve()
                if source_was_dir:
                    relative_sub_path = old_path_obj.relative_to(source_real)
                    new_path_obj = (final_dest_item / relative_sub_path).resolve()
                else:
                    if final_dest_item.is_dir():
                        new_path_obj = (final_dest_item / source_real.name).resolve()
                    else:
                        new_path_obj = final_dest_item.resolve()
                    
                if new_path_obj == private_resolved or private_resolved in new_path_obj.parents:
                    print(f"Protected: resource moved to private area ({new_path_obj.name})")
                    continue
                    
                db.save_checksum(str(new_path_obj), checksum_val)

            print(f"Moved: {source_real.name}")

        except Exception as e:
            print(f"Error executing move '{source_real.name}': {e}")



def deal_cmd_change_directory(current_path: Path, cmd_parms: str) -> None:
    """
    Change the current directory to the specified path relative to the confined filesystem root.
    Args:
        current_path (Path): The current directory path relative to the confined filesystem root.
        cmd_parms (str): The command string containing the target directory.
    """
    rel_target = cmd_parms
    if rel_target == "\\":
        new_path = MCP_FS_ROOT_DIR
    else:
        new_path = (current_path / rel_target).resolve()
    if not new_path.is_relative_to(MCP_FS_ROOT_DIR):
        print("Access denied: Cannot escape server root boundaries.")
        return current_path
    if not new_path.exists() or not new_path.is_dir():
        print(f"Directory not found: {new_path}")
        return current_path
    # Update current_path in the calling context
    return new_path


def deal_cmd_exec_shell(current_path: Path, cmd_parms: str) -> None:
    """
    Extracts and executes a shell command contextually inside the current_path virtual folder.
    """
    shell_cmd = cmd_parms
    if not shell_cmd:
        print("Error: No command provided to execute.")
        return

    print(f"Executing: '{shell_cmd}' inside {current_path}")
    try:
        # Run command using the calculated path folder context (cwd)
        result = subprocess.run(
            shell_cmd, 
            shell=True, 
            cwd=current_path, 
            text=True, 
            capture_output=False # Stream output directly to user terminal
        )
    except Exception as e:
        print(f"Shell Execution Failed: {e}") 


def print_cli_usage():
    """
    Print the usage instructions for the checksum database maintenance CLI.
    """
    try:
        app_version = version(__package__)
    except Exception as e:
        app_version = ""
    print(f"Checksum Database Maintenance CLI {app_version}")
    print("Commands:")
    print("  protect <path>  - Deny overwrite for the specified path.")
    print("  open <path> - Allow overwrite for the specified path.")
    print("  clear-up           - Remove entries for files that no longer exist.")
    print("  ls                 - List files and directories in the current directory.")
    print("  cd <path>          - Change the current directory to the specified path.")
    print("  mv <source> <destination> - Move or rename a file or directory.")
    print("  db-list            - List all entries in the checksum database.")
    print("  exec <command>     - Execute a shell command in the current directory.")
    print("  help or usage      - Display this help message.")
    print("  exit               - Exit the CLI.")

def db_maintain_cli():
    """
    Iterative command-line interface for maintaining the checksum database.
    It initially take the root path as its current path and allows the user to navigate
    within the confined filesystem, and perform maintenance operations on the checksum database.
    Navigational commands won't change os level current working directory.
    Commands:
        - protect <path>: Deny overwrite for the specified path.
        - open <path>: Allow overwrite for the specified path.
        - clear-up: Remove entries for files that no longer exist.
        - ls: List files and directories in the current directory.
        - cd <path>: Change the current directory to the specified path.
        - mv <source> <destination>: Move or rename a file or directory.
        - db-list: List all entries in the checksum database .
        - exec: Execute a shell command in the current directory.
        - help or usage: Display this help message.
        - exit: Exit the CLI.
    """
    print_cli_usage()

    # current path relative to the confined filesystem root
    current_path = Path(MCP_FS_ROOT_DIR)

    while True:
        display_path = current_path.relative_to(MCP_FS_ROOT_DIR)
        if display_path == Path("."): display_path = "/"
        command = input(f"{BOLD_BLUE}MCP-FS | {display_path}: {NC}").strip()
        if not command:
            continue
        if command.startswith("protect "):
            cmd_parms = command[len("protect "):].strip()
            deal_cmd_deny_write(current_path, cmd_parms)
        elif command.startswith("open "):
            cmd_parms = command[len("open "):].strip()
            deal_cmd_allow_write(current_path, cmd_parms)
        elif command == "clear-up":
            checksum_db_remove_absent_files()
            print("Cleared up absent file entries.")
        elif command == "ls":
            deal_cmd_list_dir(current_path)
        elif command.startswith("mv "):
            cmd_parms = command[len("mv "):].strip().split(maxsplit=1)
            deal_cmd_move_file(current_path, cmd_parms)
        elif command == "db-list":
            checksum_db_list_entries()
        elif command.startswith("cd "):
            cmd_parms = command[len("cd "):].strip()
            current_path = deal_cmd_change_directory(current_path, cmd_parms)
        elif command == "help" or command == "usage":
            print_cli_usage()
        elif command.startswith("exec "):
            cmd_parms = command.removeprefix("exec ").strip()
            deal_cmd_exec_shell(current_path, cmd_parms)
        elif command == "exit":
            break
        else:
            print(f"Unknown command {command}. Type 'help' or 'usage' for available commands.")

if __name__ == "__main__":
    db_maintain_cli()