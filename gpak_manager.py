"""
Read and extract from Mewgenics resources.gpak archives (read-only).

GPAK format:
  [uint32 file_count]
  Per file: [uint16 path_len][path bytes (UTF-8)][uint32 data_size]
  Then: file data stored sequentially in file-table order.
"""
import struct
from pathlib import Path


def parse_file_table(gpak_path):
    """Parse gpak file table. Returns list of {path, size, offset}."""
    with open(gpak_path, 'rb') as f:
        # Read file table into memory in one shot (~1 MB for 18K entries)
        file_count = struct.unpack('<I', f.read(4))[0]
        buf = f.read(2 * 1024 * 1024)  # 2 MB covers any file table

    entries = []
    pos = 0
    for _ in range(file_count):
        path_len = struct.unpack_from('<H', buf, pos)[0]
        pos += 2
        path = buf[pos:pos + path_len].decode('utf-8')
        pos += path_len
        data_size = struct.unpack_from('<I', buf, pos)[0]
        pos += 4
        entries.append({"path": path, "size": data_size})

    data_start = 4 + pos  # uint32 file_count + table bytes
    offset = data_start
    for entry in entries:
        entry["offset"] = offset
        offset += entry["size"]
    return entries


def extract_files(gpak_path, file_paths):
    """Extract multiple files in one pass. Returns {path: bytes}."""
    entries = parse_file_table(gpak_path)
    entry_map = {e["path"]: e for e in entries}

    result = {}
    with open(gpak_path, 'rb') as f:
        for fp in file_paths:
            if fp in entry_map:
                entry = entry_map[fp]
                f.seek(entry["offset"])
                result[fp] = f.read(entry["size"])
    return result


def find_gpak():
    """Auto-detect resources.gpak location. Returns Path or None."""
    import sys
    # PyInstaller exe: __file__ points to temp dir, use sys.executable instead
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent
    candidates = [
        app_dir / "resources.gpak",
        app_dir.parent / "resources.gpak",
    ]
    for drive in ("C:/", "D:/", "E:/", "F:/"):
        for sub in (
            Path(drive) / "Games" / "Mewgenics",
            Path(drive) / "SteamLibrary" / "steamapps" / "common" / "Mewgenics",
            Path(drive) / "Program Files (x86)" / "Steam" / "steamapps" / "common" / "Mewgenics",
        ):
            candidates.append(sub / "resources.gpak")

    for p in candidates:
        if p.exists():
            return p
    return None
