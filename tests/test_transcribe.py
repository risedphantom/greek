import json

import pytest

from greekpipe import transcribe
from greekpipe.config import Settings
from greekpipe.llm import LLMAuthError, LLMBadOutput, LLMTransient

DATE = "2026-09-07T12:06:19+00:00"
VOCAB = "η Δευτέρα понедельник\nη Τρίτη вторник\nη Τετάρτη среда\nη Πέμπτη четверг"


def make_settings(root):
    return Settings(
        tg_api_id=1, tg_api_hash="h", tg_phone="+1", tg_2fa_password="",
        tg_chat_id=-1, tg_teacher_id=1, openrouter_api_key="sk-test",
        or_vision_model="vision/model", or_text_model="text/model",
        root=root, state_dir=root / "state", media_dir=root / "media",
        content_dir=root / "content", transcripts_dir=root / "content" / "transcripts",
        lexicon_dir=root / "content" / "lexicon",
    )


def store(settings, msg):
    messages_dir = settings.state_dir / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    (messages_dir / f"{msg['id']}.json").write_text(json.dumps(msg, ensure_ascii=False), "utf-8")
    if msg["media"]:
        path = settings.root / msg["media"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8fake-jpeg")


def photo_msg(id, text=""):
    return {"id": id, "date": DATE, "text": text, "media": f"media/{id}.jpg",
            "media_kind": "photo", "reply_to": None, "grouped_id": None}


def text_msg(id, text):
    return {"id": id, "date": DATE, "text": text, "media": None,
            "media_kind": None, "reply_to": None, "grouped_id": None}


def reply(**overrides):
    out = {
        "kind": "table", "title": "Склонение", "body_md": "| a | b |\n|---|---|",
        "greek_words": [{"lemma": "μέρα", "pos": "noun", "surface": ["μέρες"]}],
        "topic_hint": "Склонение ж.р. -α", "confidence": 0.95,
    }
    out.update(overrides)
    return out


class FakeLLM:
    """Records calls; per msg id returns a canned dict or raises a canned exception."""

    def __init__(self, replies=None, default=None):
        self.replies = replies or {}
        self.default = default if default is not None else reply()
        self.calls = []
        self.last_cost = 0.0

    def chat_json(self, model, system, user_parts, schema_name):
        msg_id = int(schema_name.rsplit("-", 1)[1])
        self.calls.append({"model": model, "system": system, "user_parts": user_parts, "msg_id": msg_id})
        self.last_cost += 0.001
        result = self.replies.get(msg_id, self.default)
        if isinstance(result, Exception):
            raise result
        return result


def read_transcript(settings, msg_id):
    return json.loads((settings.transcripts_dir / f"{msg_id}.json").read_text("utf-8"))


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def test_is_candidate():
    assert transcribe.is_candidate(photo_msg(1))
    assert not transcribe.is_candidate(text_msg(2, "Интернет пропал"))
    assert not transcribe.is_candidate(text_msg(3, "https://telemost.yandex.ru/j/1"))
    vocab = ("η Δευτέρα – понедельник, η Τρίτη – вторник, η Τετάρτη – среда")[:60]
    assert len(vocab) == 60
    assert transcribe.is_candidate(text_msg(4, vocab))
    assert not transcribe.is_candidate(text_msg(5, "Добрый день! Д/з"))


def test_is_candidate_ignores_emoji_and_greetings_with_long_text():
    assert not transcribe.is_candidate(text_msg(1, "🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷🇬🇷 ок"))
    assert not transcribe.is_candidate(text_msg(2, "Добрый день! Задания выслала, будут вопросы пишите. Хорошего дня!"))
    assert not transcribe.is_candidate({**photo_msg(3), "media_kind": "pdf", "media": "media/3.pdf"})


def test_transcribe_writes_file_and_skips_existing(settings):
    store(settings, photo_msg(10))
    store(settings, photo_msg(11, "Таблица"))
    settings.transcripts_dir.mkdir(parents=True)
    (settings.transcripts_dir / "10.json").write_text('{"msg_id": 10}\n', "utf-8")
    fake = FakeLLM()

    report = transcribe.transcribe_pending(settings, fake)

    assert [c["msg_id"] for c in fake.calls] == [11]
    assert report.done == [11]
    assert (settings.transcripts_dir / "10.json").read_text("utf-8") == '{"msg_id": 10}\n'
    raw = (settings.transcripts_dir / "11.json").read_text("utf-8")
    assert raw.endswith("}\n") and "Склонение ж.р. -α" in raw
    t = json.loads(raw)
    assert t == {
        "msg_id": 11, "source": "photo", "kind": "table", "title": "Склонение",
        "body_md": "| a | b |\n|---|---|",
        "greek_words": [{"lemma": "μέρα", "pos": "noun", "surface": ["μέρες"]}],
        "topic_hint": "Склонение ж.р. -α", "confidence": 0.95, "model": "vision/model",
    }
    assert report.cost == pytest.approx(0.001)


def test_photo_user_parts_carry_caption_date_and_image(settings):
    store(settings, photo_msg(20, "Упражнение 7"))
    store(settings, photo_msg(21))
    fake = FakeLLM()

    transcribe.transcribe_pending(settings, fake)

    with_caption, without = (c["user_parts"] for c in fake.calls)
    assert with_caption[0]["type"] == "text"
    assert "Сообщение учителя (подпись к фото): Упражнение 7" in with_caption[0]["text"]
    assert DATE[:10] in with_caption[0]["text"]
    assert with_caption[1]["type"] == "image_url"
    assert "Подписи нет." in without[0]["text"]


def test_transcribe_honours_limit_in_id_order(settings):
    for i in (32, 30, 31):
        store(settings, photo_msg(i))
    fake = FakeLLM()

    report = transcribe.transcribe_pending(settings, fake, limit=2)

    assert report.done == [30, 31]
    assert not (settings.transcripts_dir / "32.json").exists()


def test_transcribe_marks_low_confidence(settings):
    store(settings, photo_msg(40))
    report = transcribe.transcribe_pending(settings, FakeLLM(default=reply(confidence=0.5)))

    assert report.low_confidence == [40]
    assert report.done == [40]
    assert read_transcript(settings, 40)["confidence"] == 0.5


def test_transient_error_leaves_pending(settings):
    store(settings, photo_msg(50))
    store(settings, photo_msg(51))
    fake = FakeLLM(replies={50: LLMTransient("429")})

    report = transcribe.transcribe_pending(settings, fake)

    assert report.pending == [50]
    assert report.done == [51]
    assert not (settings.transcripts_dir / "50.json").exists()


def test_bad_output_writes_other_kind_with_zero_confidence(settings):
    store(settings, photo_msg(60))
    report = transcribe.transcribe_pending(settings, FakeLLM(replies={60: LLMBadOutput("not json")}))

    t = read_transcript(settings, 60)
    assert t["kind"] == "other"
    assert t["confidence"] == 0.0
    assert t["msg_id"] == 60 and t["source"] == "photo" and t["model"] == "vision/model"
    assert report.low_confidence == [60]
    assert report.done == [60]


def test_auth_error_aborts_run_after_keeping_progress(settings):
    for i in (70, 71, 72):
        store(settings, photo_msg(i))
    fake = FakeLLM(replies={71: LLMAuthError("401")})

    with pytest.raises(LLMAuthError):
        transcribe.transcribe_pending(settings, fake)

    assert [c["msg_id"] for c in fake.calls] == [70, 71]
    assert (settings.transcripts_dir / "70.json").exists()
    assert not (settings.transcripts_dir / "72.json").exists()


def test_text_post_uses_text_model(settings):
    store(settings, text_msg(80, VOCAB))
    store(settings, photo_msg(81))
    store(settings, text_msg(82, "Хорошего дня!"))
    fake = FakeLLM()

    report = transcribe.transcribe_pending(settings, fake)

    text_call, photo_call = fake.calls
    assert text_call["model"] == "text/model"
    assert text_call["user_parts"] == [{"type": "text", "text": VOCAB}]
    assert photo_call["model"] == "vision/model"
    assert text_call["system"] != photo_call["system"]
    assert read_transcript(settings, 80)["source"] == "text"
    assert read_transcript(settings, 80)["model"] == "text/model"
    assert report.done == [80, 81]


def test_model_output_is_normalised(settings):
    store(settings, photo_msg(90))
    messy = {
        "kind": "poem", "confidence": "1.7", "msg_id": 999, "source": "x", "model": "liar",
        "greek_words": [
            {"lemma": "γράφω", "pos": "verb", "surface": ["γράφεις", "γράφω", "γράφεις"]},
            {"surface": ["χωρίς"]},
            {"lemma": "σπίτι"},
            {"lemma": "δουλ-ειά", "pos": "noun", "surface": ["δουλ-ειές"]},
            "junk",
        ],
    }
    transcribe.transcribe_pending(settings, FakeLLM(default=messy))

    t = read_transcript(settings, 90)
    assert t["kind"] == "other"
    assert t["confidence"] == 1.0
    assert (t["msg_id"], t["source"], t["model"]) == (90, "photo", "vision/model")
    assert t["title"] == "" and t["body_md"] == "" and t["topic_hint"] == ""
    assert t["greek_words"] == [
        {"lemma": "γράφω", "pos": "verb", "surface": ["γράφεις", "γράφω"]},
        {"lemma": "σπίτι", "pos": "other", "surface": ["σπίτι"]},
        {"lemma": "δουλειά", "pos": "noun", "surface": ["δουλειές"]},
    ]


def test_load_transcripts(settings):
    store(settings, photo_msg(100))
    store(settings, photo_msg(101))
    transcribe.transcribe_pending(settings, FakeLLM())

    loaded = transcribe.load_transcripts(settings.transcripts_dir)

    assert sorted(loaded) == [100, 101]
    assert loaded[100]["kind"] == "table"
    assert transcribe.load_transcripts(settings.root / "missing") == {}


@pytest.mark.parametrize("value, expected", [("0.8", 0.8), (-3, 0.0), (None, 0.0), ("high", 0.0), (float("nan"), 0.0)])
def test_confidence_is_coerced_and_clamped(settings, value, expected):
    store(settings, photo_msg(110))
    transcribe.transcribe_pending(settings, FakeLLM(default=reply(confidence=value)))

    assert read_transcript(settings, 110)["confidence"] == expected


def test_text_post_drops_greek_words_absent_from_source(settings):
    store(settings, text_msg(120, "Повторить глаголы: γράφω, διαβαζω. Наречия времени тоже повторить."))
    invented = reply(
        kind="exercise", body_md="Повторить глаголы: γράφω, διαβαζω. Наречия времени тоже повторить.",
        confidence=1.0,
        greek_words=[
            {"lemma": "γράφω", "pos": "verb", "surface": ["γράφω", "γράφεις"]},
            {"lemma": "διαβάζω", "pos": "verb", "surface": ["διαβάζω"]},
            {"lemma": "τώρα", "pos": "adv", "surface": ["τώρα"]},
        ],
    )
    report = transcribe.transcribe_pending(settings, FakeLLM(default=invented))

    t = read_transcript(settings, 120)
    assert t["greek_words"] == [
        {"lemma": "γράφω", "pos": "verb", "surface": ["γράφω"]},
        {"lemma": "διαβάζω", "pos": "verb", "surface": ["διαβάζω"]},
    ]
    assert t["confidence"] == 1.0
    assert report.low_confidence == []


def test_text_post_with_invented_greek_body_is_flagged_low_confidence(settings):
    store(settings, text_msg(121, "Глагол ειμαι знать спряжение, личные местоимения я, ты, он"))
    invented = reply(kind="table", body_md="| я | είμαι |\n| ты | είσαι |", confidence=1.0, greek_words=[])
    report = transcribe.transcribe_pending(settings, FakeLLM(default=invented))

    assert read_transcript(settings, 121)["confidence"] == 0.5
    assert report.low_confidence == [121]
