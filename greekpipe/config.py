import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_VISION_MODEL = "google/gemini-2.5-flash"
DEFAULT_TEXT_MODEL = "google/gemini-2.5-flash-lite"

_PUNCTUATION = dict.fromkeys(map(ord, ';,.!?«»–—()[]:"\'…'))


@dataclass(frozen=True)
class Settings:
    tg_api_id: int
    tg_api_hash: str
    tg_phone: str
    tg_2fa_password: str
    tg_chat_id: int
    tg_teacher_id: int
    openrouter_api_key: str
    or_vision_model: str
    or_text_model: str
    root: Path
    state_dir: Path
    media_dir: Path
    content_dir: Path
    transcripts_dir: Path
    lexicon_dir: Path


def load_settings(root: Path | None = None) -> Settings:
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    file_values = dotenv_values(root / ".env")

    def get(key: str, default: str | None = None) -> str:
        value = file_values.get(key) or os.environ.get(key)
        if value:
            return value
        if default is None:
            raise KeyError(key)
        return default

    content_dir = root / "content"
    return Settings(
        tg_api_id=int(get("TG_API_ID")),
        tg_api_hash=get("TG_API_HASH"),
        tg_phone=get("TG_PHONE"),
        tg_2fa_password=get("TG_2FA_PASSWORD", ""),
        tg_chat_id=int(get("TG_CHAT_ID")),
        tg_teacher_id=int(get("TG_TEACHER_ID")),
        openrouter_api_key=get("OPENROUTER_API_KEY"),
        or_vision_model=get("OR_VISION_MODEL", DEFAULT_VISION_MODEL),
        or_text_model=get("OR_TEXT_MODEL", DEFAULT_TEXT_MODEL),
        root=root,
        state_dir=root / "state",
        media_dir=root / "media",
        content_dir=content_dir,
        transcripts_dir=content_dir / "transcripts",
        lexicon_dir=content_dir / "lexicon",
    )


def normalize(word: str) -> str:
    word = unicodedata.normalize("NFC", word.translate(_PUNCTUATION))
    return word.casefold().replace("ς", "σ")


STOPWORDS = frozenset(
    normalize(w)
    for w in (
        "ο η το οι τα τον την του της των τους τις και δεν να σε με από για που "
        "είναι εγώ εσύ αυτός αυτή αυτό εμείς εσείς αυτοί αυτές αυτά μου σου μας "
        "σας ναι όχι τι πού πώς"
    ).split()
)
