'use strict';

// ---------------------------------------------------------------- state

const state = {
  site: null,
  formsIndex: {},
  query: '',
  topicId: null,
  openGroups: new Set(),
  topicText: new Map(), // topic id -> lowercased searchable text
};

const KIND_LABELS = { grammar: 'правило', table: 'таблица', vocab: 'лексика', text: 'текст' };
const LOW_CONFIDENCE = 0.7;
const AUDIO_GROUP = 'audio';

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

function renderMarkdown(md) {
  const tpl = document.createElement('template');
  tpl.innerHTML = marked.parse(md || '', { gfm: true, breaks: true });
  wrapGreek(tpl.content);
  return tpl.innerHTML;
}

// ---------------------------------------------------------------- router

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  if (parts[0] === 'dictionary') return { tab: 'dictionary', key: parts[1] || null };
  if (parts[1] === 'topic' && parts[2]) return { tab: 'lessons', topicId: parts[2] };
  if (parts[1] === 'hw' && parts[2]) return { tab: 'lessons', hwId: parts[2] };
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
  if (r.hwId) showHomework(r.hwId);
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
      el('a', { class: 'ref', href: `#/lessons/hw/${encodeURIComponent(ref.hw_id)}` },
        `Д/з ${hwDate(ref.hw_id)} №${ref.task_idx + 1}`)));
    article.append(el('div', { class: 'tasks' }, el('strong', { text: 'Упражнения по теме:' }), el('ul', {}, links)));
  }
  article.hidden = false;
}

function renderOccurrence(o) {
  const body = el('div', { class: 'md' });
  body.innerHTML = renderMarkdown(o.body_md); // marked output from our own content

  const photos = o.media.filter((m) => /\.(jpe?g|png|webp|gif)$/i.test(m));
  const others = o.media.filter((m) => !photos.includes(m));
  const thumbs = el('div', { class: 'thumbs' },
    photos.map((src) => el('a', { href: src, target: '_blank', rel: 'noopener', class: 'thumb' },
      el('img', { src, loading: 'lazy', alt: 'фото', width: '64' }))),
    others.map((src) => el('a', { href: src, target: '_blank', rel: 'noopener', class: 'file' },
      src.split('.').pop().toUpperCase())));

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

// ---------------------------------------------------------------- views: homework (Task 11)

function renderHomeworkPane() {
  // Task 11 renders site.homework into #hw-pane.
}

function showHomework(hwId) {
  // Task 11 scrolls to the homework / task.
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
