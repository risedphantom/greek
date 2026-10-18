import json
import shutil
from pathlib import Path

from greekpipe.build import build_site, write_site
from greekpipe.config import load_settings, normalize

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "messages_sample.json"
ENV = """\
TG_API_ID=12345
TG_API_HASH=abc
TG_PHONE=+10000000000
TG_CHAT_ID=-100123
TG_TEACHER_ID=42
OPENROUTER_API_KEY=sk-test
"""
ALBUM = 14321865122108194
ALBUM_IDS = list(range(123358, 123364))


def transcript(msg_id, kind="grammar", hint="", title="", body="", confidence=0.9):
    return {"msg_id": msg_id, "source": "photo", "kind": kind, "title": title, "body_md": body,
            "greek_words": [], "topic_hint": hint, "confidence": confidence, "model": "m"}


def entry(lemma, pos="noun", forms=None, surfaces=(), msg_ids=(), proper_name=False,
          needs_review=False):
    return {"lemma": lemma, "translation": ["x"], "pos": pos, "gender": None, "class": None,
            "forms": forms or {}, "example": {"el": "", "ru": ""}, "lemma_key": normalize(lemma),
            "surfaces": list(surfaces), "msg_ids": list(msg_ids), "model": "m",
            "proper_name": proper_name, "needs_review": needs_review}


