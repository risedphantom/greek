from greekpipe.lexicon import LexiconReport
from greekpipe.summary import render
from greekpipe.transcribe import TranscribeReport
from greekpipe.update_runner import needs_commit

STATS = {"topics": 5, "occurrences": 40, "homework": 7, "tasks": 12, "words": 300}


def test_render_summary_lists_low_confidence_and_unmapped():
    text = render(
        [1, 2, 3],
        TranscribeReport(done=[123, 124], pending=[125], low_confidence=[123]),
        LexiconReport(done=["λόγος"], pending=["a", "b"], needs_review=["λόγος"]),
        ["Новая тема"],
        STATS,
        0.0123,
        today="2026-10-18",
    )
    assert text.splitlines()[0] == "Обновление сайта — 2026-10-18"
    assert "Новые сообщения 3" in text
    assert "Новые разборы 2" in text
    assert "media/123.jpg" in text
    assert "Ожидают повтора 3" in text
    assert "«Новое»" in text and "Новая тема" in text
    assert "Новые слова 1" in text and "нужна проверка" in text and "λόγος" in text
    assert "Итого стоимость $0.0123" in text
    assert "Сайт: тем 5" in text


def test_render_text_post_and_empty():
    text = render(
        [], TranscribeReport(low_confidence=[9]), LexiconReport(), [], STATS, 0, text_ids={9},
        today="2026-10-18",
    )
    assert "#9" in text and "media/9.jpg" not in text
    assert "Новые сообщения 0" in text and "Новые слова 0" in text
    assert "«Новое»" not in text
    assert "Итого стоимость $0.0000" in text


def test_needs_commit():
    assert not needs_commit("")
    assert not needs_commit("\n  \n")
    assert needs_commit(" M content/site.json\n")
