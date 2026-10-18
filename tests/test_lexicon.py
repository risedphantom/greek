import json

import pytest

from greekpipe import lexicon
from greekpipe.lexicon import LemmaRef
from greekpipe.llm import LLMAuthError, LLMBadOutput, LLMTransient
from tests.test_transcribe import make_settings

PERSONS = ["a", "b", "c", "d", "e", "f"]


def verb_forms(n=6):
    return {
        "present": PERSONS[:n], "imperfect": PERSONS, "aorist": PERSONS, "future": PERSONS,
        "imperative": {"sg": "x", "pl": "y"},
    }


def entry_for(item):
    forms = verb_forms() if item["pos"] == "verb" else {}
    return {
        "lemma": item["lemma"], "translation": ["перевод"], "pos": item["pos"],
        "gender": None, "class": None, "forms": forms,
        "example": {"el": "Παράδειγμα.", "ru": "Пример."},
    }


class FakeLLM:
    """Answers each batch with one valid entry per lemma; `overrides` patches or drops entries."""

    def __init__(self, overrides=None, error=None):
        self.overrides = overrides or {}
        self.error = error
        self.calls = []
        self.last_cost = 0.0

    def chat_json(self, model, system, user_parts, schema_name):
        items = json.loads(user_parts[0]["text"])
        self.calls.append({"model": model, "system": system, "items": items})
        self.last_cost += 0.001
        if self.error:
            raise self.error
        entries = []
        for item in items:
            override = self.overrides.get(item["lemma"], {})
            if override is None:
                continue
            entries.append({**entry_for(item), **override})
        return {"entries": entries}


def ref(lemma, pos="adv", surfaces=(), msg_ids=(1,)):
    return LemmaRef(lemma, pos, set(surfaces) or {lemma}, set(msg_ids))


def refs_of(*lemmas, pos="adv"):
    return {lexicon.normalize(lm): ref(lm, pos) for lm in lemmas}


def transcript(msg_id, words, kind="vocab", confidence=0.95):
    return {"msg_id": msg_id, "kind": kind, "confidence": confidence, "greek_words": words}


def word(lemma, pos="verb", *surface):
    return {"lemma": lemma, "pos": pos, "surface": list(surface)}


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def test_collect_lemmas_drops_stopwords_and_merges_surfaces():
    transcripts = {
        1: transcript(1, [word("τρώω", "verb", "τρώει"), word("και", "other", "και")]),
        2: transcript(2, [word("Τρώω", "verb", "Τρώω")]),
    }

    refs = lexicon.collect_lemmas(transcripts)

    assert list(refs) == ["τρώω"]
    assert refs["τρώω"] == LemmaRef("τρώω", "verb", {"τρώει", "Τρώω"}, {1, 2})


def test_collect_drops_short_and_non_greek_tokens_keeps_first_pos():
    transcripts = {
        1: transcript(1, [word("ά", "other"), word("hello", "other"), word("привет", "other"),
                          word("каτερίνα", "other"), word("Rodosσ", "other"),
                          word("καλός", "adj", "καλή")]),
        2: transcript(2, [word("καλός", "adv", "καλά")]),
    }

    refs = lexicon.collect_lemmas(transcripts)

    assert list(refs) == ["καλόσ"]
    assert refs["καλόσ"].pos == "adj"
    assert refs["καλόσ"].surfaces == {"καλή", "καλά"}


def test_collect_skips_low_confidence_and_chat():
    transcripts = {
        1: transcript(1, [word("γράφω")], kind="chat"),
        2: transcript(2, [word("διαβάζω")], kind="other"),
        3: transcript(3, [word("τρέχω")], confidence=0.6),
        4: transcript(4, [word("πίνω")], kind="exercise", confidence=0.7),
    }

    assert list(lexicon.collect_lemmas(transcripts)) == ["πίνω"]


def test_generate_writes_entry_file(settings):
    refs = {"τρώω": LemmaRef("τρώω", "verb", {"τρώει", "Τρώω"}, {5, 2})}
    fake = FakeLLM()

    report = lexicon.generate_pending(settings, fake, refs)

    assert report.done == ["τρώω"] and report.pending == [] and report.needs_review == []
    assert report.cost == pytest.approx(0.001)
    assert fake.calls[0]["model"] == "text/model"
    assert fake.calls[0]["items"] == [{"lemma": "τρώω", "pos": "verb", "examples": ["Τρώω", "τρώει"]}]
    raw = (settings.lexicon_dir / "τρώω.json").read_text("utf-8")
    assert raw.endswith("}\n")
    entry = json.loads(raw)
    assert entry == {
        **entry_for({"lemma": "τρώω", "pos": "verb"}),
        "lemma_key": "τρώω", "surfaces": ["Τρώω", "τρώει"], "msg_ids": [2, 5],
        "needs_review": False, "proper_name": False, "model": "text/model",
    }
    assert lexicon.load_lexicon(settings.lexicon_dir) == {"τρώω": entry}


