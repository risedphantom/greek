'use strict';

// ---------------------------------------------------------------- state

const state = {
  site: null,
  formsIndex: {},
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
      frag.append(el('span', { class: 'w', 'data-key': key, text: m[0] }));
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
  card.scrollIntoView({ block: 'start' });
  flash(card);
  const row = taskIdx != null && document.getElementById(`${hwId}-${taskIdx}`);
  if (row) flash(row);
}

// ---------------------------------------------------------------- views: dictionary (Task 12)

function renderDictionary(key) {
  const view = document.getElementById('view-dictionary');
  view.replaceChildren(el('section', { class: 'pane' }, el('h2', { text: 'Словарь — скоро' })));
}

// Hover hook for Greek word spans; Task 12 replaces with hover cards.
function onGreekWordHover(span, key, event) {}

// ---------------------------------------------------------------- events + boot

function bindEvents() {
  window.addEventListener('hashchange', route);
  document.getElementById('search').addEventListener('input', (e) => {
    state.query = e.target.value;
    renderTopicList();
  });
  document.getElementById('hw-search').addEventListener('input', (e) => filterHomework(e.target.value));
  document.addEventListener('mouseover', (e) => {
    const span = e.target.closest && e.target.closest('.w');
    if (span) window.GREEK.onGreekWordHover(span, span.dataset.key, e);
  });
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