def make_settings(tmp_path, transcripts=(), lexicon=(), messages=None):
    (tmp_path / ".env").write_text(ENV)
    messages_dir = tmp_path / "state" / "messages"
    messages_dir.mkdir(parents=True)
    if messages is None:
        messages = json.loads(FIXTURE.read_text("utf-8"))
    for m in messages:
        (messages_dir / f"{m['id']}.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    content = tmp_path / "content"
    (content / "transcripts").mkdir(parents=True)
    (content / "lexicon").mkdir()
    shutil.copy(ROOT / "content" / "topics.json", content / "topics.json")
    for t in transcripts:
        (content / "transcripts" / f"{t['msg_id']}.json").write_text(
            json.dumps(t, ensure_ascii=False), "utf-8")
    for e in lexicon:
        (content / "lexicon" / f"{e['lemma_key']}.json").write_text(
            json.dumps(e, ensure_ascii=False), "utf-8")
    return load_settings(tmp_path)


def topic(site, topic_id):
    return next(t for g in site["grammar"]["groups"] for t in g["topics"] if t["id"] == topic_id)


def homework(site, hw_id):
    return next(h for h in site["homework"] if h["id"] == hw_id)


def test_album_becomes_one_occurrence(tmp_path):
    transcripts = [transcript(i, hint="Склонение м.р. -ης", body=f"part {i}",
                              title="" if i == ALBUM_IDS[0] else "Склонение",
                              confidence=0.5 if i == ALBUM_IDS[2] else 0.9)
                   for i in ALBUM_IDS]
    transcripts.append(transcript(123364, kind="exercise", hint="Склонение м.р. -ης"))
    site = build_site(make_settings(tmp_path, transcripts))

    occurrences = topic(site, "noun-masc-is")["occurrences"]
    assert len(occurrences) == 1
    occ = occurrences[0]
    assert occ["id"] == str(ALBUM)
    assert len(occ["media"]) == 6
    assert occ["msg_ids"] == ALBUM_IDS
    assert occ["title"] == "Склонение"
    assert occ["body_md"] == "\n\n---\n\n".join(f"part {i}" for i in ALBUM_IDS)
    assert occ["confidence"] == 0.5
    assert occ["date"] == "2026-09-24"
    assert topic(site, "noun-masc-is")["first_date"] == "2026-09-24"
    assert site["stats"]["occurrences"] == 1
    assert site["stats"]["low_confidence"] == 1


def test_task_refs_and_topic_ids_cross_link(tmp_path):
    transcripts = [transcript(123088, kind="exercise", hint="Предлог σε")]
    site = build_site(make_settings(tmp_path, transcripts))

    task = homework(site, "hw-2026-09-18")["tasks"][0]
    assert task["text"].startswith("1) повторить предлог σε")
    assert "prep-se" in task["topic_ids"]
    assert task["done_key"] == "hw-2026-09-18:0"
    assert task["media"] == ["media/123088.jpg", "media/123089.jpg"]
    assert task["transcripts"] == [{"title": "", "body_md": "", "kind": "exercise",
                                    "confidence": 0.9}]
    assert {"hw_id": "hw-2026-09-18", "task_idx": 0} in topic(site, "prep-se")["task_refs"]
    assert topic(site, "prep-se")["occurrences"] == []  # exercises never become occurrences


def test_forms_index_handles_case_accent_final_sigma(tmp_path):
    lexicon = [
        entry("τρώω", pos="verb", msg_ids=[123088],
              forms={"present": ["τρώω", "τρως", "τρώει", "τρώμε", "τρώτε", "τρώνε"],
                     "future": ["θα φάω", "θα φας", "θα φάει", "θα φάμε", "θα φάτε", "θα φάνε"]}),
        entry("φοιτητής", surfaces=["φοιτητές"], msg_ids=[123089]),
        entry("καλημέρα", pos="interj", msg_ids=[123089]),
        entry("δουλειά", msg_ids=[123089],
              forms={"sg": {"nom": "η δουλειά", "gen": "της δουλειάς", "acc": "τη δουλειά"},
                     "pl": {"nom": "οι δουλειές", "gen": "των δουλειών", "acc": "τις δουλειές"}}),
    ]
    index = build_site(make_settings(tmp_path, lexicon=lexicon))["forms_index"]

    assert index[normalize("Τρώει")] == "τρώω"
    assert index[normalize("φάω")] == "τρώω"
    assert index[normalize("φοιτητής")] == "φοιτητήσ"
    assert index[normalize("ΦΟΙΤΗΤΈΣ")] == "φοιτητήσ"
    assert index["δουλειά"] == "δουλειά"
    assert index[normalize("δουλειάς")] == "δουλειά"
    assert not {"η", "της", "τη", "οι", "θα", "των", "τισ"} & index.keys()
    assert index[normalize("ΚΑΛΗΜΕΡΑ")] == "καλημέρα"
    assert index[normalize("Καλημερα")] == "καλημέρα"


def test_forms_index_only_greek_keys(tmp_path):
    lexicon = [entry("γράφω", pos="verb", surfaces=["хочу"], msg_ids=[123088],
                     forms={"imperfect": ["έγραφα", "γράφdαμε"]}),
               entry("γεια σου", pos="phrase", msg_ids=[123088])]
    index = build_site(make_settings(tmp_path, lexicon=lexicon))["forms_index"]

    assert "хочу" not in index
    assert normalize("γράφdαμε") not in index
    assert index[normalize("έγραφα")] == "γράφω"
    assert index["γεια σου"] == "γεια σου"


def test_forms_index_collision_first_lemma_wins(tmp_path):
    lexicon = [entry("καλά", pos="adv", msg_ids=[123089]),
               entry("καλός", pos="adj", msg_ids=[122819],
                     forms={"n": {"sg": "καλό", "pl": "καλά"}})]
    site = build_site(make_settings(tmp_path, lexicon=lexicon))

    assert site["forms_index"]["καλά"] == "καλόσ"
    assert site["stats"]["form_collisions"] == 1


def test_homework_newest_first_and_due_flag(tmp_path):
    site = build_site(make_settings(tmp_path))

    ids = [h["id"] for h in site["homework"]]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == len(set(ids))
    latest = site["homework"][0]
    assert latest["id"] == "hw-2026-09-24"
    assert (latest["assigned"], latest["due"], latest["due_flagged"]) == (
        "2026-09-24", "2026-09-26", True)
    hw_18 = homework(site, "hw-2026-09-18")
    assert (hw_18["due"], hw_18["due_flagged"]) == ("2026-09-21", False)
    assert [t["idx"] for t in hw_18["tasks"]] == [0, 1, 2, 3]


def test_dictionary_rows_sources_and_order(tmp_path):
    transcripts = [transcript(123088, kind="exercise"), transcript(122819, hint="Предлог σε")]
    lexicon = [entry("σπίτι", msg_ids=[123088, 122819], needs_review=True),
               entry("άγαλμα", msg_ids=[123089])]
    site = build_site(make_settings(tmp_path, transcripts, lexicon))

    rows = site["dictionary"]
    assert [r["lemma"] for r in rows] == ["σπίτι", "άγαλμα"]
    row = rows[0]
    assert row["needs_review"] is True
    assert row["first_date"] == "2026-09-14"
    assert row["sources"] == [{"kind": "topic", "id": "prep-se", "date": "2026-09-14"},
                              {"kind": "hw", "id": "hw-2026-09-18", "date": "2026-09-18"}]
    assert "proper_name" not in row
    assert site["stats"]["words"] == 2


def test_verb_class_derived_from_ending(tmp_path):
    lexicon = [{**entry(lemma, pos="verb", msg_ids=[123088]), "class": model_class}
               for lemma, model_class in [("θέλω", "2 спр."), ("φιλάω", "1 спр."),
                                          ("αγαπώ", None), ("έρχομαι", "1 спр.")]]
    lexicon.append({**entry("σπίτι", msg_ids=[123088]), "class": "ουδ."})
    site = build_site(make_settings(tmp_path, lexicon=lexicon))

    assert {r["lemma"]: r["class"] for r in site["dictionary"]} == {
        "θέλω": "1 спр.", "φιλάω": "2 спр.", "αγαπώ": "2 спр.", "έρχομαι": "-μαι", "σπίτι": "ουδ."}


def test_invalid_entry_on_disk_badged_needs_review(tmp_path):
    valid = {**entry("νερό", pos="adv", msg_ids=[123088]), "example": {"el": "Νερό.", "ru": "Вода."}}
    broken = {**valid, "lemma": "μέσα", "lemma_key": "μέσα", "translation": []}
    site = build_site(make_settings(tmp_path, lexicon=[valid, broken]))

    assert {r["lemma"]: r["needs_review"] for r in site["dictionary"]} == {"νερό": False, "μέσα": True}


def test_proper_names_excluded(tmp_path):
    lexicon = [entry("Αθήνα", surfaces=["Αθήνα"], msg_ids=[123088], proper_name=True),
               entry("σπίτι", msg_ids=[123088])]
    site = build_site(make_settings(tmp_path, lexicon=lexicon))

    assert [r["lemma"] for r in site["dictionary"]] == ["σπίτι"]
    assert normalize("Αθήνα") not in site["forms_index"]


def test_audio_caption_fallback(tmp_path):
    def msg(msg_id, minute, text="", kind=None):
        return {"id": msg_id, "date": f"2026-09-23T14:{minute:02d}:00+00:00", "text": text,
                "media": f"media/{msg_id}.mp3" if kind else None, "media_kind": kind,
                "reply_to": None, "grouped_id": None}

    messages = [msg(1, 0, "Слушаем диалог", "audio"), msg(2, 10, "Трек к уроку 5"),
                msg(3, 11, kind="audio"), msg(4, 30, "Старый текст"), msg(5, 35, kind="audio")]
    site = build_site(make_settings(tmp_path, messages=messages))

    assert site["audio"] == [
        {"id": "1", "date": "2026-09-23", "file": "media/1.mp3", "caption": "Слушаем диалог"},
        {"id": "3", "date": "2026-09-23", "file": "media/3.mp3", "caption": "Трек к уроку 5"},
        {"id": "5", "date": "2026-09-23", "file": "media/5.mp3", "caption": "Трек 3"},
    ]


def test_audio_caption_skips_homework_marker(tmp_path):
    messages = [
        {"id": 1, "date": "2026-09-23T14:00:00+00:00", "text": "Доброе утро! \nД/з на след. занятие:",
         "media": None, "media_kind": None, "reply_to": None, "grouped_id": None},
        {"id": 2, "date": "2026-09-23T14:01:00+00:00", "text": "", "media": "media/2.mp3",
         "media_kind": "audio", "reply_to": None, "grouped_id": None},
        {"id": 3, "date": "2026-09-23T14:02:00+00:00", "text": " Диалог \n в кафе ",
         "media": "media/3.mp3", "media_kind": "audio", "reply_to": None, "grouped_id": None},
    ]
    site = build_site(make_settings(tmp_path, messages=messages))

    assert [a["caption"] for a in site["audio"]] == ["Трек 1", "Диалог в кафе"]


def test_new_group_only_lists_used_topics(tmp_path):
    transcripts = [transcript(122819, kind="vocab", hint="Совсем неизвестная тема")]
    site = build_site(make_settings(tmp_path, transcripts))

    groups = {g["id"]: g for g in site["grammar"]["groups"]}
    assert [t["title"] for t in groups["new"]["topics"]] == ["Совсем неизвестная тема"]
    assert len(groups["prep"]["topics"]) > 0  # empty taxonomy topics are still emitted
    assert [g["id"] for g in site["grammar"]["groups"]][-1] == "new"


def test_write_site_is_valid_json_and_has_stats(tmp_path):
    settings = make_settings(tmp_path)
    path = write_site(settings)

    assert path == settings.content_dir / "site.json"
    text = path.read_text("utf-8")
    assert text.endswith("}\n")
    site = json.loads(text)
    assert set(site["stats"]) == {"topics", "occurrences", "homework", "tasks", "words",
                                  "form_collisions", "low_confidence"}
    assert site["stats"]["homework"] == len(site["homework"]) > 0
    assert site["updated"] == "2026-09-24"
    assert sorted(p.name for p in settings.content_dir.iterdir()) == [
        "lexicon", "site.json", "topics.json", "transcripts"]

