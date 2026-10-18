"""Assemble messages, transcripts, homework, taxonomy and lexicon into content/site.json."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from greekpipe.config import STOPWORDS, normalize
from greekpipe.lexicon import load_lexicon
from greekpipe.segment import sessions, tasks_for
from greekpipe.tg_fetch import load_messages
from greekpipe.topics import NEW_GROUP, Taxonomy, load_topics
from greekpipe.transcribe import load_transcripts

OCCURRENCE_KINDS = frozenset({"grammar", "table", "vocab", "text"})
LOW_CONFIDENCE = 0.7
ALBUM_SEPARATOR = "\n\n---\n\n"
AUDIO_CAPTION_WINDOW = timedelta(minutes=2)
NOT_FORMS = frozenset(normalize(w) for w in "ο η το οι τα τον την του της των τους τις τη θα".split())


def _day(msg: dict) -> str:
    return msg["date"][:10]


def _occurrences(messages, transcripts) -> list[dict]:
    """One occurrence per teaching transcript, album members merged into one."""
    by_id = {m["id"]: m for m in messages}
    albums: dict[str, list[int]] = {}
    for msg_id in sorted(transcripts):
        if transcripts[msg_id]["kind"] not in OCCURRENCE_KINDS or msg_id not in by_id:
            continue
        album = by_id[msg_id]["grouped_id"]
        albums.setdefault(str(album if album is not None else msg_id), []).append(msg_id)

    result = []
    for occ_id, ids in albums.items():
        members = [transcripts[i] for i in ids]
        result.append({
            "id": occ_id,
            "date": _day(by_id[ids[0]]),
            "kind": members[0]["kind"],
            "title": next((t["title"] for t in members if t["title"]), ""),
            "body_md": ALBUM_SEPARATOR.join(t["body_md"] for t in members),
            "media": [by_id[i]["media"] for i in ids if by_id[i]["media"]],
            "msg_ids": ids,
            "confidence": min(t["confidence"] for t in members),
            "topic_hint": members[0]["topic_hint"],
        })
    return result


def _homework(messages, transcripts, taxonomy: Taxonomy) -> list[dict]:
    by_id = {m["id"]: m for m in messages}
    order = taxonomy.all_topic_ids()
    result: list[dict] = []
    seen: dict[str, int] = {}
    for session in sessions(messages, transcripts):
        if session.kind != "homework":
            continue
        hw_id = f"hw-{session.date.isoformat()}"
        seen[hw_id] = seen.get(hw_id, 0) + 1
        if seen[hw_id] > 1:
            hw_id = f"{hw_id}-{seen[hw_id]}"
        tasks = []
        for task in tasks_for(session, messages, transcripts):
            found = [transcripts[i] for i in task.media_ids if i in transcripts]
            topic_ids = set(taxonomy.topic_for_task_text(task.text))
            for t in found:
                group, topic_id = taxonomy.resolve(t["topic_hint"])
                if t["topic_hint"].strip() and group != NEW_GROUP:
                    topic_ids.add(topic_id)
            tasks.append({
                "idx": task.idx,
                "type": task.type,
                "text": task.text,
                "media": [by_id[i]["media"] for i in task.media_ids if by_id[i]["media"]],
                "transcripts": [{k: t[k] for k in ("title", "body_md", "kind", "confidence")}
                                for t in found],
                "topic_ids": [tid for tid in order if tid in topic_ids],
                "done_key": f"{hw_id}:{task.idx}",
            })
        result.append({
            "id": hw_id,
            "assigned": session.date.isoformat(),
            "due": session.due.isoformat() if session.due else None,
            "due_flagged": session.due_flagged,
            "tasks": tasks,
        })
    return result[::-1]


def _grammar(occurrences, homework, taxonomy: Taxonomy) -> tuple[dict, dict[int, str]]:
    """Grammar tree plus a msg_id -> topic_id map for dictionary sources."""
    placed: dict[str, list[dict]] = {}
    new_titles: dict[str, str] = {}
    for occ in occurrences:
        hint = occ.pop("topic_hint")
        group, topic_id = taxonomy.resolve(hint)
        if group == NEW_GROUP:
            new_titles.setdefault(topic_id, hint)
        placed.setdefault(topic_id, []).append(occ)

    refs: dict[str, list[dict]] = {}
    for hw in homework:
        for task in hw["tasks"]:
            for topic_id in task["topic_ids"]:
                refs.setdefault(topic_id, []).append({"hw_id": hw["id"], "task_idx": task["idx"]})

    def topic(topic_id: str, title: str) -> dict:
        occs = sorted(placed.get(topic_id, []), key=lambda o: (o["date"], o["id"]))
        return {"id": topic_id, "title": title, "first_date": occs[0]["date"] if occs else None,
                "occurrences": occs, "task_refs": refs.get(topic_id, [])}

    groups = []
    for group in taxonomy.groups:
        topics = [topic(t["id"], t["title"]) for t in group["topics"]]
        if group["id"] == NEW_GROUP:
            topics += [topic(tid, title) for tid, title in new_titles.items()]
        groups.append({"id": group["id"], "title": group["title"], "topics": topics})

    msg_topics = {i: tid for tid, occs in placed.items() for o in occs for i in o["msg_ids"]}
    return {"groups": groups}, msg_topics


def _audio(messages) -> list[dict]:
    result = []
    for msg in messages:
        if msg["media_kind"] != "audio":
            continue
        caption = msg["text"].strip()
        if not caption:
            when = datetime.fromisoformat(msg["date"])
            caption = next(
                (m["text"].strip() for m in reversed(messages)
                 if m["id"] < msg["id"] and not m["media_kind"] and m["text"].strip()
                 and when - datetime.fromisoformat(m["date"]) <= AUDIO_CAPTION_WINDOW),
                "",
            ) or f"Трек {len(result) + 1}"
        result.append({"id": str(msg["id"]), "date": _day(msg), "file": msg["media"],
                       "caption": caption})
    return result


def _form_tokens(forms) -> list[str]:
    if isinstance(forms, str):
        return forms.split()
    values = forms.values() if isinstance(forms, dict) else forms or []
    return [token for value in values for token in _form_tokens(value)]


def _index_keys(entry: dict) -> set[str]:
    words = [entry["lemma"], *entry["surfaces"], *_form_tokens(entry["forms"])]
    keys = {normalize(w) for w in words}
    return {k for k in keys if len(k) > 1 and k not in NOT_FORMS and k not in STOPWORDS}


def _dictionary(lexicon, messages, msg_topics, homework) -> tuple[list[dict], dict, int]:
    dates = {m["id"]: _day(m) for m in messages}
    msg_hw = {}
    for hw in reversed(homework):  # oldest first, so a msg_id keeps its first homework
        for task in hw["tasks"]:
            for path in task["media"]:
                msg_hw.setdefault(_path_id(path), (hw["id"], hw["assigned"]))

    rows = []
    for key, entry in lexicon.items():
        if entry.get("proper_name"):
            continue
        known = sorted(dates[i] for i in entry["msg_ids"] if i in dates)
        sources: dict[tuple[str, str], str] = {}
        for msg_id in entry["msg_ids"]:
            if msg_id in msg_topics and msg_id in dates:
                found = ("topic", msg_topics[msg_id]), dates[msg_id]
            elif msg_id in msg_hw:
                found = ("hw", msg_hw[msg_id][0]), msg_hw[msg_id][1]
            else:
                continue
            sources[found[0]] = min(found[1], sources.get(found[0], found[1]))
        rows.append((key, entry, {
            "lemma": entry["lemma"],
            "key": key,
            **{k: entry.get(k) for k in ("translation", "pos", "gender", "class", "forms",
                                         "example")},
            "needs_review": bool(entry.get("needs_review")),
            "first_date": known[0] if known else None,
            "sources": [{"kind": kind, "id": sid, "date": day} for (kind, sid), day
                        in sorted(sources.items(), key=lambda s: (s[1], s[0]))],
        }))
    rows.sort(key=lambda r: (r[2]["first_date"] or "9999", r[0]))

    index: dict[str, str] = {}
    collisions = 0
    for key, entry, _ in rows:
        for form in sorted(_index_keys(entry)):
            if form not in index:
                index[form] = key
            elif index[form] != key:
                collisions += 1
    return [row for _, _, row in rows], dict(sorted(index.items())), collisions


def _path_id(path: str) -> int:
    return int(Path(path).stem)


def build_site(settings) -> dict:
    messages = load_messages(settings.state_dir)
    transcripts = load_transcripts(settings.transcripts_dir)
    taxonomy = load_topics(settings.content_dir / "topics.json")
    lexicon = load_lexicon(settings.lexicon_dir)

    occurrences = _occurrences(messages, transcripts)
    homework = _homework(messages, transcripts, taxonomy)
    grammar, msg_topics = _grammar(occurrences, homework, taxonomy)
    dictionary, forms_index, collisions = _dictionary(lexicon, messages, msg_topics, homework)
    topics = [t for g in grammar["groups"] for t in g["topics"]]
    return {
        "updated": max((_day(m) for m in messages), default=None),
        "grammar": grammar,
        "homework": homework,
        "dictionary": dictionary,
        "forms_index": forms_index,
        "audio": _audio(messages),
        "stats": {
            "topics": sum(1 for t in topics if t["occurrences"]),
            "occurrences": len(occurrences),
            "homework": len(homework),
            "tasks": sum(len(hw["tasks"]) for hw in homework),
            "words": len(dictionary),
            "form_collisions": collisions,
            "low_confidence": sum(1 for o in occurrences if o["confidence"] < LOW_CONFIDENCE),
        },
    }


def write_site(settings) -> Path:
    path = settings.content_dir / "site.json"
    tmp = path.with_suffix(".json.tmp")
    body = json.dumps(build_site(settings), ensure_ascii=False, indent=1) + "\n"
    tmp.write_text(body, "utf-8")
    os.replace(tmp, path)
    return path
