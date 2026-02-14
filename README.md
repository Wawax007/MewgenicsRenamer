# MewgenicsRenamer v5.0.0

A modding tool for [Mewgenics](https://store.steampowered.com/app/1236160/Mewgenics/) that lets you rename in-game entities and cats.

> **Download the latest release on [Nexus Mods](https://www.nexusmods.com/mewgenics/mods/2).**

---

## Features

| Tab | Description |
|-----|-------------|
| **Save Renamer** | Rename your existing cats directly in save files |
| **Game Data Modder** | Rename enemies, bosses, familiars, items, and furniture for all players |
| **Cat Name Pools** | Add or remove names from the random cat name generator |

## How it works

The tool writes small override files in a `data/` folder next to the game executable.
The engine loads these instead of the packed originals from `resources.gpak`.

- `resources.gpak` is **never modified**
- Your name choices are saved in `name_overrides.json` and persist across updates
- Restart the game after applying or removing overrides

## Running from source

Requires **Python 3.10+** on Windows.

```
pip install -r requirements.txt
python main.py
```

## Building

```
pip install pyinstaller
pyinstaller --windowed --name MewgenicsRenamer main.py
```

Output: `dist/MewgenicsRenamer/` — zip this folder for distribution.

## Project structure

```
main.py               Entry point
gui.py                Three-tab tkinter GUI
constants.py          Version, paths, limits
gpak_manager.py       GPAK archive reader (read-only)
game_data_manager.py  Entity/CSV management, loose file overrides, cat name pools
save_manager.py       Save file operations (SQLite + LZ4 blobs)
blob_parser.py        Cat blob binary parser
entity_registry.py    Entity category definitions
name_modifier.py      Name replacement in binary blobs
```

## License

All rights reserved. Source code provided for review and verification purposes only.
See [LICENSE](LICENSE).
