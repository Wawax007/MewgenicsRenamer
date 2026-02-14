import sqlite3
import shutil
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime

from constants import SAVE_BASE_DIR, GAME_PROCESS_NAME, BACKUP_EXTENSION
from entity_registry import ENTITY_CATEGORIES
from blob_parser import get_parser


def discover_saves():
    """Scan the Mewgenics save directory for all save profiles and files.

    Returns a list of dicts: {"steam_id": str, "save_name": str, "path": Path, "modified": datetime}
    """
    base = Path(SAVE_BASE_DIR)
    results = []
    if not base.exists():
        return results

    for profile_dir in base.iterdir():
        if not profile_dir.is_dir():
            continue
        saves_dir = profile_dir / "saves"
        if not saves_dir.exists():
            continue
        for sav_file in saves_dir.glob("*.sav"):
            results.append({
                "steam_id": profile_dir.name,
                "save_name": sav_file.stem,
                "path": sav_file,
                "modified": datetime.fromtimestamp(sav_file.stat().st_mtime),
            })

    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def open_save(path):
    """Open a save file as a read-only SQLite connection and validate its schema."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Save file not found: {path}")

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    known_tables = {cat.table for cat in ENTITY_CATEGORIES}
    if not tables & known_tables:
        conn.close()
        raise ValueError(f"Invalid save file (no known tables): {path}")
    return conn


def open_save_rw(path):
    """Open a save file as a read-write SQLite connection."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Save file not found: {path}")
    return sqlite3.connect(str(path))


def get_all_entries(conn):
    """Scan all registered tables for renameable entries.

    Uses the entity registry to decide which tables/keys to scan
    and which parser to use for each blob.

    Returns a list of dicts:
        source:       str   — table name
        key:          int|str — primary key in that table
        blob:         bytes
        blob_size:    int
        category_id:  str   — registry category id
        category:     str   — display name for GUI grouping
        read_only:    bool
        name:         str   — parsed display name (or error placeholder)
        warnings:     list[str]
        parse_error:  str|None
    """
    # Discover available tables
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    entries = []

    for category in ENTITY_CATEGORIES:
        if category.table not in tables:
            continue

        # Build query
        if category.key_filter:
            rows = conn.execute(
                f"SELECT key, data FROM [{category.table}] WHERE key = ?",
                (category.key_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT key, data FROM [{category.table}]"
            ).fetchall()

        parser = get_parser(category.parser_id)

        for key, data in rows:
            if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
                continue

            entry = {
                "source": category.table,
                "key": key,
                "blob": data,
                "blob_size": len(data),
                "category_id": category.id,
                "category": category.display_name,
                "read_only": category.read_only,
                "name": "",
                "warnings": [],
                "parse_error": None,
            }

            try:
                if parser["can_parse"](data):
                    result = parser["parse"](data)
                    entry["name"] = result.name
                    entry["warnings"] = result.warnings
                else:
                    entry["name"] = "<unrecognized format>"
                    entry["parse_error"] = "Unrecognized blob format"
                    entry["read_only"] = True
            except Exception as e:
                entry["name"] = f"<error: {e}>"
                entry["parse_error"] = str(e)
                entry["read_only"] = True

            entries.append(entry)

    return entries


def write_blob(save_path, table, key, new_blob):
    """Write a modified blob back to any table in the save file."""
    conn = open_save_rw(save_path)
    try:
        conn.execute(
            f"UPDATE [{table}] SET data = ? WHERE key = ?",
            (new_blob, key)
        )
        conn.commit()
        row = conn.execute(
            f"SELECT data FROM [{table}] WHERE key = ?",
            (key,)
        ).fetchone()
        if row is None or row[0] != new_blob:
            raise IOError("Write verification failed — blob mismatch after write")
    finally:
        conn.close()


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(save_path):
    """Create a timestamped backup of the save file.

    Returns the backup Path. Raises on failure or integrity mismatch.
    """
    save_path = Path(save_path)
    backup_dir = save_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_name = f"{save_path.stem}_renamer_{timestamp}{BACKUP_EXTENSION}"
    backup_path = backup_dir / backup_name

    shutil.copy2(save_path, backup_path)

    if _file_sha256(save_path) != _file_sha256(backup_path):
        backup_path.unlink(missing_ok=True)
        raise IOError("Backup integrity check failed — hashes do not match")

    return backup_path


def restore_backup(backup_path, save_path):
    """Restore a backup file over the current save."""
    backup_path = Path(backup_path)
    save_path = Path(save_path)

    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    shutil.copy2(backup_path, save_path)

    if _file_sha256(backup_path) != _file_sha256(save_path):
        raise IOError("Restore integrity check failed — hashes do not match")


def is_game_running():
    """Check if the Mewgenics process is currently running."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    MAX_PATH = 260

    # Snapshot all processes
    TH32CS_SNAPPROCESS = 0x2
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * MAX_PATH),
        ]

    try:
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return False
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        target = GAME_PROCESS_NAME.lower().encode()
        if kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.lower() == target:
                    kernel32.CloseHandle(snap)
                    return True
                if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(snap)
    except Exception:
        pass
    return False


def list_backups(save_path):
    """List available backups for a given save file, newest first."""
    save_path = Path(save_path)
    backup_dir = save_path.parent / "backups"
    if not backup_dir.exists():
        return []

    backups = list(backup_dir.glob(f"{save_path.stem}_renamer_*{BACKUP_EXTENSION}"))
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return backups
