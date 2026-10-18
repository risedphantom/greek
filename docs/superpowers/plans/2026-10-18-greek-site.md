# Greek Course Companion Site — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the teacher's Telegram group stream into a static GitHub Pages site (grammar left / homework right / dictionary tab with hover cards) that one CLI command keeps current.

**Architecture:** A Python package `greekpipe` (run via `uv`) does fetch (Telethon) → transcribe (OpenRouter vision/text) → segment → lexicon (OpenRouter text) → build (`content/site.json`). Every stage is incremental and idempotent: inputs and outputs are files in `state/`, `media/`, `content/`, keyed by Telegram message id or lemma, so a crash or 429 just leaves work for the next run. A vanilla HTML/CSS/JS site reads `content/site.json`; no bundler. `/update-greek` is a thin skill over `scripts/update.py`.

**Tech Stack:** Python ≥3.12 via `uv` (telethon, python-dotenv, httpx, pytest), OpenRouter OpenAI-compatible API, vanilla HTML/CSS/JS + `marked` from jsDelivr CDN, GitHub Pages (main branch root), `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-10-18-greek-site-design.md`

## Global Constraints

- Public repo: `.env`, `tg.session*`, `.superpowers/` gitignored; no Telegram ids, sender ids, or sender names anywhere in committed files; only the teacher's messages are stored.
- Runtime AI = OpenRouter only (`OPENROUTER_API_KEY`); no step may call Claude or need a Claude Code session.
- `.env` keys: `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`, `TG_2FA_PASSWORD`, `TG_CHAT_ID` (`-CHAT_ID`), `TG_TEACHER_ID` (`TEACHER_ID`), `OPENROUTER_API_KEY`, `OR_VISION_MODEL` (default `google/gemini-2.5-flash`), `OR_TEXT_MODEL` (default `google/gemini-2.5-flash-lite`).
- Stored message shape (`state/messages/<id>.json`): `{"id", "date" (ISO 8601 with tz), "text", "media" (repo-relative path or null), "media_kind" ("photo"|"audio"|"pdf"|null), "reply_to", "grouped_id"}`. Nothing else.
- Idempotent stages: an output file that exists is never regenerated unless deleted by hand.
- Transcript confidence `< 0.7` → listed in update summary. Unmapped `topic_hint` → group `new` («Новое») and listed in summary.
- Segmentation: new session after a gap `> 2h`; homework marker regex `Д/з|Домашн|Задани|Высылаю задани` (case-insensitive); due date = date in marker, else next lesson session date, else assigned date + 2 days with `due_flagged: true`.
- Lexicon: batches of ≤ 20 lemmas; verbs get present, imperfect, aorist, future, imperative; nouns 3 cases × 2 numbers with article; adjectives 3 genders × 2 numbers; cached at `content/lexicon/<lemma>.json`.
- Dictionary scope: every content word minus the stop-list in `greekpipe/config.py`.
- UI language Russian; Greek verbatim. Verb hover cards show all tenses.
- Commit messages end with the attribution block given in the session.

## Review Focus

1. Telethon `FloodWaitError` mid-backfill: already-downloaded messages stay on disk and `state/last_id` reflects only fully processed messages, so the next run resumes without duplicates. → test in Task 2.
2. OpenRouter 429/5xx or non-JSON reply: the item is left pending (no transcript file), the run continues with other items, and the summary counts pending items. → tests in Task 3 and Task 4.
3. Homework whose marker has no date and no later lesson exists yet (the common Thursday case): due = assigned + 2 days, `due_flagged: true`, shown with «≈» in the UI. → test in Task 5.
4. Greek surface form with different case/accent position or final sigma («Τρώει», «ΚΑΛΗΜΕΡΑ», «φοιτητής» vs «φοιτητήσ» typo): hover lookup is by NFC-casefolded surface form from the lexicon's forms, final `ς` normalised to `σ`. → test in Task 8.
5. A teacher text message that is chat, not content («Интернет пропал», scheduling): never becomes a task, occurrence, or lexicon source. → tests in Task 4 (classifier gate) and Task 5.

---

### Task 1: Project skeleton and config

**Files:**
- Create: `pyproject.toml`, `greekpipe/__init__.py`, `greekpipe/config.py`, `tests/conftest.py`, `tests/fixtures/messages_sample.json`, `tests/test_config.py`, `.nojekyll`, `README.md`
- Modify: `.gitignore` (add `.pytest_cache/`, `.venv/`)