def test_generate_batches_of_20(settings):
    refs = refs_of(*(f"λέξη{chr(0x3b1 + i % 24)}{i:02d}" for i in range(45)))
    fake = FakeLLM()

    report = lexicon.generate_pending(settings, fake, refs)

    assert [len(c["items"]) for c in fake.calls] == [20, 20, 5]
    assert len(report.done) == 45
    assert report.cost == pytest.approx(0.003)


def test_generate_honours_batch_limit(settings):
    refs = refs_of(*(f"λέξη{chr(0x3b1 + i)}" for i in range(5)))
    fake = FakeLLM()

    report = lexicon.generate_pending(settings, fake, refs, batch_size=2, limit=1)

    assert len(fake.calls) == 1
    assert report.done == ["λέξηα", "λέξηβ"]
    assert report.pending == []


def test_generate_skips_cached(settings):
    settings.lexicon_dir.mkdir(parents=True)
    (settings.lexicon_dir / "σπίτι.json").write_text("{}\n", "utf-8")
    fake = FakeLLM()

    report = lexicon.generate_pending(settings, fake, refs_of("σπίτι", "νερό"))

    assert [i["lemma"] for c in fake.calls for i in c["items"]] == ["νερό"]
    assert report.done == ["νερό"]
    assert (settings.lexicon_dir / "σπίτι.json").read_text("utf-8") == "{}\n"


def test_invalid_verb_marked_needs_review(settings):
    fake = FakeLLM(overrides={"γράφω": {"forms": verb_forms(5)}})

    report = lexicon.generate_pending(settings, fake, refs_of("γράφω", "τρέχω", pos="verb"))

    assert report.needs_review == ["γράφω"]
    assert report.done == ["γράφω", "τρέχω"]
    entry = json.loads((settings.lexicon_dir / "γράφω.json").read_text("utf-8"))
    assert entry["needs_review"] is True
    assert entry["forms"]["present"] == PERSONS[:5]


def test_transient_leaves_batch_pending(settings):
    fake = FakeLLM(error=LLMTransient("429"))

    report = lexicon.generate_pending(settings, fake, refs_of("νερό", "σπίτι"))

    assert report.pending == ["νερό", "σπίτι"] and report.done == []
    assert not list(settings.lexicon_dir.glob("*.json"))


def test_bad_output_leaves_batch_pending(settings):
    fake = FakeLLM(error=LLMBadOutput("not json"))

    report = lexicon.generate_pending(settings, fake, refs_of("νερό"))

    assert report.pending == ["νερό"]
    assert not list(settings.lexicon_dir.glob("*.json"))


def test_auth_error_aborts(settings):
    with pytest.raises(LLMAuthError):
        lexicon.generate_pending(settings, FakeLLM(error=LLMAuthError("401")), refs_of("νερό"))


def test_missing_lemma_in_reply_stays_pending(settings):
    fake = FakeLLM(overrides={"σπίτι": None})

    report = lexicon.generate_pending(settings, fake, refs_of("νερό", "σπίτι"))

    assert report.done == ["νερό"]
    assert report.pending == ["σπίτι"]
    assert not (settings.lexicon_dir / "σπίτι.json").exists()


def test_reply_entries_for_unknown_lemmas_are_ignored(settings):
    class Extra(FakeLLM):
        def chat_json(self, *args):
            out = super().chat_json(*args)
            out["entries"].append(entry_for({"lemma": "ψέμα", "pos": "noun"}))
            return out

    report = lexicon.generate_pending(settings, Extra(), refs_of("νερό"))

    assert report.done == ["νερό"]
    assert sorted(p.name for p in settings.lexicon_dir.glob("*.json")) == ["νερό.json"]


NOUN_FORMS = {"sg": {"nom": "η μέρα", "gen": "της μέρας", "acc": "τη μέρα"},
              "pl": {"nom": "οι μέρες", "gen": "των ημερών", "acc": "τις μέρες"}}
ADJ_FORMS = {"m": {"sg": "καλός", "pl": "καλοί"}, "f": {"sg": "καλή", "pl": "καλές"},
             "n": {"sg": "καλό", "pl": "καλά"}}


