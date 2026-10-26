# Copyright (c) 2023 Carter Lasalle
# Adapted from mac_messages_mcp/messages.py at 154a08595220438638cafee95fb553554c57f9ae.
# MIT license; see THIRD_PARTY_NOTICES.md.
"""Best-effort attributedBody decoder; not a full Apple typedstream parser."""


def extract_body_from_attributed(blob: bytes | None) -> str | None:
    if not isinstance(blob, bytes):
        return None
    marker = blob.find(b"NSString")
    if marker < 0:
        return None
    header = marker + len(b"NSString")
    if blob[header:header + 1] != b"\x01" or blob[header + 2:header + 5] != b"\x84\x01+":
        return None
    position = header + 5
    if position >= len(blob):
        return None
    length = blob[position]
    position += 1
    if length >= 0x80:
        width = {0x81: 2, 0x82: 3, 0x83: 4}.get(length)
        if width is None or position + width > len(blob):
            return None
        length = int.from_bytes(blob[position:position + width], "little")
        position += width
    if position + length > len(blob):
        return None
    try:
        return blob[position:position + length].decode("utf-8")
    except UnicodeDecodeError:
        return None
