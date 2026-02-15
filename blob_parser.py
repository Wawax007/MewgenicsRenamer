import struct
import logging
from dataclasses import dataclass, field

import lz4.block


# Cat blob format (stored as LZ4-compressed or raw in SQLite):
#   Compressed: [u32 uncompressed_size][lz4 block]
#   Raw:        directly the decompressed layout below
#
# Decompressed layout:
#   [0:4]   u32 LE  magic = 0x13
#   [4:12]  8 bytes seed
#   [12:16] u32 LE  name_len (character count, NOT byte count)
#   [16:20] u32 LE  padding = 0
#   [20:…]  UTF-16LE name (name_len * 2 bytes)
#   [… ]    binary data (stats, abilities, etc.)

_CAT_MAGIC = 0x13
_NAME_LEN_OFFSET = 12
_NAME_START = 20
_MAX_NAME_CHARS = 500  # safety cap (game limit is 24)


@dataclass
class ParseResult:
    """Result of parsing a blob for its display name."""
    name: str
    raw_data: bytes
    is_compressed: bool
    name_offset: int          # byte offset of name start in raw_data (-1 if unknown)
    name_byte_len: int        # byte length of name in raw_data
    warnings: list[str] = field(default_factory=list)


def unpack_blob(blob):
    """Unpack a cat blob into raw data, auto-detecting format.

    Tries LZ4 decompression first, then raw format.
    Returns (raw_data: bytes, is_compressed: bool).
    Raises ValueError if neither format matches.
    """
    # Try LZ4 compressed format: [uint32 uncompressed_size][lz4 block]
    if len(blob) > 8:
        try:
            size = struct.unpack_from('<I', blob, 0)[0]
            # Cat blobs decompress to ~900-1000 bytes; cap at 10 KB for safety
            if 20 < size < 10_000:
                data = lz4.block.decompress(blob[4:], uncompressed_size=size)
                if (len(data) >= _NAME_START
                        and struct.unpack_from('<I', data, 0)[0] == _CAT_MAGIC):
                    return data, True
        except lz4.block.LZ4BlockError:
            pass  # Not a valid LZ4 block — fall through to raw format check
        except Exception as e:
            logging.debug(f"Unexpected error during LZ4 decompression: {e}")

    # Try raw (uncompressed) format
    if (len(blob) >= _NAME_START
            and struct.unpack_from('<I', blob, 0)[0] == _CAT_MAGIC):
        return bytes(blob), False

    raise ValueError("Not a valid cat blob (neither LZ4-compressed nor raw)")


def pack_blob(raw_data, is_compressed):
    """Pack raw cat data back into blob format."""
    if is_compressed:
        compressed = lz4.block.compress(bytes(raw_data), store_size=False)
        return struct.pack('<I', len(raw_data)) + compressed
    return bytes(raw_data)


def is_cat_blob(blob):
    """Check if a blob matches the cat data format."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < _NAME_START:
        return False
    try:
        unpack_blob(blob)
        return True
    except ValueError:
        return False


def parse_display_name(blob):
    """Extract the display name from a cat blob.

    Handles both LZ4-compressed and raw formats.
    Returns (name: str, name_start: int, name_byte_count: int).
    Raises ValueError on format errors.
    """
    data, _ = unpack_blob(blob)

    name_len = struct.unpack_from('<I', data, _NAME_LEN_OFFSET)[0]
    if name_len == 0:
        return ("", _NAME_START, 0)

    if name_len > _MAX_NAME_CHARS:
        raise ValueError(
            f"Name length {name_len} exceeds safety limit of {_MAX_NAME_CHARS} chars "
            f"(likely corrupted blob)"
        )

    name_byte_count = name_len * 2
    name_end = _NAME_START + name_byte_count

    if name_end > len(data):
        raise ValueError(
            f"Name extends beyond data: need {name_end} bytes, "
            f"data has {len(data)}"
        )

    name = data[_NAME_START:name_end].decode('utf-16-le')
    return (name, _NAME_START, name_byte_count)


def validate_blob(blob):
    """Validate basic structural properties of a cat blob.

    Returns a list of warning strings (empty = valid).
    """
    warnings = []
    try:
        data, _ = unpack_blob(blob)
    except ValueError as e:
        warnings.append(str(e))
        return warnings

    name_len = struct.unpack_from('<I', data, _NAME_LEN_OFFSET)[0]
    if name_len > 100:
        warnings.append(f"Suspicious name length: {name_len}")

    return warnings


# ---------------------------------------------------------------------------
# Parser registry: each parser_id maps to a dict of functions.
#   can_parse(blob) -> bool
#   parse(blob) -> ParseResult
# ---------------------------------------------------------------------------

def _cat_parse(blob):
    """Parse a cat blob (LZ4-compressed or raw) into a ParseResult."""
    data, is_compressed = unpack_blob(blob)
    name, name_start, name_byte_count = parse_display_name(blob)
    warnings = validate_blob(blob)
    return ParseResult(
        name=name,
        raw_data=data,
        is_compressed=is_compressed,
        name_offset=name_start,
        name_byte_len=name_byte_count,
        warnings=warnings,
    )


def _unknown_can_parse(blob):
    return isinstance(blob, (bytes, bytearray)) and len(blob) > 0


def _unknown_parse(blob):
    """Fallback parser for unknown blob formats."""
    return ParseResult(
        name="<unknown format>",
        raw_data=bytes(blob),
        is_compressed=False,
        name_offset=-1,
        name_byte_len=0,
        warnings=["Unknown blob format"],
    )


PARSERS = {
    "cat_blob": {
        "can_parse": is_cat_blob,
        "parse": _cat_parse,
    },
    "unknown": {
        "can_parse": _unknown_can_parse,
        "parse": _unknown_parse,
    },
}


def get_parser(parser_id):
    """Get a parser dict by its ID. Falls back to 'unknown'."""
    return PARSERS.get(parser_id, PARSERS["unknown"])