**Interfaces:**
- Produces: `config.Settings` dataclass with fields matching every `.env` key above plus `root: Path`, `state_dir`, `media_dir`, `content_dir`, `transcripts_dir`, `lexicon_dir`; `config.load_settings(root: Path | None = None) -> Settings` (reads `.env` via python-dotenv, applies model defaults); `config.STOPWORDS: frozenset[str]` (NFC-casefolded; at least: ο η το οι τα τον την του της των τους τις και δεν να σε με από για που είναι εγώ εσύ αυτός αυτή αυτό εμείς εσείς αυτοί αυτές αυτά μου σου της μας σας τους ναι όχι τι πού πώς); `config.normalize(word: str) -> str` (NFC, casefold, strip punctuation incl. `;«»–—`, final `ς` → `σ`).

- [ ] **Step 1: Write `pyproject.toml`**: project `greekpipe`, `requires-python = ">=3.12"`, dependencies `telethon>=1.36`, `python-dotenv`, `httpx>=0.27`; dev group `pytest`, `pytest-asyncio`, `respx`. `[tool.pytest.ini_options] testpaths = ["tests"]`.
- [ ] **Step 2: Copy fixture** `/private/tmp/claude-501/-Users-anton-dev-own-greek/5486a39f-ed65-419d-930e-e549ceb18baf/scratchpad/raw/fixture_messages.json` → `tests/fixtures/messages_sample.json` (104 sanitised teacher messages, 2026-09-07…2026-09-24, already in the stored message shape).
- [ ] **Step 3: Write failing tests** `tests/test_config.py`:
  - `test_load_settings_defaults_models(tmp_path, monkeypatch)`: write a `.env` with only TG/OR keys → `s.or_vision_model == "google/gemini-2.5-flash"`, `s.or_text_model == "google/gemini-2.5-flash-lite"`, `s.tg_chat_id == -CHAT_ID` (int), `s.transcripts_dir == tmp_path/"content"/"transcripts"`.
  - `test_normalize`: `normalize("Τρώει;") == "τρώει"`, `normalize("φοιτητής") == "φοιτητήσ"`, `normalize("ΚΑΛΗΜΕΡΑ,") == "καλημερα"` — note: casefold keeps accents; assert `normalize("Καλημέρα") == "καλημέρα"`.
  - `test_stopwords_normalized`: every item in `STOPWORDS` equals `normalize(item)`.
- [ ] **Step 4: Run** `uv run pytest -q` → FAIL (module missing).
- [ ] **Step 5: Implement `greekpipe/config.py`** per Interfaces.
- [ ] **Step 6: Run** `uv run pytest -q` → PASS. Also `uv run python -c "from greekpipe.config import load_settings; print(load_settings().or_vision_model)"` prints the model.
- [ ] **Step 7: Write `README.md`** (10 lines: what it is, `uv run scripts/update.py`, `.env` keys) and empty `.nojekyll`.
- [ ] **Step 8: Commit** `feat: project skeleton, settings and fixture`.

### Task 2: Telegram fetch (incremental, stripped, resumable)

**Files:**
- Create: `greekpipe/tg_fetch.py`, `tests/test_tg_fetch.py`
- Output dirs: `state/messages/`, `state/last_id`, `media/`

**Interfaces:**
- Produces: `tg_fetch.strip_message(m, media_path: str | None) -> dict` (Telethon `Message` or duck-typed object with `.id .date .message .reply_to .grouped_id .media` → stored shape; `media_kind` from `MessageMediaPhoto` → `"photo"`, document mime `audio/*` → `"audio"`, `application/pdf` → `"pdf"`, anything else incl. `MessageMediaWebPage` → `None`, `media=None`); `tg_fetch.media_filename(msg_id: int, kind: str) -> str` (`media/<id>.jpg|.mp3|.pdf`); `tg_fetch.read_last_id(state_dir) -> int` (0 if missing); `async tg_fetch.fetch_new(settings, client=None) -> list[int]` (ids written this run); `tg_fetch.load_messages(state_dir) -> list[dict]` sorted by id.
- Behaviour: iterate `client.iter_messages(chat, min_id=last_id, reverse=True)`; skip `sender_id != TG_TEACHER_ID`; skip `voice` (ogg) documents; download media before writing the JSON; write JSON then advance `state/last_id` per message; on `FloodWaitError` sleep `e.seconds + 1` and continue; on `KeyboardInterrupt`/other exception, the last_id already written stays valid.

