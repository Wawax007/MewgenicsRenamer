"""
Manage game-data entity names: parse localization CSVs from the gpak,
maintain a JSON override file, and build modified CSVs for injection.
"""
import csv
import json
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from gpak_manager import extract_files

# ---------- configuration ----------

LANGUAGES = ["en", "sp", "fr", "de", "it", "pt-br"]
LANGUAGE_LABELS = {
    "en": "English", "sp": "Español", "fr": "Français",
    "de": "Deutsch", "it": "Italiano", "pt-br": "Português (BR)",
}

# Which CSVs to parse and how to categorise keys inside them.
ENTITY_SOURCES = [
    {
        "csv_path": "data/text/units.csv",
        "categories": [
            ("enemies",      "Enemies",      "ENEMY_",          "_NAME"),
            ("familiars",    "Familiars",     "FAMILIAR_",       "_NAME"),
            ("player_units", "Player Units",  "PLAYER_",         "_NAME"),
        ],
    },
    {
        "csv_path": "data/text/items.csv",
        "categories": [
            ("items", "Items", "ITEM_", "_NAME"),
        ],
    },
    {
        "csv_path": "data/text/furniture.csv",
        "categories": [
            ("furniture", "Furniture", "FURNITURE_NAME_", ""),
        ],
    },
]

CATNAME_POOLS = [
    {"gpak_path": "data/catnames_female_en.txt",  "label": "Female"},
    {"gpak_path": "data/catnames_male_en.txt",    "label": "Male"},
    {"gpak_path": "data/catnames_neutral_en.txt", "label": "Neutral"},
]

OVERRIDES_FILENAME = "name_overrides.json"

# ---------- dataclass ----------

@dataclass
class GameEntity:
    key: str                # CSV key  e.g. ENEMY_FLY_NAME
    category: str           # e.g. "enemies"
    csv_path: str           # which CSV it lives in
    names: dict = field(default_factory=dict)   # {lang: str}

# ---------- loading ----------

def _parse_csv(raw_bytes):
    """Return (header, rows) from raw CSV bytes (handles BOM)."""
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    rows = list(reader)
    return header, rows


def _lang_indices(header):
    """Map language code → column index."""
    mapping = {}
    for i, col in enumerate(header):
        key = col.strip().lower()
        if key in LANGUAGES:
            mapping[key] = i
    return mapping


def load_entity_names(gpak_path):
    """
    Parse localization CSVs from the gpak and return
    {category_id: [GameEntity, …]}  sorted alphabetically by English name.
    """
    csv_paths = [src["csv_path"] for src in ENTITY_SOURCES]
    csv_data = extract_files(str(gpak_path), csv_paths)

    result = {}

    for source in ENTITY_SOURCES:
        raw = csv_data.get(source["csv_path"])
        if raw is None:
            continue
        header, rows = _parse_csv(raw)
        if not header:
            continue
        li = _lang_indices(header)

        for row in rows:
            if not row or not row[0] or row[0].strip().startswith("//"):
                continue
            csv_key = row[0].strip()

            for cat_id, _display, prefix, suffix in source["categories"]:
                if not csv_key.startswith(prefix):
                    continue
                if suffix and not csv_key.endswith(suffix):
                    continue

                # Collect non-template, non-empty names
                names = {}
                for lang, idx in li.items():
                    if idx < len(row):
                        val = row[idx].strip()
                        if val and "{" not in val:
                            names[lang] = val
                if not names:
                    break  # row matched prefix+suffix but has no real names

                result.setdefault(cat_id, []).append(
                    GameEntity(key=csv_key, category=cat_id,
                               csv_path=source["csv_path"], names=names)
                )
                break   # matched one category, done with this row

    for cat_id in result:
        result[cat_id].sort(key=lambda e: e.names.get("en", e.key).lower())
    return result


def get_category_display_order():
    """Return [(cat_id, display_name), …] in source order."""
    out = []
    for src in ENTITY_SOURCES:
        for cat_id, display, _pfx, _sfx in src["categories"]:
            out.append((cat_id, display))
    return out

# ---------- override persistence ----------

def _overrides_path(gpak_path):
    return Path(gpak_path).parent / OVERRIDES_FILENAME


def save_overrides(overrides, gpak_path):
    """Persist overrides dict  {key: {lang: newname}} to JSON."""
    path = _overrides_path(gpak_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "overrides": overrides}, f,
                  indent=2, ensure_ascii=False)


def load_overrides(gpak_path):
    """Load overrides. Returns {} if file absent or corrupt."""
    path = _overrides_path(gpak_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("overrides", {})
    except (json.JSONDecodeError, KeyError, OSError):
        return {}

# ---------- CSV rewriting ----------

def _apply_overrides_to_csv(raw_bytes, overrides):
    """
    Read a CSV, replace language columns for overridden keys, re-serialise.
    overrides: {csv_key: {lang: newname}}
    """
    header, rows = _parse_csv(raw_bytes)
    if not header:
        return raw_bytes
    li = _lang_indices(header)

    out_rows = [header]
    for row in rows:
        if row and row[0].strip() in overrides:
            key = row[0].strip()
            for lang, new_val in overrides[key].items():
                idx = li.get(lang)
                if idx is not None:
                    # extend row if too short
                    while len(row) <= idx:
                        row.append("")
                    row[idx] = new_val
        out_rows.append(row)

    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out_rows)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _key_belongs_to_csv(key, csv_path):
    """Check if a CSV key belongs to a given CSV file based on ENTITY_SOURCES."""
    for src in ENTITY_SOURCES:
        if src["csv_path"] != csv_path:
            continue
        for _cid, _disp, prefix, suffix in src["categories"]:
            if key.startswith(prefix) and (not suffix or key.endswith(suffix)):
                return True
    return False


