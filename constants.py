import os
import logging

# Save file location
_appdata = os.environ.get("APPDATA", "")
if not _appdata:
    logging.warning("APPDATA environment variable is not set — save discovery may fail")
SAVE_BASE_DIR = os.path.join(_appdata, "Glaiel Games", "Mewgenics")

# Limits
MAX_NAME_LEN = 24
MIN_NAME_LEN = 1

# App info
APP_NAME = "Mewgenics Renamer"
APP_VERSION = "5.2.0"

GAME_PROCESS_NAME = "Mewgenics.exe"
BACKUP_EXTENSION = ".savbackup"
