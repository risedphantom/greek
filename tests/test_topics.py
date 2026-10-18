from pathlib import Path

import pytest

from greekpipe.topics import load_topics
from greekpipe.transcribe import load_transcripts

ROOT = Path(__file__).resolve().parent.parent
TOPICS = ROOT / "content" / "topics.json"
TRANSCRIPTS = ROOT / "content" / "transcripts"
OCCURRENCE_KINDS = {"grammar", "table", "vocab", "text"}


@pytest.fixture(scope="module")
def taxonomy():
    return load_topics(TOPICS)


def test_groups_in_fixed_order(taxonomy):
    assert [g["id"] for g in taxonomy.groups] == [
        "verb", "noun", "article", "pronoun", "adj", "prep",
        "adverb", "lexis", "texts", "audio", "new",
    ]
    assert taxonomy.topic("prep-se")["title"] == "Предлог σε"
    assert taxonomy.topic("missing") is None
    ids = taxonomy.all_topic_ids()
    assert len(ids) == len(set(ids))
    assert ids.index("conj-1") < ids.index("prep-se")


def test_resolve_exact_and_substring(taxonomy):
    assert taxonomy.resolve("Предлог σε") == ("prep", "prep-se")
    assert taxonomy.resolve("Предлог σε и его значения") == ("prep", "prep-se")
    assert taxonomy.resolve("ГЛАГОЛ ΕΊΜΑΙ") == ("verb", "verb-eimai")
    assert taxonomy.resolve("Склонение м.р. -ης") == ("noun", "noun-masc-is")


def test_resolve_texts_and_lexis_prefixes(taxonomy):
    assert taxonomy.resolve("Текст: Στο μάθημα ελληνικών") == ("texts", "readings")
    assert taxonomy.resolve("Κείμενο 3: Η οικογένεια") == ("texts", "readings")
    assert taxonomy.resolve("Лексика: дни недели") == ("lexis", "lexis-days")
    assert taxonomy.resolve("Лексика: что-то совсем другое") == ("lexis", "lexis-misc")


def test_resolve_unmapped_goes_to_new(taxonomy):
    assert taxonomy.resolve("Аорист: сильные глаголы!") == ("new", "аорист-сильные-глаголы")
    assert taxonomy.unmapped(["Предлог σε", "Аорист", "Аорист"]) == ["Аорист"]


def test_short_hint_does_not_substring_match(taxonomy):
    assert taxonomy.resolve("сп")[0] == "new"


def test_substring_matches_whole_words_only(taxonomy):
    assert taxonomy.resolve("Прошедшее время глаголов")[0] == "new"
    assert taxonomy.resolve("Модальные глаголы")[0] == "new"
    assert taxonomy.resolve("Сообщение другу")[0] == "new"


def test_mediopassive_verbs_resolve_to_verb_mai(taxonomy):
    assert taxonomy.resolve("Глаголы на -ομαι") == ("verb", "verb-mai")
    assert taxonomy.resolve("Глаголы на -μαι, упражнения") == ("verb", "verb-mai")


def test_topic_for_task_text(taxonomy):
    assert taxonomy.topic_for_task_text("Повторить спряжение глаголов 1 и 2") == ["conj-1", "conj-2"]
    assert taxonomy.topic_for_task_text("Упр. 5: предлог σε, вставьте пропуски") == ["prep-se"]
    assert taxonomy.topic_for_task_text("Выучить слова") == []
    assert taxonomy.topic_for_task_text("Упр. 3: единственного и множественного числа") == []


@pytest.mark.skipif(not TRANSCRIPTS.is_dir(), reason="no backfilled transcripts")
def test_every_backfilled_hint_is_mapped(taxonomy):
    hints = [
        t["topic_hint"]
        for t in load_transcripts(TRANSCRIPTS).values()
        if t["kind"] in OCCURRENCE_KINDS
    ]
    assert hints
    assert taxonomy.unmapped(hints) == []
