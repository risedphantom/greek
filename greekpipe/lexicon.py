"""Dictionary of course lemmas: collect from transcripts, fill forms and glosses via the LLM."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from greekpipe.config import STOPWORDS, normalize
from greekpipe.llm import LLMBadOutput, LLMTransient, text_part
from greekpipe.prompts import LEXICON_SYSTEM
from greekpipe.transcribe import LOW_CONFIDENCE, POS

SKIP_KINDS = frozenset({"chat", "other"})
MIN_LETTERS = 2
MAX_EXAMPLES = 3
REQUIRED = ("lemma", "translation", "pos", "example")
OPTIONAL = ("gender", "class")
TENSES = ("present", "imperfect", "aorist", "future")
PERSONS = 6
CASES = ("nom", "gen", "acc")
NUMBERS = ("sg", "pl")
GENDERS = ("m", "f", "n")

_GREEK_LETTER = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
_NON_GREEK_LETTER = re.compile(r"[^\W\d_\u0370-\u03ff\u1f00-\u1fff]")
_LETTER = re.compile(r"[^\W\d_]")


@dataclass
class LemmaRef:
    lemma: str
    pos: str
    surfaces: set[str]
    msg_ids: set[int]


@dataclass
class LexiconReport:
    done: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    cost: float = 0.0


def _keep(key: str) -> bool:
    return (
        key not in STOPWORDS
        and len(_LETTER.findall(key)) >= MIN_LETTERS
        and bool(_GREEK_LETTER.search(key))
        and not _NON_GREEK_LETTER.search(key)
    )


def collect_lemmas(transcripts: dict[int, dict]) -> dict[str, LemmaRef]:
    refs: dict[str, LemmaRef] = {}
    for msg_id, t in sorted(transcripts.items()):
        if t.get("kind") in SKIP_KINDS or t.get("confidence", 0.0) < LOW_CONFIDENCE:
            continue
        for w in t.get("greek_words", []):
            key = normalize(w["lemma"])
            if not _keep(key):
                continue
            ref = refs.setdefault(key, LemmaRef(w["lemma"], w["pos"], set(), set()))
            ref.surfaces.update(w.get("surface", []))
            ref.msg_ids.add(msg_id)
    return refs


def _filled(s) -> bool:
    return isinstance(s, str) and bool(s.strip())


def _pair(d, keys) -> bool:
    return isinstance(d, dict) and all(_filled(d.get(k)) for k in keys)


def _forms_ok(pos: str, forms) -> bool:
    if pos == "verb":
        return (
            isinstance(forms, dict)
            and all(
                isinstance(forms.get(t), list) and len(forms[t]) == PERSONS and all(map(_filled, forms[t]))
                for t in TENSES
            )
            and _pair(forms.get("imperative"), NUMBERS)
        )
    if pos == "noun":
        return isinstance(forms, dict) and all(_pair(forms.get(n), CASES) for n in NUMBERS)
    if pos == "adj":
        return isinstance(forms, dict) and all(_pair(forms.get(g), NUMBERS) for g in GENDERS)
    return forms in (None, {})


def validate(entry: dict) -> bool:
    translation = entry.get("translation")
    return (
        all(k in entry for k in REQUIRED)
        and isinstance(translation, list)
        and bool(translation)
        and all(map(_filled, translation))
        and entry["pos"] in POS
        and entry.get("gender") in (None, *GENDERS)
        and _pair(entry["example"], ("el", "ru"))
        and _forms_ok(entry["pos"], entry.get("forms"))
    )


def _request_items(refs: list[LemmaRef]) -> list[dict]:
    return [
        {"lemma": r.lemma, "pos": r.pos, "examples": sorted(r.surfaces)[:MAX_EXAMPLES]}
        for r in refs
    ]


def _entry(raw: dict, key: str, ref: LemmaRef, model: str) -> dict:
    ok = validate(raw)
    entry = {k: raw.get(k) for k in ("lemma", "translation", "pos", *OPTIONAL)}
    entry.update(
        lemma=ref.lemma,
        forms=raw.get("forms") or {},
        example=raw.get("example"),
        lemma_key=key,
        surfaces=sorted(ref.surfaces),
        msg_ids=sorted(ref.msg_ids),
        needs_review=not ok,
        model=model,
    )
    return entry


def _write(lexicon_dir: Path, entry: dict) -> None:
    body = json.dumps(entry, ensure_ascii=False, indent=2) + "\n"
    path = lexicon_dir / f"{entry['lemma_key']}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, "utf-8")
    os.replace(tmp, path)


def _by_key(reply: dict) -> dict[str, dict]:
    entries = reply.get("entries")
    if not isinstance(entries, list):
        return {}
    out = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("lemma"), str):
            out.setdefault(normalize(e["lemma"]), e)
    return out


def generate_pending(
    settings, client, refs: dict[str, LemmaRef], batch_size: int = 20, limit: int | None = None
) -> LexiconReport:
    report = LexiconReport()
    out_dir = settings.lexicon_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [k for k in sorted(refs) if not (out_dir / f"{k}.json").exists()]
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    if limit is not None:
        batches = batches[:limit]
    model = settings.or_text_model
    cost_before = client.last_cost
    for n, keys in enumerate(batches):
        items = _request_items([refs[k] for k in keys])
        user = text_part(json.dumps(items, ensure_ascii=False))
        try:
            reply = client.chat_json(model, LEXICON_SYSTEM, [user], f"lexicon-{n}")
        except (LLMTransient, LLMBadOutput):
            report.pending.extend(keys)
            continue
        returned = _by_key(reply)
        for key in keys:
            raw = returned.get(key)
            if raw is None:
                report.pending.append(key)
                continue
            entry = _entry(raw, key, refs[key], model)
            _write(out_dir, entry)
            report.done.append(key)
            if entry["needs_review"]:
                report.needs_review.append(key)
    report.cost = client.last_cost - cost_before
    return report


def load_lexicon(lexicon_dir: Path) -> dict[str, dict]:
    lexicon = {}
    for path in sorted(Path(lexicon_dir).glob("*.json")):
        entry = json.loads(path.read_text("utf-8"))
        lexicon[entry.get("lemma_key", path.stem)] = entry
    return lexicon
