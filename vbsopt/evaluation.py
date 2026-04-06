
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


def normalize_match_text(text: str) -> str:
    return (
        text.replace("hxxps://", "https://")
        .replace("hxxp://", "http://")
        .replace('""', '"')
        .lower()
    )


def hit_details(expected: Sequence[str], text: str) -> list[dict[str, object]]:
    normalized_text = normalize_match_text(text)
    rows: list[dict[str, object]] = []
    for item in expected:
        hit = normalize_match_text(item) in normalized_text
        rows.append({"artifact": item, "hit": hit})
    return rows


def count_hits(expected: Sequence[str], text: str) -> tuple[int, int]:
    rows = hit_details(expected, text)
    return sum(1 for row in rows if row["hit"]), len(rows)


def recall(expected: Sequence[str], text: str) -> float:
    hits, total = count_hits(expected, text)
    return hits / total if total else 1.0