- [ ] **Step 1: Write failing tests** with duck-typed fake messages (no network):
  - `test_strip_message_photo`: fake with `MessageMediaPhoto` instance → `media_kind == "photo"`, `media == "media/123.jpg"`, keys exactly the stored shape, no `sender_id` key.
  - `test_strip_message_webpage_is_text_only`: `MessageMediaWebPage` → `media is None`, `media_kind is None`, text preserved.
  - `test_strip_message_voice_skipped`: `fetch_new` with a fake client yielding a voice note from the teacher writes nothing.
  - `test_fetch_filters_non_teacher_and_advances_last_id(tmp_path)`: fake client yields ids 10 (other sender), 11 (teacher text), 12 (teacher photo) → only `11.json`, `12.json` exist, `last_id == 12`, returned `[11, 12]`.
  - `test_fetch_resumes_after_floodwait(tmp_path)`: fake client raises `FloodWaitError(seconds=0)` once after id 11 → both 11 and 12 written, no duplicates, sleep patched.
  - `test_load_messages_sorted(tmp_path)`.
- [ ] **Step 2: Run** `uv run pytest tests/test_tg_fetch.py -q` → FAIL.
- [ ] **Step 3: Implement `greekpipe/tg_fetch.py`**. Client factory: `TelegramClient(str(root/"tg.session"), api_id, api_hash)`; `await client.start(phone=…, password=…)` only when `client is None`.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Real backfill**: `uv run python -c "import asyncio; from greekpipe.config import load_settings; from greekpipe.tg_fetch import fetch_new; print(len(asyncio.run(fetch_new(load_settings()))))"` → prints ≈250; `ls media | wc -l` ≈ 144 (124 jpg + 16 mp3 + 4 pdf); `cat state/last_id` ≥ 124304. `grep -rl sender state/ | wc -l` → 0.
- [ ] **Step 6: Commit** `feat: incremental Telegram fetch; initial state and media` (include `state/` and `media/`).

### Task 3: OpenRouter client

**Files:**
- Create: `greekpipe/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Produces: `class LLMClient: def __init__(self, api_key: str, base_url="https://openrouter.ai/api/v1", http: httpx.Client | None = None, max_retries=4)`; `def chat_json(self, model: str, system: str, user_parts: list[dict], schema_name: str) -> dict` (sends `response_format={"type":"json_object"}`, strips ``` fences, `json.loads`; raises `LLMTransient` after retries on 429/5xx/timeout with exponential backoff 2,4,8,16 s honouring `Retry-After`; raises `LLMBadOutput` when the reply is not a JSON object); `def image_part(path: Path) -> dict` (`{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,…"}}`); `def text_part(s: str) -> dict`; `.last_cost: float` accumulated from `usage.cost`.
- Protocol for tests: anything with `chat_json(...)` — Task 4 and Task 7 accept `client: LLMClient | FakeLLM`.

- [ ] **Step 1: Write failing tests** using `respx`:
  - `test_chat_json_parses_fenced_json`: body `{"choices":[{"message":{"content":"```json\n{\"a\":1}\n```"}}],"usage":{"cost":0.001}}` → `{"a":1}`, `client.last_cost == 0.001`.
  - `test_chat_json_retries_on_429_then_succeeds`: first 429 with `Retry-After: 0`, then 200 → result; `route.call_count == 2`.
  - `test_chat_json_raises_transient_after_max_retries`: four 503s → `LLMTransient`; sleep patched.
  - `test_chat_json_bad_output`: content `"sorry"` → `LLMBadOutput`.
  - `test_image_part_is_data_url(tmp_path)`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: OpenRouter JSON client with backoff`.

### Task 4: Transcribe photos and classify text posts

**Files:**
- Create: `greekpipe/transcribe.py`, `greekpipe/prompts.py`, `tests/test_transcribe.py`
- Output: `content/transcripts/<msg_id>.json`

