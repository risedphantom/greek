import json
from datetime import date
from pathlib import Path

import pytest

from greekpipe import segment
from greekpipe.segment import Session

FIXTURE = Path(__file__).parent / "fixtures" / "messages_sample.json"

# The fixture ships no transcripts; these mark lesson photos as teaching content.
TRANSCRIPTS = {
    122493: {"kind": "exercise"},
    122819: {"kind": "grammar"},
    122980: {"kind": "vocab"},
}


@pytest.fixture
def messages():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def by_id(messages):
    return {m["id"]: m for m in messages}


def session_of(sessions, msg_id):
    return next(s for s in sessions if msg_id in s.message_ids)


def test_sessions_split_and_kind(messages):
    sessions = segment.sessions(messages, TRANSCRIPTS)

    hw_18 = session_of(sessions, 123086)
    assert hw_18.kind == "homework"
    assert hw_18.date == date(2026, 9, 18)
    assert hw_18.due == date(2026, 9, 21)
    assert hw_18.due_flagged is False
    assert hw_18.message_ids == list(range(123086, 123096))

    lesson = session_of(sessions, 122493)
    assert lesson.kind == "lesson"
    assert all(i in lesson.message_ids for i in range(122493, 122500))
    assert 122482 not in lesson.message_ids  # 12:56 -> 16:03 gap splits

    hw_10 = session_of(sessions, 122632)
    assert hw_10.kind == "homework"
    assert hw_10.due == date(2026, 9, 14)
    assert hw_10.due_flagged is False

    chat = session_of(sessions, 123206)
    assert chat.kind == "chat"
    assert chat.message_ids == [123206, 123207, 123208]


def test_lesson_detected_by_meeting_link_text():
    msgs = [{"id": 1, "date": "2026-09-01T10:00:00+00:00", "text": "Заходите в Telemost",
             "media": None, "media_kind": None, "reply_to": None, "grouped_id": None}]
    assert segment.sessions(msgs, {})[0].kind == "lesson"


def test_photo_without_transcript_is_not_lesson(messages):
    sessions = segment.sessions(messages, {})
    assert session_of(sessions, 122819).kind == "chat"


def test_due_falls_back_to_next_lesson(messages):
    for m in messages:
        if m["id"] == 122632:
            m["text"] = m["text"].replace("на 14.09.", "")
    hw = session_of(segment.sessions(messages, TRANSCRIPTS), 122632)
    assert hw.kind == "homework"
    assert hw.due == date(2026, 9, 14)
    assert hw.due_flagged is False


def test_due_flagged_when_no_later_lesson(messages):
    sessions = segment.sessions(messages, TRANSCRIPTS)
    last = sessions[-1]
    assert 123357 in last.message_ids  # «Доброе утро! Высылаю задания»
    assert last.kind == "homework"
    assert last.date == date(2026, 9, 24)
    assert last.due == date(2026, 9, 26)
    assert last.due_flagged is True


def test_due_tomorrow():
    msgs = [{"id": 1, "date": "2026-09-01T10:00:00+00:00", "text": "Д/з на завтра",
             "media": None, "media_kind": None, "reply_to": None, "grouped_id": None}]
    s = segment.sessions(msgs, {})[0]
    assert (s.kind, s.due, s.due_flagged) == ("homework", date(2026, 9, 2), False)


def test_tasks_18_09(messages):
    hw = session_of(segment.sessions(messages, TRANSCRIPTS), 123086)
    tasks = segment.tasks_for(hw, messages, TRANSCRIPTS)

    assert [t.type for t in tasks] == ["повторить", "упражнение", "тетрадь", "сочинение"]
    assert [t.idx for t in tasks] == [0, 1, 2, 3]
    assert tasks[0].text.startswith("1) повторить предлог σε")
    assert tasks[0].media_ids == [123088, 123089]
    assert tasks[1].text == "2) выполнить упражнения 7,8 устно, 9 письменно"
    assert tasks[1].media_ids == []
    assert "https://www.autodraw.com/" in tasks[2].text
    assert "Попробуй через этот тренажёр" in tasks[2].text
    assert all("Хорошего дня" not in t.text for t in tasks)


def test_reply_to_photo_attaches(messages):
    hw = session_of(segment.sessions(messages, TRANSCRIPTS), 122632)
    tasks = segment.tasks_for(hw, messages, TRANSCRIPTS)

    assert [(t.type, t.media_ids) for t in tasks[:3]] == [
        ("повторить", [122635]),
        ("тетрадь", []),
        ("упражнение", [122637, 122639]),
    ]
    assert tasks[0].text.startswith("1) Перевести предложения")
    assert "γράφω" in tasks[0].text and "καθίζω" in tasks[0].text
    assert tasks[2].text == "Выполнить задания"
    # 122653 (10:41, within 2h of 08:45) is a follow-up note from the teacher.
    assert [t.text for t in tasks[3:]] == [by_id(messages)[122653]["text"]]


def test_leading_photos_one_task_per_album_or_photo(messages):
    hw = session_of(segment.sessions(messages, TRANSCRIPTS), 123357)
    transcripts = {123358: {"kind": "exercise", "title": "Упражнения 1-6"}}
    tasks = segment.tasks_for(hw, messages, transcripts)

    assert [(t.type, t.text, t.media_ids) for t in tasks] == [
        ("упражнение", "Упражнения 1-6", list(range(123358, 123364))),
        ("задание", "Задание по фото", [123364]),
        ("задание", "Задание по фото", [123365]),
        ("повторить", by_id(messages)[123366]["text"], [123367]),
    ]


def test_dangling_reply_to_is_ignored(messages):
    hw = session_of(segment.sessions(messages, TRANSCRIPTS), 122847)
    tasks = segment.tasks_for(hw, messages, TRANSCRIPTS)
    assert [t.media_ids for t in tasks] == [[122848], [122849]]


def test_chat_messages_never_tasks(messages):
    chat = Session(kind="chat", date=date(2026, 9, 16), message_ids=[122987], due=None, due_flagged=False)
    assert segment.tasks_for(chat, messages, {}) == []

    hw = Session(kind="homework", date=date(2026, 9, 16), message_ids=[122987], due=None, due_flagged=False)
    assert segment.tasks_for(hw, messages, {122987: {"kind": "chat"}}) == []


@pytest.mark.parametrize("text,kind,expected", [
    ("Тетрадь упражнений с. 3 и 4 прописать", None, "тетрадь"),
    ("1) повторить предлог σε", None, "повторить"),
    ("составить предложения", None, "сочинение"),
    ("прочитать текст", None, "текст"),
    ("прослушать трек", None, "аудио"),
    ("выполнить упражнения 7,8 устно", None, "упражнение"),
    ("Перевести предложения, выучить глаголы: 1. Кладет тетрадь.", None, "повторить"),
    ("", "exercise", "упражнение"),
    ("", "text", "текст"),
    ("", "table", "повторить"),
    ("", None, "задание"),
])
def test_task_type(text, kind, expected):
    assert segment.task_type(text, kind) == expected
