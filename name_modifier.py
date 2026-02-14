import struct

from constants import MAX_NAME_LEN, MIN_NAME_LEN
from blob_parser import unpack_blob, pack_blob

# Offsets in raw (decompressed) cat data
_NAME_LEN_OFFSET = 12   # uint32 LE
_NAME_START = 20         # full UTF-16LE (name_len * 2 bytes)


def validate_new_name(name):
    """Validate a proposed new name.

    Returns a list of warning/error strings (empty = valid).
    """
    issues = []
    if len(name) < MIN_NAME_LEN:
        issues.append(f"Name must be at least {MIN_NAME_LEN} character(s)")
    if len(name) > MAX_NAME_LEN:
        issues.append(
            f"Name must be at most {MAX_NAME_LEN} characters "
            f"(got {len(name)})"
        )
    if not name.isprintable():
        issues.append("Name contains non-printable characters")
    if not all(ord(c) < 128 for c in name):
        issues.append("Warning: non-ASCII characters may not render correctly in-game")
    return issues


def replace_display_name(blob, new_name):
    """Replace the display name in a cat blob.

    Handles both LZ4-compressed and raw (uncompressed) formats.
    Returns the modified blob as bytes.
    Raises ValueError if the name is invalid.
    """
    errors = [e for e in validate_new_name(new_name) if not e.startswith("Warning")]
    if errors:
        raise ValueError("; ".join(errors))

    data, is_compressed = unpack_blob(blob)

    old_name_len = struct.unpack_from('<I', data, _NAME_LEN_OFFSET)[0]
    old_name_end = _NAME_START + old_name_len * 2

    new_name_encoded = new_name.encode('utf-16-le')

    modified = bytearray()
    modified.extend(data[:_NAME_LEN_OFFSET])
    modified.extend(struct.pack('<I', len(new_name)))
    modified.extend(data[_NAME_LEN_OFFSET + 4:_NAME_START])
    modified.extend(new_name_encoded)
    modified.extend(data[old_name_end:])

    return pack_blob(modified, is_compressed)


def verify_modified_blob(original_blob, modified_blob, expected_name):
    """Verify that a modified blob contains the expected name and valid data.

    Returns (success: bool, message: str).
    """
    try:
        mod_data, _ = unpack_blob(modified_blob)
    except Exception as e:
        return (False, f"Failed to unpack modified blob: {e}")

    try:
        name_len = struct.unpack_from('<I', mod_data, _NAME_LEN_OFFSET)[0]
        actual_name = mod_data[_NAME_START:_NAME_START + name_len * 2].decode('utf-16-le')
    except Exception as e:
        return (False, f"Failed to parse name from modified blob: {e}")

    if actual_name != expected_name:
        return (False, f"Name mismatch: expected '{expected_name}', got '{actual_name}'")

    # Verify the data section (everything after the name) is preserved
    try:
        orig_data, _ = unpack_blob(original_blob)
    except Exception as e:
        return (False, f"Failed to unpack original blob: {e}")

    old_len = struct.unpack_from('<I', orig_data, _NAME_LEN_OFFSET)[0]
    new_len = struct.unpack_from('<I', mod_data, _NAME_LEN_OFFSET)[0]

    old_rest = orig_data[_NAME_START + old_len * 2:]
    new_rest = mod_data[_NAME_START + new_len * 2:]

    if old_rest != new_rest:
        return (False, "Data after name was corrupted during modification")

    return (True, "Verification passed")
