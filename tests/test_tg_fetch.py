import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeAudio,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

from greekpipe import tg_fetch
from greekpipe.config import Settings

TEACHER = 777
STORED_KEYS = {"id", "date", "text", "media", "media_kind", "reply_to", "grouped_id"}
DATE = datetime(2026, 9, 7, 12, 6, 19, tzinfo=timezone.utc)


def msg(id, text="", sender_id=TEACHER, media=None, reply_to=None, grouped_id=None):
    return SimpleNamespace(
        id=id, date=DATE, message=text, sender_id=sender_id,
        reply_to=SimpleNamespace(reply_to_msg_id=reply_to) if reply_to else None,
        grouped_id=grouped_id, media=media,
    )


def photo():
    return MessageMediaPhoto(photo=None)


def voice():
    doc = SimpleNamespace(mime_type="audio/ogg", attributes=[DocumentAttributeAudio(duration=3, voice=True)])
    return MessageMediaDocument(document=doc)


def mp3():
    doc = SimpleNamespace(mime_type="audio/mpeg", attributes=[DocumentAttributeAudio(duration=3)])
    return MessageMediaDocument(document=doc)


def pdf():
    doc = SimpleNamespace(mime_type="application/pdf", attributes=[])
    return MessageMediaDocument(document=doc)


class FakeClient:
    def __init__(self, messages, flood_after=None):
        self.messages = messages
        self.flood_after = flood_after
        self.flooded = False
        self.downloads = []

    async def get_entity(self, x):
        return x

    async def iter_messages(self, entity, min_id=0, reverse=True):
        for m in self.messages:
            if m.id <= min_id:
                continue
            yield m
            if m.id == self.flood_after and not self.flooded:
                self.flooded = True
                raise FloodWaitError(request=None, capture=0)

    async def download_media(self, m, file):
        self.downloads.append(m.id)
        with open(file, "wb") as f:
            f.write(b"x")
        return file


@pytest.fixture
def settings(tmp_path):
    return Settings(
        tg_api_id=1, tg_api_hash="h", tg_phone="p", tg_2fa_password="", tg_chat_id=-1,
        tg_teacher_id=TEACHER, openrouter_api_key="", or_vision_model="", or_text_model="",
        root=tmp_path, state_dir=tmp_path / "state", media_dir=tmp_path / "media",
        content_dir=tmp_path / "content", transcripts_dir=tmp_path / "content/transcripts",
        lexicon_dir=tmp_path / "content/lexicon",
    )


def test_strip_message_photo():
    out = tg_fetch.strip_message(msg(123, media=photo()), "media/123.jpg")
    assert set(out) == STORED_KEYS
    assert out["media_kind"] == "photo"
    assert out["media"] == "media/123.jpg"
    assert out["date"] == "2026-09-07T12:06:19+00:00"


def test_strip_message_webpage_is_text_only():
    out = tg_fetch.strip_message(msg(5, "see this", media=MessageMediaWebPage(webpage=None)), None)
    assert out["media"] is None
    assert out["media_kind"] is None
    assert out["text"] == "see this"


def test_strip_message_reply_and_group():
    out = tg_fetch.strip_message(msg(5, "x", reply_to=3, grouped_id=99), None)
    assert out["reply_to"] == 3
    assert out["grouped_id"] == 99


def test_media_filename():
    assert tg_fetch.media_filename(1, "photo") == "media/1.jpg"
    assert tg_fetch.media_filename(1, "audio") == "media/1.mp3"
    assert tg_fetch.media_filename(1, "pdf") == "media/1.pdf"


async def test_strip_message_voice_skipped(settings):
    client = FakeClient([msg(1, media=voice())])
    assert await tg_fetch.fetch_new(settings, client) == []
    assert not list((settings.state_dir / "messages").glob("*.json"))
    assert client.downloads == []


async def test_fetch_filters_non_teacher_and_advances_last_id(settings):
    client = FakeClient([msg(10, "hi", sender_id=1), msg(11, "text"), msg(12, media=photo())])
    assert await tg_fetch.fetch_new(settings, client) == [11, 12]
    names = sorted(p.name for p in (settings.state_dir / "messages").glob("*.json"))
    assert names == ["11.json", "12.json"]
    assert tg_fetch.read_last_id(settings.state_dir) == 12
    assert (settings.root / "media/12.jpg").exists()
    stored = json.loads((settings.state_dir / "messages/12.json").read_text("utf-8"))
    assert stored["media"] == "media/12.jpg"
    assert "sender_id" not in stored


async def test_fetch_media_kinds(settings):
    client = FakeClient([msg(1, media=mp3()), msg(2, media=pdf())])
    await tg_fetch.fetch_new(settings, client)
    assert (settings.root / "media/1.mp3").exists()
    assert (settings.root / "media/2.pdf").exists()


async def test_fetch_skips_empty_message_but_advances(settings):
    client = FakeClient([msg(1, ""), msg(2, "ok")])
    assert await tg_fetch.fetch_new(settings, client) == [2]
    assert tg_fetch.read_last_id(settings.state_dir) == 2


async def test_fetch_drops_meeting_links(settings):
    client = FakeClient([
        msg(1, "join https://us02web.ZOOM.us/j/123"),
        msg(2, "https://telemost.yandex.ru/j/1", media=photo()),
        msg(3, "normal"),
    ])
    assert await tg_fetch.fetch_new(settings, client) == [3]
    assert not (settings.root / "media/2.jpg").exists()
    assert tg_fetch.read_last_id(settings.state_dir) == 3


async def test_fetch_resumes_after_floodwait(settings, monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = FakeClient([msg(11, "a"), msg(12, "b")], flood_after=11)
    assert await tg_fetch.fetch_new(settings, client) == [11, 12]
    assert sleeps == [1]


async def test_fetch_is_incremental(settings):
    client = FakeClient([msg(1, "a"), msg(2, "b")])
    await tg_fetch.fetch_new(settings, client)
    assert await tg_fetch.fetch_new(settings, client) == []


def test_read_last_id_missing(tmp_path):
    assert tg_fetch.read_last_id(tmp_path) == 0


def test_load_messages_sorted(tmp_path):
    d = tmp_path / "messages"
    d.mkdir()
    for i in (10, 2, 33):
        (d / f"{i}.json").write_text(json.dumps({"id": i}), "utf-8")
    assert [m["id"] for m in tg_fetch.load_messages(tmp_path)] == [2, 10, 33]