**Interfaces:**
- Consumes: `LLMClient.chat_json`, `image_part`, `text_part`; `tg_fetch.load_messages`.
- Produces: transcript shape `{"msg_id": int, "source": "photo"|"text", "kind": "grammar"|"exercise"|"text"|"vocab"|"table"|"chat"|"other", "title": str, "body_md": str, "greek_words": [{"lemma": str, "pos": "noun"|"verb"|"adj"|"adv"|"phrase"|"other", "surface": [str]}], "topic_hint": str, "confidence": float, "model": str}`; `transcribe.is_candidate(msg: dict) -> bool` (photo, or text with ≥ 40 chars after stripping URLs/emoji and not matching `^(Добр|Хорошего|Спасибо)`); `transcribe.transcribe_pending(settings, client, limit: int | None = None) -> TranscribeReport` with `done: list[int]`, `pending: list[int]` (transient failures), `low_confidence: list[int]`, `cost: float`; `transcribe.load_transcripts(transcripts_dir) -> dict[int, dict]`.
- Prompts in `prompts.py`: `VISION_SYSTEM` (Russian+Greek textbook page; transcribe verbatim; tables as markdown tables; keep Russian explanations; classify `kind`; `topic_hint` = short Russian grammar topic name like «Спряжение 1», «Предлог σε», «Склонение ж.р. -α», «Глагол είμαι», «Лексика: дни недели»; list every Greek content word as lemma + surface forms + pos; confidence 0–1; output JSON only, schema spelled out), `TEXT_SYSTEM` (same schema; `kind: "chat"` for scheduling/greetings).

- [ ] **Step 1: Write failing tests** with a `FakeLLM` returning canned dicts and recording calls:
  - `test_is_candidate`: photo → True; `"Интернет пропал"` → False; `"https://telemost.yandex.ru/j/1"` → False; 60-char vocab text → True; `"Добрый день! Д/з"` → False.
  - `test_transcribe_writes_file_and_skips_existing(tmp_path)`: two photo messages, one transcript pre-existing → FakeLLM called once, both files present, `report.done == [new_id]`.
  - `test_transcribe_marks_low_confidence`: fake returns `confidence 0.5` → id in `report.low_confidence`, file still written.
  - `test_transient_error_leaves_pending`: fake raises `LLMTransient` for one id → in `report.pending`, no file, other id done.
  - `test_bad_output_writes_other_kind_with_zero_confidence`: fake raises `LLMBadOutput` → file written with `kind "other"`, `confidence 0.0`, id in `low_confidence`.
  - `test_text_post_uses_text_model`: fake records model → equals `settings.or_text_model`; photo → `or_vision_model`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `transcribe.py` + `prompts.py`. **Step 4: Run** → PASS.
- [ ] **Step 5: Smoke on 3 real photos**: `uv run python -c "…transcribe_pending(s, LLMClient(s.openrouter_api_key), limit=3)"` → open the 3 JSON files, eyeball `body_md` against `media/<id>.jpg` (Read tool). Adjust prompt wording if `kind`/`topic_hint` are off; delete the 3 files and rerun until good.
- [ ] **Step 6: Full backfill**: run without limit; expect ≈160 transcripts, cost ≈ $0.30–0.50; rerun once if `pending` non-empty.
- [ ] **Step 7: Commit** `feat: photo/text transcription via OpenRouter; backfilled transcripts` (include `content/transcripts/`).

### Task 5: Segment into lessons, homework and tasks

**Files:**
- Create: `greekpipe/segment.py`, `tests/test_segment.py`

