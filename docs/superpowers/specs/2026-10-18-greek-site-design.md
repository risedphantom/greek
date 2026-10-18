# Greek course companion site — design

Date: 2026-10-18. Owner: Anton (Telegram id 409946218, GitHub `risedphantom`).

## Intent

Anton takes Greek lessons twice a week in a private Telegram group
(«Группа 5/ сентябрь», chat id `-CHAT_ID`, 3 members). The teacher
Наталья (Telegram user id `TEACHER_ID`) posts, mostly as photos of textbook
pages, the grammar covered in each lesson and the homework for the next
one. The material is scattered across hundreds of messages and impossible
to search or review. The site turns that stream into a structured,
searchable study companion: grammar on the left, homework on the right, a
dictionary of every word that has appeared, with inflection tables on hover.

Content must stay current with roughly 5 minutes of effort twice a week,
via a Claude Code skill that pulls new messages and rebuilds content.

## Goals

- All grammar explained in the group, grouped logically, full-text searchable.
- All homework, newest first, each task typed and linked to the grammar it
  exercises, with the original photo one click away.
- Dictionary of every Greek word that appeared, with Russian translation,
  part of speech, full forms, and where it appeared.
- Hover card on any Greek word anywhere on the site: translation first,
  then forms.
- One command (`/update-greek`) to ingest new messages and publish.

## Non-goals

- Not a general Greek learning app; only material from this group.
- No user accounts, no server, no database. Static site only.
- No interactive exercise checking (no answer validation). Done-checkboxes
  are the only interactivity beyond navigation.
- No real-time sync with Telegram; updates are manual, twice a week.

## Anti-goals (would make it a failure even if it "works")

- Hallucinated grammar or translations presented as the teacher's. Every
  transcribed item must link to its source message/photo so it can be
  checked. Low-confidence transcriptions are flagged, never silently kept.
- Re-generating existing content on each update (cost, churn, drift).
  Ingestion is incremental and idempotent.
- Secrets in the repo (`.env`, `tg.session`). The repo is public.

## Source material (observed 2026-08-30 → 2026-10-17, 289 messages)

| Kind | Count | Role |
|---|---|---|
| Photos (phone shots of textbook/workbook pages, grammar tables, exercise lists, reading texts) | 124 | Primary content. Albums (`grouped_id`) are multi-page spreads. |
| Text posts, often with link preview («webpage» media) | ~38 | Vocab lists (`греч – рус` lines), grammar notes in Russian, phrase sets. Full text is in the message. Many forwarded from channels `@greev` and a private channel. |
| mp3 | 16 | Alphabet / pronunciation tracks. |
| ogg voice notes | 2 | Chat, ignore. |
| PDF | 4 | Textbook «Επικοινωνήστε Ελληνικά 1a» and its workbook, scanned (OCR layer unusable). |
| Messages from other members | 39 | Scheduling chatter, questions. Ignore for content. |

Rhythm:

- Lesson evening: greeting + video-call link, then after the lesson a burst
  of forwarded photos = material covered (grammar, texts, tables).
- Next morning: «Добрый день! Д/з» (or «Домашнее задание на DD.MM»,
  «Высылаю задания», «Задания») + photos + one-line instructions
  («упр. 7,8 устно, 9 письменно», «Тетрадь с упражнениями с. 29, 30, 33»,
  «Прочитать и перевести текст») = homework.
- Teacher sometimes replies to a specific photo (`reply_to`) to attach an
  instruction to it.

## Decisions made with Anton

- **Access**: Telegram user session via Telethon (`api_id`/`api_hash`/phone
  from `.env`; `tg.session` in repo dir, gitignored). Already logged in.
- **Transcription**: OpenAI vision API (`OPENAI_API_KEY` in `.env`), not
  Tesseract (fails on Greek diacritics + tables + phone photos) and not
  in-session Claude.
- **Hosting**: GitHub Pages, **public** repo under personal account
  `risedphantom`. Note: `gh` CLI is currently logged into a work account;
  the builder must switch/add the personal account before pushing.
