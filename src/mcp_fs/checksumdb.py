import sqlite3
from pathlib import Path
from threading import Lock
import hashlib

def calculate_bytes_checksum(data: bytes) -> str:
    """Calculate SHA-256 hash of raw byte data."""
    return hashlib.sha256(data).hexdigest()


class ChecksumDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.lock = Lock()
        self._init_db()

    def _init_db(self):
        """Create the database table if it doesn't exist yet."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS file_checksums (
                        file_path TEXT PRIMARY KEY,
                        checksum TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    def get_checksum(self, file_path: str) -> str | None:
        """Fetch the last known checksum for a file path."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT checksum FROM file_checksums WHERE file_path = ?", 
                    (file_path,)
                )
                row = cursor.fetchone()
                return row[0] if row else None  # Fixed: Extract index 0 string from tuple
            finally:
                conn.close()

    def save_checksum(self, file_path: str, checksum: str):
        """Save or overwrite a checksum for a file path."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("""
                    INSERT INTO file_checksums (file_path, checksum, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(file_path) DO UPDATE SET 
                        checksum = excluded.checksum,
                        updated_at = CURRENT_TIMESTAMP
                """, (file_path, checksum))
                conn.commit()
            finally:
                conn.close()

    def delete_checksum(self, file_path: str):
        """Delete a checksum entry for a file path."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("DELETE FROM file_checksums WHERE file_path = ?", (file_path,))
                conn.commit()
            finally:
                conn.close()

    def get_all_file_paths(self) -> list[str]:
        """Fetch all tracked file paths from the database."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT file_path FROM file_checksums")
                return [row[0] for row in cursor.fetchall()]  # Fixed: Extract index 0 from each tuple
            finally:
                conn.close()
