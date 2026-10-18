# greek

Pipeline (`greekpipe`) that turns a Telegram Greek-course group's messages
into a static site (GitHub Pages, `.nojekyll`).

Run the full update:

    uv run scripts/update.py

Tests: `uv run pytest -q`

`.env` keys (gitignored): `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`, `TG_2FA_PASSWORD`,
`TG_CHAT_ID`, `TG_TEACHER_ID`, `OPENROUTER_API_KEY`; optional `OR_VISION_MODEL`
(default `google/gemini-2.5-flash`), `OR_TEXT_MODEL` (default `google/gemini-2.5-flash-lite`).
