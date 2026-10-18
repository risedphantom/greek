# greek

Live site: **https://risedphantom.github.io/greek/**

Pipeline (`greekpipe`) that turns a Telegram Greek-course group's messages
into a static site (GitHub Pages serves the `main` branch root; `.nojekyll`).
Left panel: grammar by topic (searchable). Right panel: homework, newest
first. «Словарь» tab: every word that appeared, with hover cards showing
translation and forms.

Run the full update (fetch → transcribe → lexicon → build → summary):

    uv run scripts/update.py            # fetch new messages and rebuild
    uv run scripts/update.py --no-fetch # rebuild only
    uv run scripts/update.py --no-fetch --push  # commit + push content

In Claude Code: `/update-greek` wraps the same flow with a review step.

Tests: `uv run pytest -q`

`.env` keys (gitignored): `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`, `TG_2FA_PASSWORD`,
`TG_CHAT_ID`, `TG_TEACHER_ID`, `OPENROUTER_API_KEY`; optional `OR_VISION_MODEL`
(default `google/gemini-2.5-flash`), `OR_TEXT_MODEL` (default `google/gemini-2.5-flash-lite`).
