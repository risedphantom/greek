import asyncio
import json
import os
import re
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeAudio,
    MessageMediaDocument,
    MessageMediaPhoto,
)

MEETING_LINK = re.compile(r"zoom\.us|telemost\.yandex", re.IGNORECASE)
_EXTENSIONS = {"photo": "jpg", "audio": "mp3", "pdf": "pdf"}


def media_filename(msg_id: int, kind: str) -> str:
    return f"media/{msg_id}.{_EXTENSIONS[kind]}"


def _document(media):
    return media.document if isinstance(media, MessageMediaDocument) else None


def _is_voice(media) -> bool:
    document = _document(media)
    return document is not None and any(
        isinstance(a, DocumentAttributeAudio) and a.voice for a in document.attributes
    )


def media_kind(media) -> str | None:
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    document = _document(media)
    if document is None:
        return None
    mime = document.mime_type or ""
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    return None


def strip_message(m, media_path: str | None) -> dict:
    kind = media_kind(m.media) if media_path else None
    return {
        "id": m.id,
        "date": m.date.isoformat(),
        "text": m.message or "",
        "media": media_path if kind else None,
        "media_kind": kind,
        "reply_to": m.reply_to.reply_to_msg_id if m.reply_to else None,
        "grouped_id": m.grouped_id,
    }


def read_last_id(state_dir: Path) -> int:
    path = Path(state_dir) / "last_id"
    return int(path.read_text().strip()) if path.exists() else 0


def load_messages(state_dir: Path) -> list[dict]:
    files = (Path(state_dir) / "messages").glob("*.json")
    messages = [json.loads(p.read_text("utf-8")) for p in files]
    return sorted(messages, key=lambda m: m["id"])


def _write_message(state_dir: Path, message: dict) -> None:
    messages_dir = state_dir / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(message, ensure_ascii=False, indent=2) + "\n"
    path = messages_dir / f"{message['id']}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, "utf-8")
    os.replace(tmp, path)


async def fetch_new(settings, client=None) -> list[int]:
    if client is None:
        client = TelegramClient(
            str(settings.root / "tg.session"), settings.tg_api_id, settings.tg_api_hash
        )
        await client.start(phone=settings.tg_phone, password=settings.tg_2fa_password)
    entity = await client.get_entity(settings.tg_chat_id)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    written: list[int] = []
    last_id = read_last_id(settings.state_dir)
    while True:
        try:
            async for m in client.iter_messages(entity, min_id=last_id, reverse=True):
                if await _store(settings, client, m):
                    written.append(m.id)
                last_id = m.id
                (settings.state_dir / "last_id").write_text(f"{last_id}\n")
            return written
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)


async def _store(settings, client, m) -> bool:
    text = m.message or ""
    if m.sender_id != settings.tg_teacher_id or _is_voice(m.media):
        return False
    if MEETING_LINK.search(text):
        return False
    kind = media_kind(m.media)
    if not text and kind is None:
        return False
    media_path = None
    if kind:
        media_path = media_filename(m.id, kind)
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        await client.download_media(m, file=str(settings.root / media_path))
    _write_message(settings.state_dir, strip_message(m, media_path))
    return True
