import shutil
from dataclasses import replace
from pathlib import Path

from greekpipe.config import load_settings
from greekpipe.update_runner import run


def test_dry_run_rebuilds_without_llm_or_fetch(tmp_path):
    real = load_settings()
    for name in ("state", "content"):
        shutil.copytree(real.root / name, tmp_path / name)
    settings = replace(
        real,
        root=tmp_path,
        state_dir=tmp_path / "state",
        content_dir=tmp_path / "content",
        transcripts_dir=tmp_path / "content" / "transcripts",
        lexicon_dir=tmp_path / "content" / "lexicon",
    )
    before = (real.content_dir / "site.json").read_bytes()
    result = run(settings, fetch=True, limit=None, dry_run=True)
    assert result.fetched == [] and result.cost == 0
    assert "Новые разборы 0" in result.summary and "Новые слова 0" in result.summary
    assert (tmp_path / "content" / "site.json").read_bytes() == before
