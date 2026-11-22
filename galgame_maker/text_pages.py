"""Unicode-aware story text boundaries independent of the Qt interface."""

from __future__ import annotations

import unicodedata


def grapheme_ends(text: str) -> list[int]:
    """Return codepoint offsets after visible clusters used by dialogue paging.

    This covers combining marks, emoji variation selectors, skin tones, joined
    emoji sequences and regional-indicator pairs without a runtime dependency.
    """
    ends: list[int] = []
    regional_count = 0
    for index, character in enumerate(text):
        code = ord(character)
        regional = 0x1F1E6 <= code <= 0x1F1FF
        attaches = (
            unicodedata.combining(character) != 0
            or unicodedata.category(character) in {"Mc", "Me"}
            or code in range(0xFE00, 0xFE10)
            or code in range(0x1F3FB, 0x1F400)
            or character == "\u200d"
            or (index > 0 and text[index - 1] == "\u200d")
            or (regional and regional_count % 2 == 1)
        )
        if attaches and ends:
            ends[-1] = index + 1
        else:
            ends.append(index + 1)
        regional_count = regional_count + 1 if regional else 0
    return ends


def page_end(text: str, start: int, maximum: int = 80) -> int:
    """Return the next exclusive codepoint offset, preferring a late break."""
    if not 0 <= start <= len(text):
        raise ValueError("page start is outside the text")
    if maximum < 1:
        raise ValueError("page capacity must be positive")
    ends = grapheme_ends(text)
    boundaries = [end for end in ends if end > start]
    if not boundaries:
        return len(text)
    limit = min(maximum, len(boundaries))
    candidate = boundaries[limit - 1]
    if limit == len(boundaries):
        return candidate
    for boundary in reversed(boundaries[max(0, limit // 2):limit]):
        if text[boundary - 1] in "。！？.!?\n\t ":
            return boundary
    return candidate


def valid_page_start(text: str, offset: int) -> bool:
    return type(offset) is int and (offset == 0 or offset in grapheme_ends(text))
