'use strict';

// ---------------------------------------------------------------- state

const state = {
  site: null,
  formsIndex: {},
  dictByKey: new Map(),
  query: '',
  topicId: null,
  openGroups: new Set(),
  topicText: new Map(), // topic id -> lowercased searchable text
  hwCards: [], // [{ card, rows: [{ row, text }] }] for the homework filter
};

const KIND_LABELS = { grammar: 'правило', table: 'таблица', vocab: 'лексика', text: 'текст' };
const LOW_CONFIDENCE = 0.7;
const AUDIO_GROUP = 'audio';
const FLASH_MS = 2000;
const PHOTO_RE = /\.(jpe?g|png|webp|gif)$/i;

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// "YYYY-MM-DD" -> "DD.MM" / "DD.MM.YYYY"
function fmtDate(iso, withYear = false) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return withYear ? `${d}.${m}.${y}` : `${d}.${m}`;
}

function hwDate(hwId) {
  return fmtDate(hwId.replace(/^hw-/, ''));
}

// Restart a short highlight animation on a node.
function flash(node) {
  node.classList.remove('flash');
  void node.offsetWidth;
  node.classList.add('flash');
  setTimeout(() => node.classList.remove('flash'), FLASH_MS);
}

// ---------------------------------------------------------------- normalize / index

