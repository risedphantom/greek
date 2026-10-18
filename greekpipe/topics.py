"""Grammar-tree taxonomy: map free-form topic hints and task texts onto topics.json."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from greekpipe.config import normalize

NEW_GROUP = "new"
TEXTS = ("texts", "readings")
LEXIS_GROUP = "lexis"
LEXIS_MISC = "lexis-misc"
MIN_SUBSTRING = 4

_TEXT_PREFIXES = ("текст", "диалог", normalize("κείμενο"))
_LEXIS_PREFIX = re.compile(r"^\s*лексика\s*:\s*", re.IGNORECASE)
_NUMBER_PAIR = re.compile(r"(\D+?)\s*(\d+)\s+и\s+(\d+)")
_NON_WORD = re.compile(r"[\W_]+")


def _norm(text: str) -> str:
    return " ".join(normalize(text).split())


def _contains(outer: str, inner: str) -> bool:
    """Whole-word containment: inner's tokens appear contiguously in outer's."""
    return f" {inner} " in f" {outer} "


def slug(text: str) -> str:
    return _NON_WORD.sub("-", normalize(text)).strip("-")


class Taxonomy:
    def __init__(self, data: dict):
        self.groups: list[dict] = data["groups"]
        self._topics: dict[str, dict] = {}
        self._hints: list[tuple[str, str, str]] = []  # (normalised hint, group id, topic id)
        for group in self.groups:
            for topic in group["topics"]:
                self._topics[topic["id"]] = topic
                for hint in topic["hints"]:
                    self._hints.append((_norm(hint), group["id"], topic["id"]))

    def topic(self, topic_id: str) -> dict | None:
        return self._topics.get(topic_id)

    def all_topic_ids(self) -> list[str]:
        return list(self._topics)

    def resolve(self, topic_hint: str) -> tuple[str, str]:
        hint = _norm(topic_hint)
        if found := self._match(hint, self._hints):
            return found
        if hint.startswith(_TEXT_PREFIXES):
            return TEXTS
        if lexis := _LEXIS_PREFIX.match(topic_hint):
            rest = _norm(topic_hint[lexis.end():])
            lexis_hints = [h for h in self._hints if h[1] == LEXIS_GROUP]
            return (
                self._match(rest, lexis_hints)
                or self._match(rest, lexis_hints, substring=True)
                or (LEXIS_GROUP, LEXIS_MISC)
            )
        return self._match(hint, self._hints, substring=True) or (NEW_GROUP, slug(topic_hint))

    def unmapped(self, hints: Iterable[str]) -> list[str]:
        return sorted({h for h in hints if self.resolve(h)[0] == NEW_GROUP})

    def topic_for_task_text(self, text: str) -> list[str]:
        normalised = _norm(text)
        # «спряжение глаголов 1 и 2» also reads as «… глаголов 1» and «… глаголов 2»
        expanded = [normalised] + [
            f"{m[1].strip()} {n}" for m in _NUMBER_PAIR.finditer(normalised) for n in (m[2], m[3])
        ]
        found = {tid for hint, _, tid in self._hints if any(_contains(t, hint) for t in expanded)}
        return [tid for tid in self._topics if tid in found]

    @staticmethod
    def _match(hint, candidates, substring=False) -> tuple[str, str] | None:
        if not substring:
            return next(((gid, tid) for h, gid, tid in candidates if h == hint), None)
        best, best_len = None, MIN_SUBSTRING - 1
        for h, gid, tid in candidates:
            overlap = min(len(h), len(hint))
            if overlap > best_len and (_contains(hint, h) or _contains(h, hint)):
                best, best_len = (gid, tid), overlap
        return best


def load_topics(path: Path) -> Taxonomy:
    return Taxonomy(json.loads(Path(path).read_text("utf-8")))
