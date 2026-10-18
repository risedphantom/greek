import asyncio
from dataclasses import dataclass

from greekpipe.build import build_site, write_site
from greekpipe.lexicon import LexiconReport, collect_lemmas, generate_pending
from greekpipe.llm import LLMClient
from greekpipe.summary import render
from greekpipe.tg_fetch import fetch_new
from greekpipe.topics import load_topics
from greekpipe.transcribe import TranscribeReport, load_transcripts, transcribe_pending

HINT_KINDS = {"grammar", "table", "vocab", "text"}
HINT_CONFIDENCE = 0.7


class FetchError(Exception):
    pass


@dataclass
class RunResult:
    summary: str
    fetched: list[int]
    transcribe: TranscribeReport
    lexicon: LexiconReport
    cost: float


def needs_commit(porcelain_output: str) -> bool:
    return bool(porcelain_output.strip())


def _unmapped_hints(settings, transcripts: dict[int, dict]) -> list[str]:
    taxonomy = load_topics(settings.content_dir / "topics.json")
    return taxonomy.unmapped(
        t["topic_hint"]
        for t in transcripts.values()
        if t.get("kind") in HINT_KINDS and t.get("confidence", 0) >= HINT_CONFIDENCE and t.get("topic_hint")
    )


def run(settings, *, fetch: bool, limit: int | None, dry_run: bool, llm_client=None) -> RunResult:
    fetched: list[int] = []
    if fetch and not dry_run:
        try:
            fetched = asyncio.run(fetch_new(settings))
        except Exception as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
    transcribe, lexicon = TranscribeReport(), LexiconReport()
    cost = 0.0
    if not dry_run:
        client = llm_client or LLMClient(settings.openrouter_api_key)
        transcribe = transcribe_pending(settings, client, limit=limit)
        transcripts = load_transcripts(settings.transcripts_dir)
        lexicon = generate_pending(settings, client, collect_lemmas(transcripts), limit=limit)
        cost = client.last_cost
    transcripts = load_transcripts(settings.transcripts_dir)
    write_site(settings)
    stats = build_site(settings)["stats"]
    text_ids = {i for i, t in transcripts.items() if t.get("source") == "text"}
    summary = render(
        fetched, transcribe, lexicon, _unmapped_hints(settings, transcripts), stats, cost,
        text_ids=text_ids,
    )
    return RunResult(summary, fetched, transcribe, lexicon, cost)