@pytest.mark.parametrize("pos, forms, ok", [
    ("verb", verb_forms(), True),
    ("verb", verb_forms(5), False),
    ("verb", {k: v for k, v in verb_forms().items() if k != "future"}, False),
    ("verb", {**verb_forms(), "imperative": {"sg": "x"}}, False),
    ("verb", {**verb_forms(), "aorist": ["a", "", "c", "d", "e", "f"]}, False),
    ("noun", NOUN_FORMS, True),
    ("noun", {**NOUN_FORMS, "pl": {"nom": "οι μέρες", "gen": "των ημερών"}}, False),
    ("adj", ADJ_FORMS, True),
    ("adj", {"m": ADJ_FORMS["m"], "f": ADJ_FORMS["f"]}, False),
    ("adv", {}, True),
    ("phrase", None, True),
    ("other", {"x": 1}, False),
])
def test_validate_forms_by_pos(pos, forms, ok):
    entry = {**entry_for({"lemma": "καλός", "pos": pos}), "forms": forms}
    assert lexicon.validate(entry) is ok


def test_validate_rejects_bad_core_fields():
    good = entry_for({"lemma": "νερό", "pos": "adv"})
    assert lexicon.validate(good)
    assert not lexicon.validate({**good, "translation": []})
    assert not lexicon.validate({**good, "translation": "вода"})
    assert not lexicon.validate({**good, "pos": "pronoun"})
    assert not lexicon.validate({**good, "pos": ["adv"]})
    assert not lexicon.validate({**good, "translation": ["вода", "влага", "жидкость", "сок"]})
    assert not lexicon.validate({k: v for k, v in good.items() if k != "example"})
    assert not lexicon.validate({**good, "gender": "x"})
    assert lexicon.validate({k: v for k, v in good.items() if k not in ("gender", "class")})


def test_validate_rejects_unaccented_polysyllable():
    good = {**entry_for({"lemma": "γιος", "pos": "noun"}), "forms": {
        "sg": {"nom": "ο γιος", "gen": "του γιου", "acc": "τον γιο"},
        "pl": {"nom": "οι γιοι", "gen": "των γιων", "acc": "τους γιους"}}}
    assert lexicon.validate(good)
    assert not lexicon.validate({**good, "forms": NOUN_FORMS | {"pl": {**NOUN_FORMS["pl"], "nom": "οι μερες"}}})
    verb = {**entry_for({"lemma": "δουλεύω", "pos": "verb"})}
    verb["forms"] = {**verb["forms"], "imperfect": ["δουλευα", "δούλευες", "δούλευε", "δουλεύαμε", "δουλεύατε", "δούλευαν"]}
    assert not lexicon.validate(verb)


def test_validate_rejects_adjective_with_article_or_foreign_paradigm():
    adj = {**entry_for({"lemma": "κλειστός", "pos": "adj"}), "lemma_key": "κλειστόσ"}
    ok = {"m": {"sg": "κλειστός", "pl": "κλειστοί"}, "f": {"sg": "κλειστή", "pl": "κλειστές"},
          "n": {"sg": "κλειστό", "pl": "κλειστά"}}
    assert lexicon.validate({**adj, "forms": ok})
    assert not lexicon.validate({**adj, "forms": ADJ_FORMS})
    assert not lexicon.validate({**adj, "forms": {**ok, "f": {"sg": "η κλειστή", "pl": "οι κλειστές"}}})


def test_proper_name_flag(settings):
    refs = {
        "τζον": ref("Τζον", "other"),
        "αμερικανόσ": ref("Αμερικανός", "noun"),
        "κυριακή": ref("Κυριακή", "noun"),
        "αθήνα": ref("αθήνα", "other"),
    }
    fake = FakeLLM(overrides={
        "Τζον": {"translation": ["Джон"]},
        "Αμερικανός": {"translation": ["американец"], "forms": NOUN_FORMS},
        "Κυριακή": {"translation": ["Воскресенье"], "forms": NOUN_FORMS},
        "αθήνα": {"translation": ["афины"]},
    })

    lexicon.generate_pending(settings, fake, refs)

    flags = {k: e["proper_name"] for k, e in lexicon.load_lexicon(settings.lexicon_dir).items()}
    assert flags == {"τζον": True, "αμερικανόσ": False, "κυριακή": False, "αθήνα": False}


def test_imperative_list_normalised(settings):
    forms = {**verb_forms(), "imperative": ["φάε", "φάτε"]}
    fake = FakeLLM(overrides={"τρώω": {"forms": forms}})

    report = lexicon.generate_pending(settings, fake, refs_of("τρώω", pos="verb"))

    entry = json.loads((settings.lexicon_dir / "τρώω.json").read_text("utf-8"))
    assert entry["forms"]["imperative"] == {"sg": "φάε", "pl": "φάτε"}
    assert entry["needs_review"] is False and report.needs_review == []
