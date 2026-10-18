import json
import re
from pathlib import Path


from greekpipe.config import STOPWORDS, load_settings, normalize

ENV = """\
TG_API_ID=12345
TG_API_HASH=abc
TG_PHONE=+10000000000
TG_2FA_PASSWORD=secret
TG_CHAT_ID=-CHAT_ID
TG_TEACHER_ID=TEACHER_ID
OPENROUTER_API_KEY=sk-test
"""


def test_load_settings_defaults_models(tmp_path, monkeypatch):
    monkeypatch.delenv("OR_VISION_MODEL", raising=False)
    monkeypatch.delenv("OR_TEXT_MODEL", raising=False)
    (tmp_path / ".env").write_text(ENV)
    s = load_settings(tmp_path)
    assert s.or_vision_model == "google/gemini-2.5-flash"
    assert s.or_text_model == "google/gemini-2.5-flash-lite"
    assert s.tg_chat_id == -CHAT_ID
    assert s.tg_api_id == 12345
    assert s.transcripts_dir == tmp_path / "content" / "transcripts"


def test_load_settings_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_PHONE", raising=False)
    (tmp_path / ".env").write_text(ENV.replace("TG_PHONE=+10000000000\n", ""))
    try:
        load_settings(tmp_path)
    except KeyError as e:
        assert "TG_PHONE" in str(e)
    else:
        raise AssertionError("expected KeyError")


def test_normalize():
    assert normalize("Τρώει;") == "τρώει"
    assert normalize("φοιτητής") == "φοιτητήσ"
    assert normalize("ΚΑΛΗΜΕΡΑ,") == "καλημερα"
    assert normalize("Καλημέρα") == "καλημέρα"


def test_stopwords_normalized():
    assert STOPWORDS
    for item in STOPWORDS:
        assert item == normalize(item)


def test_normalize_greek_punctuation():
    assert normalize("Τρώει;") == "τρώει"  # U+037E Greek question mark
    assert normalize("καλά·") == "καλά"  # U+0387 ano teleia


def test_fixture_has_no_meeting_links():
    path = Path(__file__).parent / "fixtures" / "messages_sample.json"
    messages = json.loads(path.read_text())
    keys = {"id", "date", "text", "media", "media_kind", "reply_to", "grouped_id"}
    for m in messages:
        assert set(m) == keys
        assert not re.search(r"zoom\.us|telemost\.yandex", m["text"] or "")
