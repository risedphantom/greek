from datetime import date

from greekpipe.lexicon import LexiconReport
from greekpipe.transcribe import TranscribeReport


def render(
    fetch_ids: list[int],
    transcribe_report: TranscribeReport,
    lexicon_report: LexiconReport,
    taxonomy_unmapped: list[str],
    site_stats: dict,
    cost: float,
    *,
    text_ids: set[int] = frozenset(),
    today: str | None = None,
) -> str:
    lines = [f"Обновление сайта — {today or date.today().isoformat()}", ""]
    lines.append(f"Новые сообщения {len(fetch_ids)}")
    lines.append(f"Новые разборы {len(transcribe_report.done)}")
    if transcribe_report.low_confidence:
        lines.append("  низкая уверенность:")
        for i in transcribe_report.low_confidence:
            lines.append(f"  - {'#' + str(i) if i in text_ids else f'media/{i}.jpg'}")
    pending = len(transcribe_report.pending) + len(lexicon_report.pending)
    lines.append(f"Ожидают повтора {pending}")
    if taxonomy_unmapped:
        lines.append("Новые темы в «Новое»:")
        lines.extend(f"  - {hint}" for hint in taxonomy_unmapped)
    lines.append(f"Новые слова {len(lexicon_report.done)}")
    if lexicon_report.needs_review:
        lines.append("  нужна проверка:")
        lines.extend(f"  - {lemma}" for lemma in lexicon_report.needs_review)
    lines.append("")
    lines.append(
        "Сайт: тем {topics}, вхождений {occurrences}, домашек {homework}, "
        "заданий {tasks}, слов {words}".format(**site_stats)
    )
    lines.append(f"Итого стоимость ${cost:.4f}")
    return "\n".join(lines)