- **Layout** (mockups in `.superpowers/brainstorm/*/content/layout.html`,
  `dictionary.html`):
  - Top tabs: «Уроки» (two panels) and «Словарь».
  - Left panel: search box; grammar tree grouped by part of speech
    (Глагол, Существительное, Местоимение, Предлог, …) plus «Лексика по
    темам» (numbers, days, time, time adverbs, phrase sets) and «Аудио»;
    each item shows the lesson date; selecting opens the article below the
    tree: markdown body, tables, list of exercises on this topic, source
    line (lesson date, photo count, link to original).
  - Right panel: search box; homework feed newest first, one card per
    homework («Д/з на DD.MM», «задано DD.MM»), tasks inside with a type tag
    (упражнение / текст / тетрадь / повторить / сочинение / аудио), a
    done-checkbox (localStorage), photo thumbnails (click → lightbox with
    the original next to the transcription), blue cross-links to grammar
    topics.
  - Dictionary tab: search by Greek or Russian; filter chips by part of
    speech; sortable by date; columns word / translation / type / where it
    appeared.
  - Hover card on any Greek word: lemma, **translation first**, meta line
    (part of speech, gender, declension class / conjugation group), forms
    table, one example sentence, link to dictionary entry and grammar topic.
  - **Verb cards show all main tenses from day one** (present, aorist /
    simple past, future, imperative at minimum), not only what the course
    has covered.
- UI language: Russian. Greek text kept verbatim.

## Builder's calls (made by Claude, Anton may override)

- Stack: vanilla HTML/CSS/JS, content as JSON under `content/`, media under
  `media/`. No framework, no bundler, no build step. GitHub Pages serves the
  `main` branch root. Python scripts (`uv run`, PEP 723 inline deps) under
  `scripts/`.
- OpenAI model name is read from `OPENAI_MODEL` in `.env`; default is the
  current best vision-capable model at build time. The builder verifies the
  default against `/v1/models` on first run.
- Transcription prompt asks for strict JSON: `kind` (grammar | exercise |
  text | vocab | table | other), `title`, `body_md` (Greek verbatim,
  Russian verbatim, tables as markdown tables), `greek_words` (lemmas with
  surface forms), `confidence` (0–1). Items with confidence < 0.7 are listed
  in the update summary for a human look.
- Segmentation: messages sorted by date; a new session starts after a gap
  of more than 2 hours; a session is `homework` if any of its text messages
  matches `Д/з|Домашн|Задани|Высылаю задани`, else `lesson`. Homework
  «due date» = date in the marker if present, else next lesson date, else
  message date + 2 days (flagged). Albums stay together. A reply-to-photo
  text attaches as that photo's instruction.
- Topic grouping: the transcription step also returns `topic_hint`
  (e.g. «Спряжение 1», «Предлог σε»); a `topics.json` maps hints to tree
  nodes. New unmapped hints land under «Новое» and are shown in the update
  summary for manual placement.
- Lexicon: for each new lemma, one OpenAI call returns translation(s),
  part of speech, gender, declension/conjugation class, all forms (nouns:
  3 cases × 2 numbers with article; adjectives: 3 genders, singular and
  plural; verbs: present, imperfect, aorist, future, imperative, in that
  order of priority), one example sentence with translation. Cached in
  `content/lexicon/<lemma>.json`; never regenerated unless the file is
  deleted.
- Word hover: at render time any token matching a lexicon lemma or surface
  form is wrapped; no runtime API calls.
- Audio: mp3 files hosted in `media/`, listed under «Аудио» with the
  teacher's caption (or «Трек N» if none).
- Original photos are kept at Telegram's downloaded resolution (~600×1280);
  no re-encoding.
- State: `state/last_id` (last ingested message id) and `state/messages/`
  (raw JSON per message) committed, so ingestion is reproducible and
  incremental.
- Skill `/update-greek` lives in `.claude/skills/update-greek/SKILL.md`. It
  runs fetch → transcribe → segment → lexicon, prints a summary (new
  topics, new homework, new words, low-confidence items, unmapped topic
  hints), waits for the human to fix anything, then commits and pushes.
  Manual trigger only; no cron.
- Secrets: `.env`, `tg.session*`, `.superpowers/` in `.gitignore` from the
  first commit.

## Constraints

- Public repo: never commit `.env` or `tg.session`. Teacher's materials and
  textbook scans will be public; Anton accepted this.
- Telegram rate limits: fetch is incremental and sleeps on `FloodWaitError`.
- OpenAI cost: ~15–25 images and ~20–50 new lemmas per update.
- macOS, Python via `uv`, `gh` CLI available.

## Later (explicitly out of scope now)

- Resolve «тетрадь с. 29» references to rendered workbook PDF pages.
- Spaced-repetition or flashcards over the dictionary.
- Automatic scheduling of the update skill.

## Left to the builder

- Exact JSON schemas for `content/*.json`.
- Search implementation (client-side substring is enough at this size).
- Visual design beyond the wireframes; responsive behaviour on narrow
  screens (stacking the two panels is fine).
- How the lightbox and hover card are implemented.
- Test strategy for the Python pipeline (fixtures from the already-pulled
  289 messages).
