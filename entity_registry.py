"""Registry of known entity categories in Mewgenics save files.

Each category defines where to find entities in the SQLite database
and which parser to use for name extraction/modification.
"""
from dataclasses import dataclass


@dataclass
class EntityCategory:
    id: str                     # unique key, e.g. "team_cats"
    display_name: str           # shown in UI tree header
    table: str                  # SQLite table name
    parser_id: str              # key into blob_parser.PARSERS
    key_filter: str | None = None   # for "files" table: exact key match
    read_only: bool = False     # if True, shown but cannot rename
    description: str = ""       # tooltip / info text
    sort_order: int = 0         # display order in UI


# All known entity categories, ordered by sort_order.
# Add new categories here as formats are discovered.
ENTITY_CATEGORIES = [
    EntityCategory(
        id="team_cats",
        display_name="Team Cats",
        table="cats",
        parser_id="cat_blob",
        description="Your current team of cats",
        sort_order=10,
    ),
    EntityCategory(
        id="profile_cat",
        display_name="Profile Cat",
        table="files",
        key_filter="save_file_cat",
        parser_id="cat_blob",
        description="The cat shown on your save file",
        sort_order=20,
    ),
    EntityCategory(
        id="winning_teams",
        display_name="Winning Teams",
        table="winning_teams",
        parser_id="cat_blob",
        description="Cats from winning team compositions",
        sort_order=30,
    ),
]

# Quick lookup by id
CATEGORY_MAP = {cat.id: cat for cat in ENTITY_CATEGORIES}
