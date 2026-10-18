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


def _msg(id, time, text="", media_kind=None, grouped_id=None, day="2026-10-11"):
    return {"id": id, "date": f"{day}T{time}:00+00:00", "text": text, "media": None,
            "media_kind": media_kind, "reply_to": None, "grouped_id": grouped_id}


def test_marker_starts_new_session():
    # Shape of 11.10: lesson slides, then «Д/з на завтра» 18 min later.
    msgs = [
        _msg(124117, "11:01", "Добрый день!"),
        *[_msg(i, "12:34", media_kind="photo") for i in range(124118, 124125)],
        _msg(124125, "13:15", "Д/з на завтра"),
        _msg(124126, "13:15", media_kind="photo", grouped_id=7),
        _msg(124127, "13:15", media_kind="photo", grouped_id=7),
        _msg(124128, "13:15", "Повторить спряжение глаголов 1 и 2, наречия времени"),
        _msg(124129, "13:16", "Хорошего вечера!"),
    ]
    transcripts = {i: {"kind": "exercise"} for i in range(124118, 124125)}
    lesson, hw = segment.sessions(msgs, transcripts)

    assert (lesson.kind, lesson.message_ids) == ("lesson", list(range(124117, 124125)))
    assert (hw.kind, hw.message_ids) == ("homework", list(range(124125, 124130)))
    assert (hw.due, hw.due_flagged) == (date(2026, 10, 12), False)
    tasks = segment.tasks_for(hw, msgs, {124126: {"kind": "text", "title": "Диалог"}})
    assert [(t.type, t.text, t.media_ids) for t in tasks] == [
        ("текст", "Диалог", [124126, 124127]),
        ("повторить", "Повторить спряжение глаголов 1 и 2, наречия времени", []),
    ]


def test_unmarked_instruction_session_is_homework():
    # Shape of 16.10: two exercise photos, then the instruction without a marker.
    msgs = [
        _msg(124267, "08:29", media_kind="photo", day="2026-10-16"),
        _msg(124268, "08:30", media_kind="photo", day="2026-10-16"),
        _msg(124269, "08:42", "Тетрадь с упражнениями ( закрепл.) с. 29, 30, 33", day="2026-10-16"),
    ]
    transcripts = {124267: {"kind": "exercise"}, 124268: {"kind": "exercise"}}
    [hw] = segment.sessions(msgs, transcripts)
    assert hw.kind == "homework"
    assert (hw.due, hw.due_flagged) == (date(2026, 10, 18), True)
    # The transcriber labelled 124269 «chat»; an instruction head still makes it a task.
    tasks = segment.tasks_for(hw, msgs, {**transcripts, 124269: {"kind": "chat"}})
    assert [(t.type, t.media_ids) for t in tasks] == [
        ("упражнение", [124267]), ("упражнение", [124268]), ("тетрадь", []),
    ]

    lesson = _msg(124400, "16:00", "Заходите в zoom", day="2026-10-19")
    hw = segment.sessions([*msgs, lesson], transcripts)[0]
    assert (hw.due, hw.due_flagged) == (date(2026, 10, 19), False)


def test_marker_does_not_split_homework_twice():
    msgs = [_msg(1, "08:00", "Д/з на 14.10."), _msg(2, "08:05", "Домашнее задание: повторить")]
    assert [s.message_ids for s in segment.sessions(msgs, {})] == [[1, 2]]


def test_marker_suffix_is_not_a_task():
    # Shape of 03.09 (122078-122081).
    msgs = [
        _msg(122078, "06:24", "Доброе утро! \nД/з на след. занятие:", day="2026-09-03"),
        _msg(122079, "06:24", media_kind="photo", day="2026-09-03"),
        _msg(122080, "06:25", media_kind="audio", day="2026-09-03"),
        _msg(122081, "06:26", "Прослушать 1 трек, произношение, название букв алфавита, страны", day="2026-09-03"),
    ]
    transcripts = {122079: {"kind": "table", "title": "Таблица алфавита"}}
    [hw] = segment.sessions(msgs, transcripts)
    tasks = segment.tasks_for(hw, msgs, transcripts)

    assert not any(t.text.startswith("на ") for t in tasks)
    # Leading media keep the one-task-per-photo/audio rule; the instruction follows them.
    assert [(t.type, t.text, t.media_ids) for t in tasks] == [
        ("повторить", "Таблица алфавита", [122079]),
        ("аудио", "Аудио", [122080]),
        ("аудио", "Прослушать 1 трек, произношение, название букв алфавита, страны", []),
    ]


@pytest.mark.parametrize("text,is_marker", [
    ("Домашние задания на 20.10", True),
    ("Домашнее задание на 14.09.", True),
    ("Д/з", True),
    ("Высылаю задание", True),
    ("Задания скину, обязательно", False),
])
def test_marker_regex(text, is_marker):
    assert segment._has_marker({"text": text}) is is_marker


@pytest.mark.parametrize("day,text,due,flagged", [
    ("2026-12-28", "Д/з на 05.01", date(2027, 1, 5), False),
    ("2026-09-10", "Домашнее задание на 14.09.\nупр. 3.4 письменно", date(2026, 9, 14), False),
    ("2026-09-10", "Д/з\nупр. 3.4 письменно", date(2026, 9, 12), True),
])
def test_due_from_marker_line(day, text, due, flagged):
    [hw] = segment.sessions([_msg(1, "08:00", text, day=day)], {})
    assert (hw.kind, hw.due, hw.due_flagged) == ("homework", due, flagged)
