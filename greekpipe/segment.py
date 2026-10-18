"""Group the teacher's messages into lesson/homework/chat sessions and homework tasks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

SESSION_GAP = timedelta(hours=2)
DUE_FALLBACK = timedelta(days=2)
NOT_LESSON_KINDS = frozenset({"chat", "other"})
AUDIO_KINDS = frozenset({"audio", "voice"})
PHOTO_TASK_TITLE = "Задание по фото"
AUDIO_TASK_TITLE = "Аудио"

_MARKER_WORDS = r"д/з|домашн\w*\s+задани\w*|высылаю задани\w*"
_MARKER = re.compile(_MARKER_WORDS, re.IGNORECASE)
_DUE_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\b")
_DUE_TOMORROW = re.compile(r"^\W*на\s+завтра", re.IGNORECASE)
_INSTRUCTION = re.compile(r"тетрад|упражн|выполн|прочита|повтор|письменно|устно|сочинен|перев|выучи|\bучи[мт]|состав|прослуш", re.IGNORECASE)
_MEETING = re.compile(r"telemost|zoom", re.IGNORECASE)
_GREETING = re.compile(r"^(доброе утро|добрый день|добрый вечер|привет)[!.,]*\s*", re.IGNORECASE)
_MARKER_HEAD = re.compile(
    rf"^({_MARKER_WORDS})(\s+на\s+(\d{{1,2}}\.\d{{1,2}}\.?|[^:\n]*:?))?[.:!]*\s*",
    re.IGNORECASE,
)
_CLOSING = re.compile(r"^(хорошего (дня|вечера)|если есть вопросы|задания выслала)", re.IGNORECASE)
_CONTINUATION = re.compile(r"^(попробуй|потренируйся)|^(https?://|www\.)\S+$", re.IGNORECASE)
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_INSTRUCTION_END = re.compile(r"[:\n]")

_TYPE_RULES = [
    (re.compile(r"тетрад", re.IGNORECASE), "тетрадь"),
    (re.compile(r"повтор|знать|выучить", re.IGNORECASE), "повторить"),
    (re.compile(r"сочинен|составить", re.IGNORECASE), "сочинение"),
    (re.compile(r"прочитать|текст|перев", re.IGNORECASE), "текст"),
    (re.compile(r"прослуш|трек|аудио", re.IGNORECASE), "аудио"),
    (re.compile(r"упр|выполн|письменно|устно|раскр", re.IGNORECASE), "упражнение"),
]
_KIND_TYPES = {
    "exercise": "упражнение",
    "text": "текст",
    "table": "повторить",
    "grammar": "повторить",
    "vocab": "повторить",
}


@dataclass
class Session:
    kind: Literal["lesson", "homework", "chat"]
    date: date
    message_ids: list[int]
    due: date | None
    due_flagged: bool


@dataclass
class Task:
    idx: int
    type: str
    text: str
    media_ids: list[int]


def task_type(text: str, transcript_kind: str | None) -> str:
    # Classify by the instruction head only: content after «:» (sentences to
    # translate etc.) mentions тетрадь/текст incidentally.
    head = _INSTRUCTION_END.split(text, maxsplit=1)[0]
    for pattern, kind in _TYPE_RULES:
        if pattern.search(head):
            return kind
    return _KIND_TYPES.get(transcript_kind, "задание")


def _when(msg: dict) -> datetime:
    return datetime.fromisoformat(msg["date"])


def _has_marker(msg: dict) -> bool:
    return bool(_MARKER.search(msg["text"] or ""))


def _groups(messages: list[dict]) -> list[list[dict]]:
    """Split on gaps > SESSION_GAP and on the first homework marker of a session."""
    groups: list[list[dict]] = []
    for msg in sorted(messages, key=lambda m: m["id"]):
        if (
            groups
            and _when(msg) - _when(groups[-1][-1]) <= SESSION_GAP
            and not (_has_marker(msg) and not any(_has_marker(m) for m in groups[-1]))
        ):
            groups[-1].append(msg)
        else:
            groups.append([msg])
    return groups


def _explicit_due(text: str, assigned: date) -> date | None:
    # Only the marker line: task lists below it carry numbers like «упр. 3.4».
    line = next(line for line in text.splitlines() if _MARKER.search(line))
    rest = line[_MARKER.search(line).end():]
    if _DUE_TOMORROW.match(rest):
        return assigned + timedelta(days=1)
    found = _DUE_DATE.search(rest)
    if not found:
        return None
    day, month = int(found.group(1)), int(found.group(2))
    try:
        due = date(assigned.year, month, day)
        return due if due >= assigned else date(assigned.year + 1, month, day)
    except ValueError:
        return None


def _is_instruction(text: str | None) -> bool:
    # Head only: lesson notes (conjugation tables, vocab lists) mention
    # «сочинение»/«выполнять» deep inside their content.
    head = _INSTRUCTION_END.split(_instruction(text), maxsplit=1)[0]
    return bool(_INSTRUCTION.search(head))


def _kind(group: list[dict], transcripts: dict[int, dict]) -> str:
    if any(_has_marker(m) for m in group):
        return "homework"
    if any(_MEETING.search(m["text"] or "") for m in group):
        return "lesson"
    if any(not m["media_kind"] and _is_instruction(m["text"]) for m in group):
        return "homework"
    for msg in group:
        kind = transcripts.get(msg["id"], {}).get("kind")
        if msg["media_kind"] and kind is not None and kind not in NOT_LESSON_KINDS:
            return "lesson"
    return "chat"


def sessions(messages: list[dict], transcripts: dict[int, dict]) -> list[Session]:
    result: list[Session] = []
    explicit: dict[int, date | None] = {}
    for group in _groups(messages):
        day = _when(group[0]).date()
        kind = _kind(group, transcripts)
        if kind == "homework":
            marker = next((m for m in group if _has_marker(m)), None)
            explicit[len(result)] = _explicit_due(marker["text"], day) if marker else None
        result.append(Session(kind, day, [m["id"] for m in group], None, False))

    for i, due in explicit.items():
        hw = result[i]
        if due is None:
            due = next((s.date for s in result if s.kind == "lesson" and s.date > hw.date), None)
        if due is None:
            hw.due, hw.due_flagged = hw.date + DUE_FALLBACK, True
        else:
            hw.due = due
    return result


def _instruction(text: str) -> str:
    """The message text without greetings, the homework marker and closing phrases."""
    lines = []
    for line in (text or "").splitlines():
        line = _MARKER_HEAD.sub("", _GREETING.sub("", line.strip())).strip()
        if line and not _CLOSING.match(line):
            lines.append(line)
    return "\n".join(lines)


def tasks_for(session: Session, messages: list[dict], transcripts: dict[int, dict]) -> list[Task]:
    if session.kind == "chat":
        return []
    by_id = {m["id"]: m for m in messages}
    msgs = [by_id[i] for i in sorted(session.message_ids) if i in by_id]
    media = {m["id"] for m in msgs if m["media_kind"]}
    reply_targets = {m["reply_to"] for m in msgs if m["reply_to"] in media}

    tasks: list[Task] = []
    owner: dict[int, Task] = {}
    held: dict[int, Task | None] = {}  # reply-targeted media -> task current when it arrived
    albums: dict[int, Task] = {}
    current: Task | None = None

    def new_task(type_: str, text: str) -> Task:
        task = Task(len(tasks), type_, text, [])
        tasks.append(task)
        return task

    def attach(task: Task, msg_id: int) -> None:
        task.media_ids.append(msg_id)
        owner[msg_id] = task

    def media_task(msg: dict) -> Task:
        album = msg["grouped_id"]
        if album in albums:
            return albums[album]
        transcript = transcripts.get(msg["id"], {})
        if msg["media_kind"] in AUDIO_KINDS:
            task = new_task("аудио", transcript.get("title") or AUDIO_TASK_TITLE)
        else:
            task = new_task(task_type("", transcript.get("kind")), transcript.get("title") or PHOTO_TASK_TITLE)
        if album is not None:
            albums[album] = task
        return task

    for msg in msgs:
        reply = msg["reply_to"] if msg["reply_to"] in media else None
        if msg["media_kind"]:
            if msg["id"] in reply_targets:
                held[msg["id"]] = current
                continue
            task = owner.get(reply) or current or media_task(msg)
            attach(task, msg["id"])
            if reply in held:
                del held[reply]
                attach(task, reply)
            continue

        kind = transcripts.get(msg["id"], {}).get("kind")
        text = _instruction(msg["text"])
        continuation = (
            reply in owner
            or reply in held
            or (current is not None and bool(_CONTINUATION.search(text) or not _CYRILLIC.search(text)))
        )
        if not text or (kind in (None, "chat") and not _is_instruction(text) and not continuation):
            continue
        if reply in owner:
            owner[reply].text += "\n" + text
        elif reply in held:
            del held[reply]
            current = new_task(task_type(text, kind), text)
            attach(current, reply)
        elif current is not None and (_CONTINUATION.search(text) or not _CYRILLIC.search(text)):
            current.text += "\n" + text
        else:
            current = new_task(task_type(text, kind), text)

    for msg_id, task in held.items():
        attach(task or media_task(by_id[msg_id]), msg_id)
    for task in tasks:
        task.media_ids.sort()
    return tasks