def build_all_csvs(source_gpak_path, overrides):
    """
    Build {csv_path: modified_bytes} for EVERY localization CSV,
    reading originals from *source_gpak_path* (the backup) and
    applying current overrides on top.  CSVs with zero overrides
    are included too (to revert previously applied changes).
    """
    csv_paths = [src["csv_path"] for src in ENTITY_SOURCES]
    originals = extract_files(str(source_gpak_path), csv_paths)

    # Group overrides by csv_path
    grouped = {p: {} for p in csv_paths}
    for key, langs in overrides.items():
        for p in csv_paths:
            if _key_belongs_to_csv(key, p):
                grouped[p][key] = langs
                break

    result = {}
    for csv_path in csv_paths:
        raw = originals.get(csv_path)
        if raw is None:
            continue
        csv_ovr = grouped.get(csv_path, {})
        if csv_ovr:
            result[csv_path] = _apply_overrides_to_csv(raw, csv_ovr)
        else:
            result[csv_path] = raw  # restore original
    return result

# ---------- loose file management ----------

def write_loose_files(game_dir, csv_data):
    """
    Write modified CSV files as loose files next to the game exe.

    Args:
        game_dir: Path to the game directory (where Mewgenics.exe lives)
        csv_data: {csv_path: bytes} from build_all_csvs()

    Returns:
        list of Path objects that were written
    """
    written = []
    for csv_path, data in csv_data.items():
        target = Path(game_dir) / csv_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written


def remove_loose_files(game_dir):
    """
    Remove all loose override files (CSVs + cat name pools) and clean up empty dirs.

    Returns:
        list of Path objects that were deleted
    """
    removed = []
    all_paths = [src["csv_path"] for src in ENTITY_SOURCES] + \
                [p["gpak_path"] for p in CATNAME_POOLS]
    for rel_path in all_paths:
        target = Path(game_dir) / rel_path
        if target.exists():
            target.unlink()
            removed.append(target)

    # Clean up empty directories
    for subdir in ["data/text", "data"]:
        d = Path(game_dir) / subdir
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    return removed


def has_loose_files(game_dir):
    """Check if any loose override files (CSVs or cat name pools) exist."""
    if game_dir is None:
        return False
    all_paths = [src["csv_path"] for src in ENTITY_SOURCES] + \
                [p["gpak_path"] for p in CATNAME_POOLS]
    return any((Path(game_dir) / p).exists() for p in all_paths)

# ---------- cat name pools ----------

def load_catname_pools(gpak_path):
    """
    Load cat name pools from the gpak.
    Returns {pool_label: [name, ...]} sorted alphabetically.
    """
    paths = [p["gpak_path"] for p in CATNAME_POOLS]
    raw_data = extract_files(str(gpak_path), paths)

    result = {}
    for pool in CATNAME_POOLS:
        raw = raw_data.get(pool["gpak_path"])
        if raw is None:
            continue
        text = raw.decode("utf-8-sig")
        names = [n.strip() for n in text.splitlines() if n.strip() and not n.strip().startswith("//")]
        names.sort(key=str.lower)
        result[pool["label"]] = names
    return result


def save_catname_overrides(catname_overrides, gpak_path):
    """Save catname pool overrides to the overrides JSON file."""
    path = _overrides_path(gpak_path)
    # Load existing data to preserve entity overrides
    data = {"version": 1, "overrides": {}}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    data["catname_pools"] = catname_overrides
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_catname_overrides(gpak_path):
    """Load catname pool overrides. Returns {} if absent."""
    path = _overrides_path(gpak_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("catname_pools", {})
    except (json.JSONDecodeError, KeyError, OSError):
        return {}


def build_catname_files(source_gpak_path, catname_overrides):
    """
    Build {gpak_path: bytes} for cat name pool files.
    Overrides format: {pool_label: {"added": [name,...], "removed": [name,...]}}
    """
    paths = [p["gpak_path"] for p in CATNAME_POOLS]
    originals = extract_files(str(source_gpak_path), paths)

    result = {}
    for pool in CATNAME_POOLS:
        raw = originals.get(pool["gpak_path"])
        if raw is None:
            continue
        ovr = catname_overrides.get(pool["label"])
        if not ovr:
            result[pool["gpak_path"]] = raw
            continue

        text = raw.decode("utf-8-sig")
        names = [n.strip() for n in text.splitlines() if n.strip() and not n.strip().startswith("//")]

        # Apply removals
        removed = set(ovr.get("removed", []))
        names = [n for n in names if n not in removed]

        # Apply additions
        added = ovr.get("added", [])
        names.extend(added)

        # Sort and deduplicate
        names = sorted(set(names), key=str.lower)

        result[pool["gpak_path"]] = "\r\n".join(names).encode("utf-8")
    return result
