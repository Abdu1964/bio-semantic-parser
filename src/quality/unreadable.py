"""Characters the reader could not decode (control codes, U+FFFD) — usually lost Greek letters or symbols."""
import os

MAX_UNREADABLE = int(os.getenv("MAX_UNREADABLE_CHARS", "5"))


def unreadable_count(text: str) -> int:
    return sum(1 for c in text if c == "�" or (ord(c) < 32 and c not in "\n\r\t\f"))


def unreadable_line(text: str) -> str:
    n = unreadable_count(text)
    if n <= MAX_UNREADABLE:
        return f"           ✓ Unreadable characters: {n}"
    return f"           ⚠ Unreadable characters: {n} — symbols such as β or ° may be missing from entity names"
