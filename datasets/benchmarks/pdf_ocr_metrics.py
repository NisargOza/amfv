"""Score source-checked PDF/OCR evidence in human-readable terms."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any


def normalize_text(text: str) -> str:
    """Normalize layout-only differences without hiding words or numbers."""
    text = text.translate(
        str.maketrans(
            {
                "–": "-",
                "—": "-",
                "−": "-",
                "‑": "-",
                "\u00ad": "",
            }
        )
    )
    text = re.sub(r"-\s+", "-", text)
    return " ".join(text.casefold().split())


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,%-]\d+)*%?|[a-z]+(?:-[a-z]+)*", normalize_text(text))


def _best_word_similarity(reference: str, candidate: str) -> float:
    """Return the best local word-sequence similarity for a checked passage."""
    expected = _word_tokens(reference)
    observed = _word_tokens(candidate)
    if not expected or not observed:
        return 0.0
    if len(observed) <= len(expected):
        return SequenceMatcher(None, expected, observed, autojunk=False).ratio()

    best = 0.0
    lower = max(1, len(expected) - 2)
    upper = min(len(observed), len(expected) + 2)
    for size in range(lower, upper + 1):
        for start in range(len(observed) - size + 1):
            score = SequenceMatcher(None, expected, observed[start : start + size], autojunk=False).ratio()
            if score > best:
                best = score
                if best == 1.0:
                    return best
    return best


class _HTMLTableParser(HTMLParser):
    """Collect visible cell text from simple generated HTML tables."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cells: list[str] = []
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() == "tr":
            self._cells = []
        elif tag.casefold() in {"td", "th"}:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"td", "th"} and self._cell_parts is not None:
            self._cells.append(" ".join(self._cell_parts))
            self._cell_parts = None
        elif tag.casefold() == "tr" and self._cells:
            self.rows.append(self._cells)
            self._cells = []


def _structured_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 4):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)

    parser = _HTMLTableParser()
    parser.feed(text)
    rows.extend(parser.rows)
    return rows


def score_output(text: str, document: dict[str, Any]) -> dict[str, Any]:
    """Score checked facts, passage similarity and table associations."""
    normalized = normalize_text(text)
    anchor_checks: list[dict[str, Any]] = []
    weighted_similarity = 0.0
    weighted_words = 0
    for anchor in document["anchors"]:
        anchor_normalized = normalize_text(anchor["text"])
        words = max(1, len(_word_tokens(anchor["text"])))
        similarity = _best_word_similarity(anchor["text"], text)
        weighted_similarity += similarity * words
        weighted_words += words
        anchor_checks.append(
            {
                "name": anchor["name"],
                "text": anchor["text"],
                "present": anchor_normalized in normalized,
                "word_similarity_percent": round(similarity * 100, 1),
            }
        )

    exact = sum(check["present"] for check in anchor_checks)
    metrics: dict[str, Any] = {
        "anchors": anchor_checks,
        "critical_facts_found": exact,
        "critical_facts_total": len(anchor_checks),
        "critical_facts_percent": round(exact / len(anchor_checks) * 100, 1),
        "critical_passage_word_similarity_percent": round(weighted_similarity / weighted_words * 100, 1),
        "critical_passage_reference_words": weighted_words,
    }

    expected_rows = document.get("table_rows", [])
    if not expected_rows:
        return metrics

    candidate_rows = [[normalize_text(cell) for cell in row] for row in _structured_rows(text)]
    row_checks: list[dict[str, Any]] = []
    for row in expected_rows:
        similarity = _best_word_similarity(row["text"], text)
        text_found = similarity >= 0.9
        linked = any(
            _best_word_similarity(row["text"], " ".join(candidate)) >= 0.9
            and normalize_text(row["date"]) in candidate
            and normalize_text(row["status"]) in candidate
            for candidate in candidate_rows
        )
        row_checks.append(
            {
                "name": row["name"],
                "date": row["date"],
                "status": row["status"],
                "recommendation_word_similarity_percent": round(similarity * 100, 1),
                "recommendation_found": text_found,
                "date_recommendation_status_linked": linked,
            }
        )

    recommendations_found = sum(check["recommendation_found"] for check in row_checks)
    linked_rows = sum(check["date_recommendation_status_linked"] for check in row_checks)
    metrics.update(
        table_rows=row_checks,
        table_recommendations_found=recommendations_found,
        table_rows_linked=linked_rows,
        table_rows_total=len(row_checks),
        table_recommendations_percent=round(recommendations_found / len(row_checks) * 100, 1),
        table_rows_linked_percent=round(linked_rows / len(row_checks) * 100, 1),
    )
    return metrics


__all__ = ["normalize_text", "score_output"]
