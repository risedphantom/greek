"""Turn the teacher's photos and longer text posts into structured transcripts."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from greekpipe import prompts
from greekpipe.config import normalize
from greekpipe.llm import LLMBadOutput, LLMTransient, image_part, text_part
from greekpipe.tg_fetch import load_messages

KINDS = frozenset({"grammar", "exercise", "text", "vocab", "table", "chat", "other"})
POS = frozenset({"noun", "verb", "adj", "adv", "phrase", "other"})
LOW_CONFIDENCE = 0.7
UNGROUNDED_CONFIDENCE = 0.5
MIN_TEXT_CHARS = 40

_URL = re.compile(r"(https?://|www\.)\S+")
_SMALL_TALK = re.compile(r"^(Добр|Хорошего|Спасибо)")
_MORPHEME_HYPHEN = re.compile(r"(?<=[\u0370-\u03ff\u1f00-\u1fff])-(?=[\u0370-\u03ff\u1f00-\u1fff])")
_GREEK_WORD = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]+")


@dataclass
class TranscribeReport:
    done: list[int] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)
    low_confidence: list[int] = field(default_factory=list)
    cost: float = 0.0


def _strip_noise(text: str) -> str:
    text = _URL.sub("", text)
    kept = (
        c for c in text
        if unicodedata.category(c) not in ("So", "Sk", "Cf") and c != "\ufe0f"
    )
    return "".join(kept).strip()


def is_candidate(msg: dict) -> bool:
    if msg.get("media_kind") == "photo":
        return True
    if msg.get("media"):
        return False
    text = _strip_noise(msg.get("text") or "")
    return len(text) >= MIN_TEXT_CHARS and not _SMALL_TALK.match(text)


def _confidence(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(value, 0.0), 1.0) if math.isfinite(value) else 0.0


def _form(s) -> str:
    """Tidy a word form; textbook tables split endings off with a hyphen (πωλητ-ής)."""
    return _MORPHEME_HYPHEN.sub("", s.strip()) if isinstance(s, str) else ""


def _words(raw) -> list[dict]:
    words = []
    for w in raw if isinstance(raw, list) else []:
        lemma = _form(w.get("lemma")) if isinstance(w, dict) else ""
        if not lemma:
            continue
        raw_surface = w.get("surface") if isinstance(w.get("surface"), list) else []
        surface = list(dict.fromkeys(f for f in map(_form, raw_surface) if f))
        words.append({
            "lemma": lemma,
            "pos": w.get("pos") if w.get("pos") in POS else "other",
            "surface": surface or [lemma],
        })
    return words


def _normalise(raw: dict, msg_id: int, source: str, model: str) -> dict:
    def text(key: str) -> str:
        value = raw.get(key)
        return value if isinstance(value, str) else ""

    return {
        "msg_id": msg_id,
        "source": source,
        "kind": raw.get("kind") if raw.get("kind") in KINDS else "other",
        "title": text("title"),
        "body_md": text("body_md"),
        "greek_words": _words(raw.get("greek_words")),
        "topic_hint": text("topic_hint"),
        "confidence": _confidence(raw.get("confidence")),
        "model": model,
    }


def _greek_tokens(text: str) -> set[str]:
    """Accent-insensitive Greek word tokens: the teacher often types without accents."""
    folded = unicodedata.normalize("NFD", normalize(text))
    return set(_GREEK_WORD.findall("".join(c for c in folded if not unicodedata.combining(c))))


def _ground(transcript: dict, source_text: str) -> dict:
    """Text posts are their own ground truth: drop invented words, flag invented Greek in the body."""
    known = _greek_tokens(source_text)
    words = []
    for w in transcript["greek_words"]:
        surface = [f for f in w["surface"] if _greek_tokens(f) <= known]
        if surface:
            words.append({**w, "surface": surface})
    confidence = transcript["confidence"]
    if not _greek_tokens(transcript["body_md"]) <= known:
        confidence = min(confidence, UNGROUNDED_CONFIDENCE)
    return {**transcript, "greek_words": words, "confidence": confidence}


def _request(settings, msg: dict) -> tuple[str, str, str, list[dict]]:
    """Return (source, model, system prompt, user parts) for a candidate message."""
    if msg.get("media_kind") == "photo":
        caption = msg.get("text") or ""
        intro = f"Сообщение учителя (подпись к фото): {caption}" if caption else "Подписи нет."
        parts = [
            text_part(f"Дата сообщения: {msg['date'][:10]}\n{intro}"),
            image_part(settings.root / msg["media"]),
        ]
        return "photo", settings.or_vision_model, prompts.VISION_SYSTEM, parts
    return "text", settings.or_text_model, prompts.TEXT_SYSTEM, [text_part(msg["text"])]


def _write(transcripts_dir: Path, transcript: dict) -> None:
    body = json.dumps(transcript, ensure_ascii=False, indent=2) + "\n"
    (transcripts_dir / f"{transcript['msg_id']}.json").write_text(body, "utf-8")


def transcribe_pending(settings, client, limit: int | None = None) -> TranscribeReport:
    report = TranscribeReport()
    out_dir = settings.transcripts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [
        m for m in load_messages(settings.state_dir)
        if is_candidate(m) and not (out_dir / f"{m['id']}.json").exists()
    ]
    if limit is not None:
        todo = todo[:limit]
    cost_before = client.last_cost
    for msg in todo:
        source, model, system, parts = _request(settings, msg)
        try:
            raw = client.chat_json(model, system, parts, f"transcript-{msg['id']}")
        except LLMTransient:
            report.pending.append(msg["id"])
            continue
        except LLMBadOutput:
            raw = {"kind": "other", "confidence": 0.0}
        transcript = _normalise(raw, msg["id"], source, model)
        if source == "text":
            transcript = _ground(transcript, msg["text"])
        _write(out_dir, transcript)
        report.done.append(msg["id"])
        if transcript["confidence"] < LOW_CONFIDENCE:
            report.low_confidence.append(msg["id"])
    report.cost = client.last_cost - cost_before
    return report


def load_transcripts(transcripts_dir: Path) -> dict[int, dict]:
    transcripts = {}
    for path in sorted(Path(transcripts_dir).glob("*.json")):
        t = json.loads(path.read_text("utf-8"))
        transcripts[int(t["msg_id"])] = t
    return transcripts