**Interfaces:**
- Consumes: stored messages (list[dict]), transcripts (`dict[int, dict]`).
- Produces: `@dataclass Session(kind: Literal["lesson","homework","chat"], date: date, message_ids: list[int], due: date | None, due_flagged: bool)`; `@dataclass Task(idx: int, type: str, text: str, media_ids: list[int])`; `segment.sessions(messages, transcripts) -> list[Session]` (gap > 2h splits; marker regex → `homework`; else `lesson` if the session has ≥ 1 transcript with kind not in {chat, other} or a text matching `telemost|zoom`; else `chat`; due from `(\d{1,2})\.(\d{1,2})` after the marker, year from message date; else next lesson date; else +2 days flagged); `segment.tasks_for(session, messages, transcripts) -> list[Task]` (each instruction text = task; following photos until next text attach to it; a text with `reply_to` pointing at a photo attaches to that photo's task; leading photos with no instruction → one task per album/photo titled from transcript `title`, type from transcript kind; marker/greeting/closing messages («Добрый день!», «Хорошего дня!», «Если есть вопросы…», `kind == "chat"`) are not tasks); `segment.task_type(text: str, transcript_kind: str | None) -> str` returning one of `тетрадь|повторить|сочинение|текст|аудио|упражнение|задание` by regex `тетрад` / `повтор|знать|выучить` / `сочинен|составить` / `прочитать|текст|перев` / `прослуш|трек|аудио` / `упр|выполн|письменно|устно|раскр`.

- [ ] **Step 1: Write failing tests** on `tests/fixtures/messages_sample.json`:
  - `test_sessions_split_and_kind`: session containing id 123086 («Д/з на 21.09.») is `homework` with `due == date(2026,9,21)`, `due_flagged False`; session containing 122493–122499 (07.09 16:03) is `lesson`; session of 122632–122640 (10.09) is `homework`, due 14.09 from «Домашнее задание на 14.09.»; session of 123206–123208 (20.09 «Добрый день!», «Да, конечно», «Отлично…») is `chat`.
  - `test_due_falls_back_to_next_lesson`: strip the date from 122632's text («Домашнее задание на 14.09.») → due equals the 14.09 lesson session date (`date(2026,9,14)`), `due_flagged False`.
  - `test_due_flagged_when_no_later_lesson`: homework at the end of the fixture with no date → `due == assigned + 2 days`, `due_flagged True`.
  - `test_tasks_18_09`: tasks for the 18.09 homework → 4 tasks: types `["повторить","упражнение","тетрадь","сочинение"]`; task 1 has `media_ids == [123088, 123089]`; «Попробуй через этот тренажёр…» and the autodraw link are not separate tasks (folded into the тетрадь task text or dropped).
  - `test_reply_to_photo_attaches`: in the 10.09 homework, text 122638 («Выполнить задания») and the reply-to-photo 122639 land in one task with `media_ids == [122637, 122639]`.
  - `test_chat_messages_never_tasks`: a session with only «Интернет пропал» yields no tasks.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: session and task segmentation`.

### Task 6: Topic taxonomy and seed `topics.json`

**Files:**
- Create: `greekpipe/topics.py`, `content/topics.json`, `tests/test_topics.py`

**Interfaces:**
- Produces: `topics.json` shape `{"groups":[{"id":"verb","title":"Глагол","topics":[{"id":"conj-1","title":"Спряжение 1 (глаголы на -ω)","hints":["спряжение 1","глаголы 1 спряжения", …]}]}]}`; groups in order: `verb` Глагол, `noun` Существительное, `article` Артикль, `pronoun` Местоимение, `adj` Прилагательное, `prep` Предлог, `adverb` Наречие, `lexis` Лексика по темам, `texts` Тексты для чтения, `audio` Аудио, `new` Новое; `topics.load_topics(path) -> Taxonomy`; `Taxonomy.resolve(topic_hint: str) -> tuple[group_id, topic_id]` (match `normalize(hint)` against normalised `hints`, then substring either way; else `("new", slug(hint))`); `Taxonomy.unmapped(hints: Iterable[str]) -> list[str]`; `Taxonomy.topic_for_task_text(text: str) -> list[topic_id]` (hints found as substrings in the task text, e.g. «повторить спряжение глаголов 1 и 2» → `conj-1`, `conj-2`; «предлог σε» → `prep-se`).

- [ ] **Step 1: Write failing tests**: `test_resolve_exact_and_substring` («Предлог σε и его значения» → `("prep","prep-se")`); `test_resolve_unmapped_goes_to_new`; `test_topic_for_task_text`; `test_every_backfilled_hint_is_mapped`: for every transcript in `content/transcripts/` with kind ≠ chat/other, `resolve(topic_hint)[0] != "new"` (this test is what forces the seeding; it reads the real repo dir, skip if absent).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement `topics.py`.**
- [ ] **Step 4: Seed `content/topics.json`**: `uv run python -c "…print sorted set of (topic_hint, kind, count) from transcripts"`, then write the taxonomy by hand with hints covering every printed hint (expected ≈ 40–80 hints into ≈ 20–30 topics). Reading texts («Κείμενο 1…») go to `texts`; vocab sets (числа, дни недели, время, наречия времени, приветствия, фразы) to `lexis`.
- [ ] **Step 5: Run tests** → PASS including `test_every_backfilled_hint_is_mapped`.
- [ ] **Step 6: Commit** `feat: topic taxonomy seeded from backfill`.

### Task 7: Lexicon generation

**Files:**
- Create: `greekpipe/lexicon.py`, `tests/test_lexicon.py`; prompt `LEXICON_SYSTEM` added to `greekpipe/prompts.py`
- Output: `content/lexicon/<normalized-lemma>.json`

**Interfaces:**
- Consumes: transcripts' `greek_words`, `config.STOPWORDS`, `config.normalize`, `LLMClient`.
- Produces: `lexicon.collect_lemmas(transcripts: dict[int, dict]) -> dict[str, LemmaRef]` where `LemmaRef(lemma: str, pos: str, surfaces: set[str], msg_ids: set[int])`, keyed by `normalize(lemma)`, stop-words and single-letter tokens dropped, Latin/Cyrillic-only tokens dropped; `lexicon.generate_pending(settings, client, refs, batch_size=20) -> LexiconReport(done: list[str], pending: list[str], needs_review: list[str], cost: float)`; entry shape `{"lemma", "translation": [str], "pos", "gender": "m"|"f"|"n"|null, "class": str|null, "forms": {...}, "example": {"el", "ru"}, "needs_review": bool, "model"}` with `forms` by pos: noun `{"sg":{"nom","gen","acc"},"pl":{"nom","gen","acc"}}` each a string including the article; verb `{"present":[6],"imperfect":[6],"aorist":[6],"future":[6],"imperative":{"sg","pl"}}`; adj `{"m":{"sg","pl"},"f":{…},"n":{…}}`; adv/phrase/other → `{}`; `lexicon.validate(entry) -> bool` (shape above; any 6-list not length 6 → False) — invalid entries are still written with `needs_review: true`; `lexicon.load_lexicon(dir) -> dict[str, dict]`.
- Prompt asks for Modern Greek (monotonic, no polytonic accents), Russian translations, exactly the schema, one short example sentence per lemma using course-level vocabulary.

- [ ] **Step 1: Write failing tests**: `test_collect_lemmas_drops_stopwords_and_merges_surfaces` (two transcripts both containing `τρώω` with surfaces «τρώει», «Τρώω» → one ref, surfaces merged, `msg_ids` both; `και` absent); `test_generate_batches_of_20` (45 refs → FakeLLM called 3 times); `test_generate_skips_cached` (file exists → not requested); `test_invalid_verb_marked_needs_review` (fake returns 5 present forms → file written, `needs_review True`, in report); `test_transient_leaves_batch_pending`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Smoke 1 batch** on real data, eyeball 3 entries (one verb, one noun, one adjective) for monotonic accents and sane forms; fix prompt if needed.
- [ ] **Step 6: Full run**; expect ≈ 800–1200 entries; cost ≈ $0.05–0.20. Rerun if pending.
- [ ] **Step 7: Commit** `feat: lexicon generation; backfilled dictionary`.

### Task 8: Build `content/site.json`

**Files:**
- Create: `greekpipe/build.py`, `tests/test_build.py`
- Output: `content/site.json`

**Interfaces:**
- Consumes: messages, transcripts, `segment.sessions/tasks_for`, `Taxonomy`, lexicon entries.
- Produces: `build.build_site(settings) -> dict` and `build.write_site(settings) -> Path`; shape:
  ```
  {"updated": "YYYY-MM-DD",
   "grammar": {"groups": [{"id","title","topics": [{"id","title","first_date","occurrences": [{"id": "<msg_id or grouped_id>", "date","kind","title","body_md","media": [paths],"msg_ids": [int],"confidence"}], "task_refs": [{"hw_id","task_idx"}]}]}]},
   "homework": [{"id": "hw-YYYY-MM-DD", "assigned","due","due_flagged","tasks": [{"idx","type","text","media": [paths],"transcripts": [{"title","body_md","kind"}],"topic_ids": [str],"done_key": "hw-…:idx"}]}],
   "dictionary": [{"lemma","translation","pos","gender","class","forms","example","needs_review","first_date","sources": [{"kind": "topic"|"hw", "id", "date"}]}],
   "forms_index": {"<normalized surface>": "<lexicon key>"},
   "audio": [{"id","date","file","caption"}],
   "stats": {"topics","occurrences","homework","tasks","words"}}
  ```
  Rules: occurrences come from transcripts with kind in grammar/table/vocab/text (any session kind), grouped by album (`grouped_id`) into one occurrence, placed via `Taxonomy.resolve(topic_hint)`; `task_refs` are tasks whose `topic_ids` include the topic; `topic_ids` = `topic_for_task_text(text)` ∪ resolved hints of the task's transcripts; `forms_index` covers lemma, every surface, and every string inside `forms` (split on spaces, articles dropped, `normalize`d); dictionary sorted by `first_date` then lemma; `homework` newest first; `audio` from `media_kind == "audio"` with caption = message text or the nearest preceding teacher text within 2 minutes, else «Трек N».

- [ ] **Step 1: Write failing tests** using the fixture plus hand-written mini transcripts/lexicon in `tmp_path`:
  - `test_album_becomes_one_occurrence` (6 photos with same `grouped_id` → 1 occurrence, `media` length 6).
  - `test_task_refs_and_topic_ids_cross_link` («повторить предлог σε…» task ↔ `prep-se` topic appears in both directions).
  - `test_forms_index_handles_case_accent_final_sigma`: lexicon entry `τρώω` with present `["τρώω","τρως","τρώει",…]` → `forms_index[normalize("Τρώει")] == "τρώω"`, `forms_index[normalize("φοιτητής")]` present, noun forms strip the article («η δουλειά» → key `δουλειά`).
  - `test_homework_newest_first_and_due_flag`.
  - `test_write_site_is_valid_json_and_has_stats`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Real build**: `uv run python -c "from greekpipe.config import load_settings; from greekpipe.build import write_site; print(write_site(load_settings()))"`; check `stats` (expect topics ≈ 20–30, homework ≈ 10, words ≈ 800+); `python3 -c "import json;d=json.load(open('content/site.json'));print([g['title'] for g in d['grammar']['groups'] if g['topics']])"` must not include «Новое».
- [ ] **Step 6: Commit** `feat: site.json builder; first content build`.

### Task 9: `scripts/update.py` CLI and summary

**Files:**
- Create: `scripts/update.py`, `greekpipe/summary.py`, `tests/test_summary.py`

**Interfaces:**
- Produces: `summary.render(fetch_ids, transcribe_report, lexicon_report, taxonomy_unmapped, site_stats, cost) -> str` (Russian, sections: Новые сообщения N, Новые разборы N (низкая уверенность: ids with `media/` paths), Ожидают повтора N, Новые темы в «Новое»: hints, Новые слова N (нужна проверка: lemmas), Итого стоимость $x.xxxx); `scripts/update.py` argparse: `--no-fetch`, `--limit N`, `--push`, `--dry-run`; order fetch → transcribe → lexicon → build → summary; `--push` runs `git add state media content && git commit -m "content: update <date>" && git push` only when something changed; exit code 0 even with pending items, 2 on transient failure in fetch.
- [ ] **Step 1: Write failing test** `test_render_summary_lists_low_confidence_and_unmapped` (string contains `media/123.jpg`, «Новое», `$0.0123`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `summary.py` and `scripts/update.py` (PEP 723 header not needed; it imports `greekpipe` via `uv run` in project). **Step 4: Run** → PASS.
- [ ] **Step 5: Run** `uv run scripts/update.py --no-fetch` → summary prints, nothing regenerated (idempotent: 0 new transcripts, 0 new words).
- [ ] **Step 6: Commit** `feat: update CLI with summary`.

### Task 10: Web — shell, grammar panel, search

**Files:**
- Create: `index.html`, `app.js`, `styles.css`

**Interfaces:**
- Consumes: `content/site.json` shape from Task 8.
- Produces: hash routes `#/lessons` (default), `#/lessons/topic/<id>`, `#/lessons/hw/<id>`, `#/dictionary`, `#/dictionary/<lexicon key>`; `window.GREEK = {site, formsIndex}` after load; `renderMarkdown(md) -> HTMLString` via `marked` (CDN `https://cdn.jsdelivr.net/npm/marked/marked.min.js`), with every Greek token (regex `[Ͱ-Ͽἀ-῿]+`) wrapped in `<span class="w" data-key="…">` when `normalize(token)` is in `forms_index`; JS `normalize` mirrors Python (`NFC`, `toLowerCase`, final `ς`→`σ`, strip punctuation).
- Layout per mockup: top bar (title, tabs Уроки/Словарь, «обновлено …»), left pane with search input + grouped topic list (date on each item) + article area below; right pane placeholder for Task 11. Search filters topics by title, hints are not available client-side — filter by title + occurrence titles + body text (case-insensitive substring); matched topic auto-expands.

- [ ] **Step 1: Write files.** Article shows each occurrence chronologically: date, kind badge, rendered `body_md`, thumbnails of `media`, confidence < 0.7 → badge «проверить», line «Источник: урок DD.MM · фото ×N». Group «Аудио» lists `site.audio` items as `<audio controls preload="none" src=…>` with caption and date.
- [ ] **Step 2: Verify locally**: `python3 -m http.server 8000` then open `http://localhost:8000/#/lessons` — checklist: tree renders all groups with topics, clicking a topic shows tables rendered as `<table>`, search «σε» narrows to the preposition topic, Greek words in article are underlined dotted. Take a screenshot (Read tool on a saved PNG via `screencapture` or browser) to confirm.
- [ ] **Step 3: Commit** `feat: web shell with grammar panel and search`.

### Task 11: Web — homework panel, lightbox, done-checkboxes, cross-links

**Files:**
- Modify: `index.html`, `app.js`, `styles.css`

- [ ] **Step 1: Implement** right pane: search input; cards newest first «Д/з на DD.MM» (prefix «≈» when `due_flagged`) with «задано DD.MM»; tasks with type tag, checkbox (localStorage key `done:<done_key>`), text, thumbnails, collapsible transcript, blue links for `topic_ids` → `#/lessons/topic/<id>`; topic article's `task_refs` → `#/lessons/hw/<id>` scrolling to the task. Lightbox: click thumbnail → overlay with full image left and transcript `body_md` right, Esc closes, ←/→ within the task's media.
- [ ] **Step 2: Verify locally** checklist: tick a checkbox, reload, still ticked; click «Спряжение 1» link in a task → topic opens; lightbox shows photo + text.
- [ ] **Step 3: Commit** `feat: homework panel, lightbox, cross-links`.

### Task 12: Web — dictionary tab and hover card

**Files:**
- Modify: `index.html`, `app.js`, `styles.css`

- [ ] **Step 1: Implement** dictionary view: search over lemma + translations (Greek or Russian), filter chips (Все / Глаголы / Сущ. / Прил. / Наречия / Фразы), sort toggle date↔alphabet, table columns Слово / Перевод / Тип / Где встречалось (sources as links); `needs_review` entries get «проверить» badge. Hover card on any `.w` span (and on dictionary rows): lemma, translation first (blue), meta line (pos in Russian, gender, class), forms table (noun: 6 rows with articles; verb: tabs or stacked sections Настоящее / Имперфект / Аорист / Будущее / Повелительное, 6 rows each; adj: 3×2), example `el — ru`, links «открыть в словаре» and first topic source. Card appears after 150 ms hover, stays while hovered, closes on mouseleave; on touch, tap toggles.
- [ ] **Step 2: Verify locally** checklist: hover «τρώει» in a reading text → card for τρώω with all 5 tense blocks; dictionary search «работа» finds δουλειά; filter «Глаголы» hides nouns.
- [ ] **Step 3: Commit** `feat: dictionary tab and hover cards`.

### Task 13: Deploy to GitHub Pages

**Files:**
- Modify: `README.md` (site URL)

- [ ] **Step 1:** `gh auth status` shows active account `risedphantom`; if not, `gh auth switch -u risedphantom`.
- [ ] **Step 2:** `gh repo create risedphantom/greek --public --source . --remote origin --push` (repo name is the builder's call; `greek` default).
- [ ] **Step 3:** Enable Pages from main root: `gh api -X POST repos/risedphantom/greek/pages -f 'source[branch]=main' -f 'source[path]=/'`; poll `gh api repos/risedphantom/greek/pages --jq .html_url` and `curl -sI <url>` → 200 within ~2 min.
- [ ] **Step 4:** Open the URL, run the three checklists from Tasks 10–12 once on the live site; confirm `curl -s <url>/.env` → 404 and `git ls-files | grep -E 'tg.session|\.env$'` → empty.
- [ ] **Step 5: Commit** `docs: site URL` and push.

### Task 14: `/update-greek` skill

**Files:**
- Create: `.claude/skills/update-greek/SKILL.md`

- [ ] **Step 1: Write SKILL.md** (frontmatter `name: update-greek`, `description: Pull new messages from the Greek teacher's Telegram group, transcribe and publish the site. Use when the user says "update greek", "обнови сайт", "new homework", or twice a week after lessons.`): steps — (1) `uv run scripts/update.py` and show the summary verbatim; (2) for each low-confidence transcript open `media/<id>.jpg` and the transcript side by side, fix `body_md`/`kind`/`topic_hint` by editing the JSON if wrong; (3) for each hint under «Новое» add it to an existing topic's `hints` in `content/topics.json` or create a topic; (4) `uv run scripts/update.py --no-fetch` to rebuild; (5) `uv run scripts/update.py --no-fetch --push`; (6) report the live URL and what changed (N topics, N tasks, N words). Time budget ≈ 5–10 min.
- [ ] **Step 2: Dry run** the skill in this session: invoke `/update-greek`; expect «Новые сообщения 0» and no commit.
- [ ] **Step 3: Commit** `feat: update-greek skill` and push.