const PUNCT_RE = /[;,.!?«»–—()\[\]:\"'…·\u037E\u0387]/g;

function normalize(token) {
  return token.normalize('NFC').toLowerCase().replace(PUNCT_RE, '').replace(/ς/g, 'σ');
}

function stripAccents(s) {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').normalize('NFC');
}

function lookupKey(token) {
  const n = normalize(token);
  return state.formsIndex[n] ?? state.formsIndex[stripAccents(n)];
}

function buildSearchIndex() {
  for (const group of state.site.grammar.groups) {
    for (const t of group.topics) {
      const parts = [t.title];
      for (const o of t.occurrences) parts.push(o.title, o.body_md);
      state.topicText.set(t.id, parts.join('\n').toLowerCase());
    }
  }
}

// ---------------------------------------------------------------- markdown + wrapping

const GREEK_RE = /[\u0370-\u03FF\u1F00-\u1FFF]+/g;

// Wrap Greek tokens found in forms_index; walks text nodes only.
function wrapGreek(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    GREEK_RE.lastIndex = 0;
    if (!GREEK_RE.test(text)) continue;
    GREEK_RE.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    let wrapped = false;
    for (const m of text.matchAll(GREEK_RE)) {
      const key = lookupKey(m[0]);
      if (!key) continue;
      if (m.index > last) frag.append(text.slice(last, m.index));
      frag.append(el('span', { class: 'w', 'data-key': key, tabindex: '0', text: m[0] }));
      last = m.index + m[0].length;
      wrapped = true;
    }
    if (!wrapped) continue;
    if (last < text.length) frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

function markdownTemplate(md) {
  const tpl = document.createElement('template');
  tpl.innerHTML = marked.parse(md || '', { gfm: true, breaks: true }); // our own content
  wrapGreek(tpl.content);
  return tpl;
}

// String form kept for the window.GREEK contract.
function renderMarkdown(md) {
  return markdownTemplate(md).innerHTML;
}

function renderMarkdownInto(node, md) {
  node.append(markdownTemplate(md).content);
  return node;
}

// Plain text (no markdown) with Greek words wrapped.
function textWithGreek(str, cls) {
  const span = el('span', { class: cls, text: str });
  wrapGreek(span);
  return span;
}

// ---------------------------------------------------------------- router

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  if (parts[0] === 'dictionary') return { tab: 'dictionary', key: parts[1] || null };
  if (parts[1] === 'topic' && parts[2]) return { tab: 'lessons', topicId: parts[2] };
  if (parts[1] === 'hw' && parts[2]) {
    const taskIdx = parts[3] != null && parts[3] !== '' ? Number(parts[3]) : null;
    return { tab: 'lessons', hwId: parts[2], taskIdx };
  }
  return { tab: 'lessons' };
}

function route() {
  closeLightbox();
  hideCard();
  const r = parseRoute();
  setTab(r.tab);
  if (r.tab === 'dictionary') {
    renderDictionary(r.key);
    return;
  }
  if (r.topicId && r.topicId !== state.topicId) selectTopic(r.topicId);
  if (r.hwId) showHomework(r.hwId, r.taskIdx);
}

function setTab(tab) {
  document.getElementById('view-lessons').hidden = tab !== 'lessons';
  document.getElementById('view-dictionary').hidden = tab !== 'dictionary';
  for (const name of ['lessons', 'dictionary']) {
    const a = document.getElementById(`tab-${name}`);
    a.classList.toggle('on', name === tab);
    if (name === tab) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

// ---------------------------------------------------------------- views: topic list

function findTopic(id) {
  for (const group of state.site.grammar.groups) {
    const topic = group.topics.find((t) => t.id === id);
    if (topic) return { group, topic };
  }
  return null;
}

// Topic titles win: «σε» should find the preposition, not every text containing σε.
// Falls back to occurrence titles + body text when no title matches.
function topicMatcher(q) {
  if (!q) return () => true;
  const topics = state.site.grammar.groups.flatMap((g) => g.topics);
  if (topics.some((t) => t.occurrences.length && t.title.toLowerCase().includes(q))) {
    return (t) => t.title.toLowerCase().includes(q);
  }
  return (t) => (state.topicText.get(t.id) || '').includes(q);
}

function renderTopicList() {
  const q = state.query.trim().toLowerCase();
  const list = document.getElementById('topic-list');
  list.replaceChildren();
  const matches = topicMatcher(q);

  for (const group of state.site.grammar.groups) {
    if (group.id === AUDIO_GROUP) continue;
    const topics = group.topics.filter((t) => t.occurrences.length > 0 && matches(t));
    if (!topics.length) continue;
    list.append(renderGroup(group.id, group.title, q !== '', topics.map(renderTopicItem)));
  }

  const audio = (state.site.audio || []).filter((a) => !q || a.caption.toLowerCase().includes(q));
  if (audio.length) list.append(renderGroup(AUDIO_GROUP, 'Аудио', q !== '', audio.map(renderAudioItem)));

  if (!list.children.length) list.append(el('p', { class: 'muted', text: 'Ничего не найдено' }));
}

function renderGroup(id, title, forceOpen, items) {
  const open = forceOpen || state.openGroups.has(id);
  const header = el('button', {
    type: 'button',
    class: 'grp',
    'aria-expanded': String(open),
    onclick: () => {
      if (state.openGroups.has(id)) state.openGroups.delete(id);
      else state.openGroups.add(id);
      renderTopicList();
    },
  }, el('span', { class: 'caret', 'aria-hidden': 'true', text: open ? '▾' : '▸' }), title);
  const body = el('div', { class: 'grp-items', hidden: !open }, items);
  return el('div', { class: 'group' }, header, body);
}

function renderTopicItem(topic) {
  return el('a', {
    class: 'item' + (topic.id === state.topicId ? ' on' : ''),
    href: `#/lessons/topic/${encodeURIComponent(topic.id)}`,
    'aria-current': topic.id === state.topicId ? 'true' : null,
  }, topic.title, ' ', el('small', { text: fmtDate(topic.first_date) }));
}

// ---------------------------------------------------------------- views: audio

function renderAudioItem(a) {
  return el('div', { class: 'audio-item' },
    el('div', { class: 'audio-cap' }, a.caption, ' ', el('small', { text: fmtDate(a.date, true) })),
    el('audio', { controls: true, preload: 'none', src: a.file }));
}

// ---------------------------------------------------------------- views: article

function selectTopic(id) {
  const found = findTopic(id);
  if (!found) return;
  state.topicId = id;
  state.openGroups.add(found.group.id);
  renderTopicList();
  renderArticle(found.topic);
  document.getElementById('article').scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function renderArticle(topic) {
  const article = document.getElementById('article');
  const occs = [...topic.occurrences].sort((a, b) => a.date.localeCompare(b.date));
  article.replaceChildren(el('h2', { text: topic.title }), ...occs.map(renderOccurrence));

  if (topic.task_refs.length) {
    const links = topic.task_refs.map((ref) => el('li', {},
      el('a', { class: 'ref', href: `#/lessons/hw/${encodeURIComponent(ref.hw_id)}/${ref.task_idx}` },
        `Д/з ${hwDate(ref.hw_id)} №${ref.task_idx + 1}`)));
    article.append(el('div', { class: 'tasks' }, el('strong', { text: 'Упражнения по теме:' }), el('ul', {}, links)));
  }
  article.hidden = false;
}

function renderOccurrence(o) {
  const body = renderMarkdownInto(el('div', { class: 'md' }), o.body_md);

  const photos = o.media.filter((m) => PHOTO_RE.test(m));
  const others = o.media.filter((m) => !photos.includes(m));
  const slides = photos.map((src) => ({ src, md: o.body_md }));
  const thumbs = el('div', { class: 'thumbs' },
    photos.map((src, i) => renderThumb(src, 'фото', slides, i)),
    others.map((src) => fileChip(src, src.split('.').pop().toUpperCase())));

  const src = `Источник: урок ${fmtDate(o.date)}` + (photos.length ? ` · фото ×${photos.length}` : '');
  return el('section', { class: 'occ' },
    el('div', { class: 'occ-head' },
      el('span', { class: 'date', text: fmtDate(o.date, true) }),
      el('span', { class: `badge kind-${o.kind}`, text: KIND_LABELS[o.kind] || o.kind }),
      o.confidence < LOW_CONFIDENCE ? el('span', { class: 'badge warn', text: 'проверить' }) : null),
    body,
    o.media.length ? thumbs : null,
    el('div', { class: 'src', text: src }));
}

// ---------------------------------------------------------------- views: media

// Thumbnail link: opens the lightbox; middle-click / new tab still gets the file.
function renderThumb(src, alt, slides, index) {
  const a = el('a', { href: src, class: 'thumb' }, el('img', { src, loading: 'lazy', alt, width: '64' }));
  a.addEventListener('click', (e) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    openLightbox(slides, index, a);
  });
  return a;
}

function fileChip(src, label) {
  return el('a', { href: src, target: '_blank', rel: 'noopener', class: 'file', text: label });
}

// ---------------------------------------------------------------- views: lightbox

const lightbox = { node: null, slides: [], index: 0, opener: null };

function openLightbox(slides, index, opener) {
  closeLightbox();
  lightbox.slides = slides;
  lightbox.index = index;
  lightbox.opener = opener;
  const close = el('button', { type: 'button', class: 'lb-close', 'aria-label': 'Закрыть', text: '×', onclick: closeLightbox });
  const nav = slides.length > 1 ? el('div', { class: 'lb-nav' },
    el('button', { type: 'button', 'aria-label': 'Предыдущее фото', text: '←', onclick: () => stepLightbox(-1) }),
    el('span', { class: 'lb-count' }),
    el('button', { type: 'button', 'aria-label': 'Следующее фото', text: '→', onclick: () => stepLightbox(1) })) : null;
  lightbox.node = el('div', {
    id: 'lightbox', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Фото задания',
    onclick: (e) => { if (e.target === lightbox.node) closeLightbox(); },
  }, el('div', { class: 'lb-box' },
    el('div', { class: 'lb-img' }, el('img', { alt: 'фото задания' })),
    el('div', { class: 'lb-side' }, el('div', { class: 'lb-bar' }, nav, close), el('div', { class: 'lb-text md' }))));
  document.body.append(lightbox.node);
  document.addEventListener('keydown', onLightboxKey);
  showSlide();
  close.focus();
}

function showSlide() {
  const { node, slides, index } = lightbox;
  const slide = slides[index];
  node.querySelector('.lb-img img').src = slide.src;
  const text = node.querySelector('.lb-text');
  text.replaceChildren();
  if (slide.md) renderMarkdownInto(text, slide.md);
  else text.append(el('p', { class: 'muted', text: 'Нет расшифровки' }));
  const count = node.querySelector('.lb-count');
  if (count) count.textContent = `${index + 1} / ${slides.length}`;
}

function stepLightbox(delta) {
  const n = lightbox.slides.length;
  if (n < 2) return;
  lightbox.index = (lightbox.index + delta + n) % n;
  showSlide();
}

function closeLightbox() {
  if (!lightbox.node) return;
  document.removeEventListener('keydown', onLightboxKey);
  lightbox.node.remove();
  lightbox.node = null;
  if (lightbox.opener && lightbox.opener.isConnected) lightbox.opener.focus();
}

function onLightboxKey(e) {
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft') stepLightbox(-1);
  else if (e.key === 'ArrowRight') stepLightbox(1);
  else if (e.key === 'Tab') trapFocus(e);
  else return;
  e.preventDefault();
}

function trapFocus(e) {
  const items = [...lightbox.node.querySelectorAll('button, a[href]')];
  const i = items.indexOf(document.activeElement);
  const next = e.shiftKey ? (i <= 0 ? items.length - 1 : i - 1) : (i + 1) % items.length;
  items[next].focus();
}

// ---------------------------------------------------------------- views: homework

const DUE_FLAG_TITLE = 'срок рассчитан: следующий урок не найден';

function searchable(str) {
  return stripAccents(normalize(str));
}

function renderHomeworkPane() {
  const list = document.getElementById('hw-list');
  const hws = [...state.site.homework].sort((a, b) => b.assigned.localeCompare(a.assigned));
  state.hwCards = hws.map(renderHomeworkCard);
  list.replaceChildren(...state.hwCards.map((c) => c.card),
    el('p', { class: 'muted', id: 'hw-empty', text: 'Ничего не найдено', hidden: true }));
}

function renderHomeworkCard(hw) {
  const due = el('span', { title: hw.due_flagged ? DUE_FLAG_TITLE : null },
    (hw.due_flagged ? '≈ ' : '') + `Д/з на ${fmtDate(hw.due)}`);
  const rows = hw.tasks.map((t) => ({
    row: renderTask(hw, t),
    text: searchable([t.text, ...t.transcripts.flatMap((tr) => [tr.title, tr.body_md])].join('\n')),
  }));
  const card = el('article', { class: 'hw', id: hw.id },
    el('h3', { class: 'hw-head' }, due, el('span', { class: 'muted', text: `задано ${fmtDate(hw.assigned)}` })),
    rows.map((r) => r.row));
  return { card, rows };
}

function renderTask(hw, task) {
  const key = `done:${task.done_key}`;
  const row = el('div', { class: 'task', id: `${hw.id}-${task.idx}` });
  const box = el('input', { type: 'checkbox', checked: localStorage.getItem(key) === '1' });
  const sync = () => row.classList.toggle('done', box.checked);
  box.addEventListener('change', () => {
    if (box.checked) localStorage.setItem(key, '1');
    else localStorage.removeItem(key);
    sync();
  });
  sync();
  row.append(el('label', { class: 'task-main' }, box,
    el('span', { class: 'tag', text: task.type }), textWithGreek(task.text, 'task-text')));

  // Transcripts follow the photo order for the photos that have one.
  const photos = task.media.filter((m) => PHOTO_RE.test(m));
  const slides = photos.map((src, i) => ({ src, md: task.transcripts[i]?.body_md }));
  const others = task.media.filter((m) => !PHOTO_RE.test(m));
  if (task.media.length) {
    row.append(el('div', { class: 'thumbs' },
      photos.map((src, i) => renderThumb(src, 'фото задания', slides, i)),
      others.map((src) => fileChip(src, /\.pdf$/i.test(src) ? 'PDF' : 'аудио'))));
  }

  if (task.transcripts.length) {
    row.append(el('details', { class: 'tr' }, el('summary', { text: 'Текст задания' }),
      task.transcripts.map((tr) => el('div', { class: 'tr-item' },
        tr.confidence < LOW_CONFIDENCE ? el('span', { class: 'badge warn', text: 'проверить' }) : null,
        renderMarkdownInto(el('div', { class: 'md' }), tr.body_md)))));
  }

  const links = task.topic_ids.map(findTopic).filter(Boolean).map(({ topic }) =>
    el('a', { class: 'ref', href: `#/lessons/topic/${encodeURIComponent(topic.id)}`, text: topic.title }));
  if (links.length) row.append(el('div', { class: 'task-refs' }, links));
  return row;
}

function filterHomework(query) {
  const q = searchable(query.trim());
  let any = false;
  for (const { card, rows } of state.hwCards) {
    let shown = 0;
    for (const { row, text } of rows) {
      const hit = !q || text.includes(q);
      row.hidden = !hit;
      if (hit) shown++;
    }
    card.hidden = shown === 0;
    any ||= shown > 0;
  }
  document.getElementById('hw-empty').hidden = any;
}

function showHomework(hwId, taskIdx) {
  setTab('lessons');
  const search = document.getElementById('hw-search');
  if (search.value) {
    search.value = '';
    filterHomework('');
  }
  const card = document.getElementById(hwId);
  if (!card) return;
  const row = taskIdx != null ? document.getElementById(`${hwId}-${taskIdx}`) : null;
  (row || card).scrollIntoView({ block: 'start' });
  flash(card);
  if (row) flash(row);
}

// ---------------------------------------------------------------- views: dictionary

const POS_FILTERS = [
  { id: 'all', label: 'Все', pos: null },
  { id: 'verb', label: 'Глаголы', pos: ['verb'] },
  { id: 'noun', label: 'Сущ.', pos: ['noun'] },
  { id: 'adj', label: 'Прил.', pos: ['adj'] },
  { id: 'adv', label: 'Наречия', pos: ['adv'] },
  { id: 'phrase', label: 'Фразы', pos: ['phrase', 'other'] },
];
const POS_LABELS = { noun: 'сущ.', verb: 'гл.', adj: 'прил.', adv: 'нареч.', phrase: 'фраза', other: 'др.' };
const GENDER_LABELS = { m: 'м.р.', f: 'ж.р.', n: 'ср.р.' };
const DICT_PAGE = 300;
const MAX_SOURCES = 3;
const FORMS_NOTE = 'формы сгенерированы автоматически, проверяйте по учебнику';

const dict = { built: false, q: '', filter: 'all', sort: 'date', limit: DICT_PAGE, rows: new Map(), text: new Map() };

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

function formStrings(forms) {
  if (forms == null) return [];
  if (typeof forms === 'string') return [forms];
  return Object.values(forms).flatMap(formStrings);
}

function posLabel(entry) {
  let label = POS_LABELS[entry.pos] || entry.pos;
  if (entry.pos === 'noun' && GENDER_LABELS[entry.gender]) label += ` ${GENDER_LABELS[entry.gender]}`;
  return entry.class ? `${label} · ${entry.class}` : label;
}

function sourceLink(s) {
  if (s.kind === 'hw') return el('a', { class: 'ref', href: `#/lessons/hw/${encodeURIComponent(s.id)}`, text: `Д/з ${hwDate(s.id)}` });
  const found = findTopic(s.id);
  return el('a', { class: 'ref', href: `#/lessons/topic/${encodeURIComponent(s.id)}`, text: found ? found.topic.title : s.id });
}

function wordSpan(entry) {
  return el('span', { class: 'w', 'data-key': entry.key, tabindex: '0', text: entry.lemma });
}

function renderDictRow(entry) {
  const extra = entry.sources.length - MAX_SOURCES;
  return el('tr', { id: `w-${entry.key}` },
    el('td', { class: 'd-word' }, wordSpan(entry),
      entry.needs_review ? el('span', { class: 'badge warn', text: 'проверить' }) : null),
    el('td', { text: entry.translation.join(', ') }),
    el('td', {}, el('span', { class: 'pos', text: posLabel(entry) })),
    el('td', {}, el('div', { class: 'd-src' }, entry.sources.slice(0, MAX_SOURCES).map(sourceLink),
      extra > 0 ? el('span', { class: 'muted', text: `+${extra}` }) : null)));
}

function buildDictionary() {
  const view = document.getElementById('view-dictionary');
  const search = el('input', {
    type: 'search', id: 'dict-search', class: 'search', 'aria-label': 'Поиск по словарю',
    placeholder: 'Поиск: δουλειά, работа, έφαγα…',
    oninput: (e) => { dict.q = e.target.value; dict.limit = DICT_PAGE; renderDictRows(); },
  });
  const chips = el('div', { class: 'chips' }, POS_FILTERS.map((f) => el('button', {
    type: 'button', class: 'chip', 'data-filter': f.id, 'aria-pressed': String(f.id === dict.filter), text: f.label,
    onclick: () => { dict.filter = f.id; dict.limit = DICT_PAGE; renderDictRows(); },
  })));
  const sort = el('button', {
    type: 'button', id: 'dict-sort', class: 'chip',
    onclick: () => { dict.sort = dict.sort === 'date' ? 'alpha' : 'date'; renderDictRows(); },
  });
  const table = el('table', { class: 'dict' },
    el('thead', {}, el('tr', {}, ['Слово', 'Перевод', 'Тип', 'Где встречалось'].map((h) => el('th', { text: h })))),
    el('tbody', { id: 'dict-body' }));
  const more = el('button', {
    type: 'button', id: 'dict-more', class: 'chip more', text: 'показать ещё',
    onclick: () => { dict.limit += DICT_PAGE; renderDictRows(); },
  });
  view.replaceChildren(el('section', { class: 'pane dict-pane' },
    el('div', { class: 'dict-bar' }, search, chips, sort, el('span', { id: 'dict-count', class: 'muted' })),
    el('p', { class: 'dict-note muted', text: FORMS_NOTE }),
    el('p', { id: 'dict-msg', class: 'muted', hidden: true }),
    table,
    el('p', { id: 'dict-empty', class: 'muted', text: 'Ничего не найдено', hidden: true }),
    more));
  for (const entry of state.site.dictionary) {
    dict.text.set(entry.key, searchable([entry.lemma, ...entry.translation, ...formStrings(entry.forms)].join('\n')));
  }
  dict.built = true;
}

function dictMatches() {
  const q = searchable(dict.q.trim());
  const pos = POS_FILTERS.find((f) => f.id === dict.filter).pos;
  const list = state.site.dictionary.filter((e) =>
    (!pos || pos.includes(e.pos)) && (!q || dict.text.get(e.key).includes(q)));
  if (dict.sort === 'alpha') list.sort((a, b) => a.lemma.localeCompare(b.lemma, 'el'));
  else list.sort((a, b) => b.first_date.localeCompare(a.first_date) || a.key.localeCompare(b.key, 'el'));
  return list;
}

function renderDictRows() {
  const list = dictMatches();
  const shown = list.slice(0, dict.limit).map((e) => {
    if (!dict.rows.has(e.key)) dict.rows.set(e.key, renderDictRow(e));
    return dict.rows.get(e.key);
  });
  document.getElementById('dict-body').replaceChildren(...shown);
  document.getElementById('dict-count').textContent = `${list.length} ${plural(list.length, 'слово', 'слова', 'слов')}`;
  document.getElementById('dict-empty').hidden = list.length > 0;
  document.getElementById('dict-more').hidden = list.length <= dict.limit;
  for (const chip of document.querySelectorAll('.chips .chip')) {
    chip.setAttribute('aria-pressed', String(chip.dataset.filter === dict.filter));
  }
  document.getElementById('dict-sort').textContent = dict.sort === 'date' ? '↕ по дате' : '↕ по алфавиту';
  return list;
}

function renderDictionary(key) {
  if (!dict.built) buildDictionary();
  const msg = document.getElementById('dict-msg');
  msg.hidden = true;
  if (!key) {
    renderDictRows();
    return;
  }
  dict.q = '';
  dict.filter = 'all';
  document.getElementById('dict-search').value = '';
  const list = dictMatches();
  const idx = list.findIndex((e) => e.key === key);
  if (idx < 0) {
    renderDictRows();
    msg.textContent = 'Слово не найдено';
    msg.hidden = false;
    return;
  }
  dict.limit = Math.max(dict.limit, Math.ceil((idx + 1) / DICT_PAGE) * DICT_PAGE);
  renderDictRows();
  const row = document.getElementById(`w-${key}`);
  row.scrollIntoView({ block: 'center' });
  flash(row);
}

// ---------------------------------------------------------------- hover card

const PERSONS = ['εγώ', 'εσύ', 'αυτός', 'εμείς', 'εσείς', 'αυτοί'];
const TENSES = [['present', 'Настоящее'], ['imperfect', 'Имперфект'], ['aorist', 'Аорист'], ['future', 'Будущее']];
const CASES = [['nom', 'Им.'], ['gen', 'Род.'], ['acc', 'Вин.']];
const SHOW_DELAY = 150;
const HIDE_DELAY = 200;
const CARD_GAP = 6;
const SCROLL_GRACE = 250; // focus scrolls the word into view just after the card shows
const ARTICLE_RE = /^(ο|η|το|οι|τα) /;

const card = { node: null, span: null, key: null, showTimer: 0, hideTimer: 0, overCard: false, shownAt: 0 };

function formRows(pairs) {
  return el('table', {}, el('tbody', {}, pairs.map(([label, form]) =>
    el('tr', {}, el('td', { text: label }), el('td', { text: form || '—' })))));
}

function renderForms(entry) {
  const f = entry.forms || {};
  if (entry.pos === 'noun' && f.sg) {
    return formRows(['sg', 'pl'].flatMap((n) => CASES.map(([c, label]) =>
      [`${label} ${n === 'sg' ? 'ед.' : 'мн.'}`, f[n]?.[c]])));
  }
  if (entry.pos === 'verb' && f.present) {
    const sections = TENSES.filter(([t]) => f[t]).map(([t, title]) => el('div', { class: 'hc-tense' },
      el('div', { class: 'hc-h', text: title }), formRows(PERSONS.map((p, i) => [p, f[t][i]]))));
    if (f.imperative) {
      sections.push(el('div', { class: 'hc-tense' }, el('div', { class: 'hc-h', text: 'Повелительное' }),
        formRows([['εσύ', f.imperative.sg], ['εσείς', f.imperative.pl]])));
    }
    return el('div', { class: 'hc-tenses' }, sections);
  }
  if (entry.pos === 'adj' && f.m) {
    return el('table', {},
      el('thead', {}, el('tr', {}, el('th'), el('th', { text: 'ед.' }), el('th', { text: 'мн.' }))),
      el('tbody', {}, Object.entries(GENDER_LABELS).map(([g, label]) =>
        el('tr', {}, el('td', { text: label }), el('td', { text: f[g]?.sg || '—' }), el('td', { text: f[g]?.pl || '—' })))));
  }
  return null;
}

// Nouns show their article, as stored in the nominative singular («η δουλειά»).
function cardLemma(entry) {
  const nom = entry.pos === 'noun' ? entry.forms?.sg?.nom : null;
  return nom && ARTICLE_RE.test(nom) ? nom : entry.lemma;
}

function renderCard(entry) {
  const meta = [POS_LABELS[entry.pos] || entry.pos, GENDER_LABELS[entry.gender], entry.class].filter(Boolean).join(' · ');
  const ex = entry.example?.el ? el('div', { class: 'hc-ex', text: `${entry.example.el} — ${entry.example.ru || ''}` }) : null;
  const topic = entry.sources.find((s) => s.kind === 'topic');
  return [
    el('div', { class: 'hc-lemma', text: cardLemma(entry) }),
    el('div', { class: 'hc-tr', text: entry.translation.join(', ') }),
    el('div', { class: 'hc-meta', text: meta }),
    renderForms(entry),
    ex,
    el('div', { class: 'hc-links' },
      el('a', { class: 'ref', href: `#/dictionary/${encodeURIComponent(entry.key)}`, text: 'открыть в словаре' }),
      topic ? sourceLink(topic) : null),
    el('div', { class: 'hc-note muted', text: FORMS_NOTE }),
  ];
}

function cardNode() {
  if (card.node) return card.node;
  card.node = el('div', {
    id: 'hovercard', role: 'dialog', hidden: true,
    onmouseenter: () => { card.overCard = true; clearTimeout(card.hideTimer); },
    onmouseleave: () => { card.overCard = false; scheduleHide(); },
    onclick: (e) => { if (e.target.closest('a')) hideCard(); },
  });
  document.body.append(card.node);
  return card.node;
}

function showCard(span, key) {
  clearTimeout(card.showTimer);
  clearTimeout(card.hideTimer);
  const entry = state.dictByKey.get(key);
  if (!entry) return;
  const node = cardNode();
  if (card.span !== span || node.hidden) {
    node.replaceChildren(...renderCard(entry).filter(Boolean));
    node.classList.toggle('wide', entry.pos === 'verb');
    node.setAttribute('aria-label', entry.lemma);
  }
  card.span = span;
  card.key = key;
  card.shownAt = performance.now();
  node.hidden = false;
  placeCard(span, node);
}

// Below-right of the word; flips above / left when it would leave the viewport.
// When neither side fits, the roomier side wins and the card scrolls.
function placeCard(span, node) {
  node.style.maxHeight = '';
  const r = span.getBoundingClientRect();
  const w = node.offsetWidth, h = node.offsetHeight;
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const below = vh - r.bottom - 2 * CARD_GAP, above = r.top - 2 * CARD_GAP;
  const flip = h > below && above > below;
  if (h > (flip ? above : below)) node.style.maxHeight = `${flip ? above : below}px`;
  const top = flip ? r.top - CARD_GAP - Math.min(h, above) : r.bottom + CARD_GAP;
  let left = r.left;
  if (left + w > vw) left = Math.max(CARD_GAP, r.right - w);
  node.style.top = `${top + window.scrollY}px`;
  node.style.left = `${left + window.scrollX}px`;
}

function hideCard() {
  clearTimeout(card.showTimer);
  clearTimeout(card.hideTimer);
  card.overCard = false;
  card.span = null;
  if (card.node) card.node.hidden = true;
}

function scheduleHide() {
  clearTimeout(card.hideTimer);
  card.hideTimer = setTimeout(() => { if (!card.overCard) hideCard(); }, HIDE_DELAY);
}

function cardOpenFor(span) {
  return card.node && !card.node.hidden && card.span === span;
}

const noHover = matchMedia('(hover: none)');
let lastPointer = 'mouse';

function isTouch() {
  return lastPointer === 'touch' || noHover.matches;
}

// Mouse hover: show after a short delay (touch devices use tap instead).
function onGreekWordHover(span, key, event) {
  if (isTouch()) return;
  clearTimeout(card.hideTimer);
  if (cardOpenFor(span)) return;
  clearTimeout(card.showTimer);
  card.showTimer = setTimeout(() => showCard(span, key), SHOW_DELAY);
}

function bindHoverCard() {
  document.addEventListener('pointerdown', (e) => { lastPointer = e.pointerType; }, true);
  document.addEventListener('mouseout', (e) => {
    const span = e.target.closest && e.target.closest('.w');
    if (!span || span.contains(e.relatedTarget)) return;
    clearTimeout(card.showTimer);
    if (card.node && !card.node.hidden) scheduleHide();
  });
  document.addEventListener('click', (e) => {
    const span = e.target.closest && e.target.closest('.w');
    if (span) {
      // A word inside a homework <label> must not toggle the checkbox.
      if (span.closest('label')) { e.preventDefault(); e.stopPropagation(); }
      if (isTouch()) {
        if (cardOpenFor(span)) hideCard();
        else showCard(span, span.dataset.key);
      }
      return;
    }
    if (card.node && !card.node.hidden && !card.node.contains(e.target)) hideCard();
  });
  document.addEventListener('focusin', (e) => {
    const span = e.target.closest && e.target.closest('.w');
    // Keyboard focus only; mouse clicks and taps are handled by hover / click.
    if (span) {
      if (span.matches(':focus-visible')) showCard(span, span.dataset.key);
    } else if (card.node && !card.node.contains(e.target)) clearTimeout(card.showTimer);
  });
  document.addEventListener('focusout', (e) => {
    if (!e.target.closest || !e.target.closest('.w, #hovercard')) return;
    if (card.node && card.node.contains(e.relatedTarget)) return;
    if (!card.overCard) scheduleHide();
  });
  // Registered before the lightbox's listener: Esc closes the card first.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !card.node || card.node.hidden) return;
    const span = card.span;
    hideCard();
    if (span && span.isConnected && card.node.contains(document.activeElement)) span.focus();
    e.stopImmediatePropagation();
    e.preventDefault();
  });
  // Panes scroll independently; a stale card would float over the wrong text.
  document.addEventListener('scroll', (e) => {
    if (!card.node || card.node.hidden || card.node.contains(e.target)) return;
    if (performance.now() - card.shownAt < SCROLL_GRACE) return;
    hideCard();
  }, true);
}

// ---------------------------------------------------------------- events + boot

function bindEvents() {
  window.addEventListener('hashchange', route);
  document.getElementById('search').addEventListener('input', (e) => {
    state.query = e.target.value;
    renderTopicList();
  });
  document.getElementById('hw-search').addEventListener('input', (e) => filterHomework(e.target.value));
  // Same-hash clicks fire no hashchange; re-route so the target scrolls and flashes again.
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a.ref[href^="#/"]');
    if (!a || location.hash !== a.getAttribute('href')) return;
    e.preventDefault();
    route();
  });
  document.addEventListener('mouseover', (e) => {
    const span = e.target.closest && e.target.closest('.w');
    if (span && !span.contains(e.relatedTarget)) window.GREEK.onGreekWordHover(span, span.dataset.key, e);
  });
  bindHoverCard();
}

async function boot() {
  try {
    const res = await fetch('content/site.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.site = await res.json();
  } catch (err) {
    document.getElementById('topic-list').append(
      el('p', { class: 'muted', text: `Не удалось загрузить данные: ${err.message}` }));
    return;
  }
  state.formsIndex = state.site.forms_index || {};
  state.dictByKey = new Map((state.site.dictionary || []).map((e) => [e.key, e]));
  window.GREEK = { site: state.site, formsIndex: state.formsIndex, renderMarkdown, normalize, lookupKey, onGreekWordHover };
  document.getElementById('updated').textContent = `обновлено ${fmtDate(state.site.updated, true)}`;

  for (const g of state.site.grammar.groups) state.openGroups.add(g.id);
  state.openGroups.delete(AUDIO_GROUP); // 16 players: collapsed until asked for
  buildSearchIndex();
  bindEvents();
  renderTopicList();
  renderHomeworkPane();
  route();
}

boot();
