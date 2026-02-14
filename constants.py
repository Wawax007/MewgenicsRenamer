import os

# Save file location
SAVE_BASE_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Glaiel Games", "Mewgenics"
)

# Limits
MAX_NAME_LEN = 24
MIN_NAME_LEN = 1

# App info
APP_NAME = "Mewgenics Renamer"
APP_VERSION = "5.0.0"
GAME_PROCESS_NAME = "Mewgenics.exe"
BACKUP_EXTENSION = ".savbackup"
