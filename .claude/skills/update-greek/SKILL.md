---
name: update-greek
description: Pull new messages from the Greek teacher's Telegram group, transcribe and publish the site. Use when the user says "update greek", "обнови сайт", "new homework", or twice a week after lessons.
---

# update-greek

Updates https://risedphantom.github.io/greek/ from the Telegram group.
Runtime AI is OpenRouter only (`OPENROUTER_API_KEY` in `.env`); this skill
never transcribes anything itself — it runs the pipeline and helps the
human check what the pipeline flagged. Budget: 5–10 minutes.

## Steps

1. Run the pipeline and show the summary verbatim:

       uv run scripts/update.py

   Exit code 2 = Telegram fetch failed (session/network) — stop and report.
   Exit code 3 = OpenRouter auth/credits — stop and report.

2. For each item under «низкая уверенность» in the summary: open the photo
   (`media/<id>.jpg`, Read tool) next to `content/transcripts/<id>.json`.
   If `body_md`, `kind` or `topic_hint` is wrong, edit the JSON by hand
   (keep Greek verbatim). After verifying or fixing a transcript, set its
   `confidence` to `1.0` so it leaves the review list and enters the
   dictionary. Delete the file instead to make the pipeline regenerate it
   on the next run. Items shown as `#<id>` are text posts, not photos:
   open `state/messages/<id>.json` instead of `media/<id>.jpg`.

3. For each hint under «Новые темы в „Новое“»: add it to the `hints` list of
   the right topic in `content/topics.json` (whole-word matching, lowercase
   not required), or add a new topic to the right group. Keep «Новое» empty.

4. For each lemma under «нужна проверка»: open `content/lexicon/<lemma>.json`.
   Fix forms by hand and set `needs_review` to `false`, or delete the file
   to regenerate.

5. Rebuild without fetching and re-read the summary:

       uv run scripts/update.py --no-fetch

6. Publish:

       uv run scripts/update.py --no-fetch --push

7. Report: the live URL, what changed (new topics, new homework/tasks, new
   words), anything left flagged for later.

## Notes

- Everything is incremental and idempotent: existing transcripts and lexicon
  entries are never regenerated unless their file is deleted.
- `--limit N` caps LLM work per run (N photos / N lexicon batches).
- `--dry-run` rebuilds the site without fetching or calling OpenRouter.
- Never commit `.env` or `tg.session*` (gitignored).
