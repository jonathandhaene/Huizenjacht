/**
 * Huizenjacht — collaborative mobile web app
 *
 * Data files (in docs/data/, committed to GitHub):
 *   properties.json  – array of property objects, updated daily by GitHub Actions
 *   likes.json       – { propertyId: { userName: isoTimestamp, … }, … }
 *   annotations.json – { "_meta": { tags: [] }, propertyId: { notes: [], tags: [] }, … }
 *
 * All three files are read/written via the GitHub Contents API.
 * A Personal Access Token (PAT) with "contents: write" is required.
 * The PAT is stored only in localStorage and never leaves the browser.
 */

'use strict';

// ── Configuration ─────────────────────────────────────────────────────────────
const CONFIG = {
  owner:  'jonathandhaene',
  repo:   'Huizenjacht',
  branch: 'main',
  propertiesPath:  'docs/data/properties.json',
  likesPath:       'docs/data/likes.json',
  annotationsPath: 'docs/data/annotations.json',
  basePath: '/Huizenjacht',
};

const DEFAULT_TAGS = [
  '✨ Droomhuis', '📅 Bezoeken', '💰 Goed budget',
  '🔨 Renovatie nodig', '🐄 Dieren mogelijk', '🏡 B&B potentieel',
  '🌿 Mooie tuin', '🤔 Twijfel', '❌ Nee',
];

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  user:              null,   // { name, token }
  properties:        [],
  likes:             {},
  likesSha:          null,
  annotations:       {},     // includes _meta.tags + per-property { notes, tags }
  annotationsSha:    null,
  activeTab:         'properties',
  activeFilter:      'all',
  activeTagFilter:   null,   // tag string or null
  loading:           false,
};

// ── GitHub API helpers ────────────────────────────────────────────────────────
const GitHub = {
  async getFile(path, token) {
    const url = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/contents/${path}?ref=${CONFIG.branch}`;
    const headers = { Accept: 'application/vnd.github+json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
    return res.json();
  },

  decode(file) {
    return JSON.parse(decodeURIComponent(escape(atob(file.content.replace(/\n/g, '')))));
  },

  async updateFile(path, data, sha, message, token) {
    const url = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/contents/${path}`;
    const body = JSON.stringify({
      message,
      content: btoa(unescape(encodeURIComponent(JSON.stringify(data, null, 2)))),
      sha,
      branch: CONFIG.branch,
    });
    const res = await fetch(url, {
      method: 'PUT',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.message || `GitHub API ${res.status}`);
    }
    const result = await res.json();
    return result.content.sha;
  },

  async fetchRaw(path) {
    const url = `https://raw.githubusercontent.com/${CONFIG.owner}/${CONFIG.repo}/${CONFIG.branch}/${path}?_=${Date.now()}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Raw fetch ${res.status}`);
    return res.json();
  },
};

// ── Local storage ─────────────────────────────────────────────────────────────
const Store = {
  get(key)      { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
  set(key, val) { localStorage.setItem(key, JSON.stringify(val)); },
  remove(key)   { localStorage.removeItem(key); },
};

// ── UI helpers ────────────────────────────────────────────────────────────────
function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function showToast(msg, duration = 2500) {
  $$('.toast').forEach(t => t.remove());
  const t = el('div', 'toast', msg);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), duration);
}

function setLoading(on) {
  state.loading = on;
  const btn = $('#refresh-btn');
  if (btn) btn.style.opacity = on ? '0.5' : '1';
}

// ── Initialisation ────────────────────────────────────────────────────────────
async function init() {
  const saved = Store.get('huizenjacht_user');
  if (saved && saved.name && saved.token) {
    state.user = saved;
    await startApp();
  } else {
    showSetupScreen();
  }
}

function showSetupScreen() {
  $('#loading-screen').classList.add('hidden');
  $('#main-screen').classList.add('hidden');
  $('#setup-screen').classList.remove('hidden');
}

async function startApp() {
  $('#loading-screen').classList.remove('hidden');
  $('#setup-screen').classList.add('hidden');
  $('#main-screen').classList.add('hidden');

  const pill = $('#user-pill');
  if (pill) pill.textContent = state.user.name;

  try {
    await loadData();
    renderCurrentTab();
    updateMatchBadge();
  } catch (err) {
    console.error(err);
    showToast('⚠️ Kon gegevens niet laden — controleer je verbinding');
  }

  $('#loading-screen').classList.add('hidden');
  $('#main-screen').classList.remove('hidden');
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadData() {
  try {
    state.properties = await GitHub.fetchRaw(CONFIG.propertiesPath);
  } catch {
    state.properties = [];
  }

  try {
    const file = await GitHub.getFile(CONFIG.likesPath, state.user.token);
    state.likes    = GitHub.decode(file);
    state.likesSha = file.sha;
  } catch {
    state.likes    = {};
    state.likesSha = null;
  }

  try {
    const file = await GitHub.getFile(CONFIG.annotationsPath, state.user.token);
    state.annotations    = GitHub.decode(file);
    state.annotationsSha = file.sha;
  } catch {
    state.annotations    = { _meta: { tags: [...DEFAULT_TAGS] } };
    state.annotationsSha = null;
  }

  // Ensure _meta exists
  if (!state.annotations._meta) state.annotations._meta = { tags: [...DEFAULT_TAGS] };
  if (!state.annotations._meta.tags) state.annotations._meta.tags = [...DEFAULT_TAGS];
}

async function refreshData() {
  if (state.loading) return;
  setLoading(true);
  try {
    await loadData();
    renderCurrentTab();
    updateMatchBadge();
    showToast('✅ Bijgewerkt');
  } catch {
    showToast('⚠️ Kon niet bijwerken');
  } finally {
    setLoading(false);
  }
}

// ── Like logic ────────────────────────────────────────────────────────────────
async function toggleLike(propertyId) {
  if (!state.user) return;
  const name = state.user.name;
  const alreadyLiked = state.likes[propertyId]?.[name];

  if (!state.likes[propertyId]) state.likes[propertyId] = {};
  if (alreadyLiked) {
    delete state.likes[propertyId][name];
    if (Object.keys(state.likes[propertyId]).length === 0) delete state.likes[propertyId];
    showToast('💔 Like verwijderd');
  } else {
    state.likes[propertyId][name] = new Date().toISOString();
    showToast('❤️ Geliked!');
    const likeCount = Object.keys(state.likes[propertyId]).length;
    if (likeCount >= 2) {
      const prop = state.properties.find(p => p.id === propertyId);
      showMatchCelebration(prop);
    }
  }

  renderCurrentTab();
  updateMatchBadge();

  if (!state.user.token) {
    showToast('⚠️ Geen token — likes lokaal bewaard');
    return;
  }
  try {
    state.likesSha = await persistLikes();
  } catch (err) {
    console.error('Like persist failed:', err);
    if (err.message.includes('409') || err.message.includes('conflict')) {
      showToast('🔄 Conflict — opnieuw proberen…');
      await loadData();
      await toggleLike(propertyId);
    } else {
      showToast('⚠️ Like kon niet opgeslagen worden');
    }
  }
}

async function persistLikes() {
  return GitHub.updateFile(
    CONFIG.likesPath,
    state.likes,
    state.likesSha,
    `❤️ ${state.user.name} liked a property`,
    state.user.token,
  );
}

// ── Annotations helpers ───────────────────────────────────────────────────────
function getAnnotations(propertyId) {
  const a = state.annotations[propertyId] || {};
  return { notes: a.notes || [], tags: a.tags || [] };
}

function getAvailableTags() {
  return state.annotations._meta?.tags || DEFAULT_TAGS;
}

async function persistAnnotations(commitMsg) {
  if (!state.user?.token) return;
  try {
    state.annotationsSha = await GitHub.updateFile(
      CONFIG.annotationsPath,
      state.annotations,
      state.annotationsSha,
      commitMsg || `📝 ${state.user.name} updated annotations`,
      state.user.token,
    );
  } catch (err) {
    console.error('Annotations persist failed:', err);
    showToast('⚠️ Kon annotatie niet opslaan');
  }
}

// ── Note CRUD ─────────────────────────────────────────────────────────────────
async function addNote(propertyId, text) {
  if (!text.trim()) return;
  if (!state.annotations[propertyId]) state.annotations[propertyId] = { notes: [], tags: [] };
  if (!state.annotations[propertyId].notes) state.annotations[propertyId].notes = [];
  state.annotations[propertyId].notes.push({
    user: state.user.name,
    text: text.trim(),
    ts: new Date().toISOString(),
  });
  await persistAnnotations(`📝 ${state.user.name} added a note`);
  showToast('📝 Notitie opgeslagen');
}

async function deleteNote(propertyId, index) {
  const ann = state.annotations[propertyId];
  if (!ann?.notes?.[index]) return;
  ann.notes.splice(index, 1);
  await persistAnnotations(`🗑️ ${state.user.name} deleted a note`);
  showToast('🗑️ Notitie verwijderd');
}

// ── Tag CRUD ──────────────────────────────────────────────────────────────────
async function toggleTag(propertyId, tag) {
  if (!state.annotations[propertyId]) state.annotations[propertyId] = { notes: [], tags: [] };
  if (!state.annotations[propertyId].tags) state.annotations[propertyId].tags = [];
  const tags = state.annotations[propertyId].tags;
  const idx = tags.indexOf(tag);
  if (idx >= 0) {
    tags.splice(idx, 1);
  } else {
    tags.push(tag);
  }
  await persistAnnotations(`🏷️ ${state.user.name} updated tags`);
}

async function addAvailableTag(tag) {
  tag = tag.trim();
  if (!tag) return;
  if (!state.annotations._meta) state.annotations._meta = { tags: [...DEFAULT_TAGS] };
  if (!state.annotations._meta.tags.includes(tag)) {
    state.annotations._meta.tags.push(tag);
    await persistAnnotations(`🏷️ ${state.user.name} added tag "${tag}"`);
  }
}

async function removeAvailableTag(tag) {
  if (!state.annotations._meta?.tags) return;
  state.annotations._meta.tags = state.annotations._meta.tags.filter(t => t !== tag);
  // Also remove from all properties
  Object.values(state.annotations).forEach(a => {
    if (a && Array.isArray(a.tags)) {
      a.tags = a.tags.filter(t => t !== tag);
    }
  });
  await persistAnnotations(`🗑️ ${state.user.name} removed tag "${tag}"`);
}

// ── Match helpers ─────────────────────────────────────────────────────────────
function getMatchedPropertyIds() {
  return Object.entries(state.likes)
    .filter(([, users]) => Object.keys(users).length >= 2)
    .map(([id]) => id);
}
function isLikedByMe(propertyId) {
  return !!(state.user && state.likes[propertyId]?.[state.user.name]);
}
function isMatched(propertyId) {
  return getMatchedPropertyIds().includes(propertyId);
}
function updateMatchBadge() {
  const count = getMatchedPropertyIds().length;
  const badge = $('#matches-count');
  if (!badge) return;
  badge.textContent = count;
  badge.classList.toggle('hidden', count === 0);
}

function showMatchCelebration(prop) {
  const overlay = el('div', 'match-celebration');
  overlay.innerHTML = `
    <div class="celeb-icon">🎉</div>
    <h2>Het is een Match!</h2>
    <p>${prop ? `<strong>${esc(prop.title)}</strong><br>` : ''}Jullie vinden dit allebei geweldig!</p>
    <button onclick="this.parentElement.remove()">Bekijk het pand</button>`;
  document.body.appendChild(overlay);
  setTimeout(() => overlay.remove(), 6000);
}

// ── Tab navigation ────────────────────────────────────────────────────────────
function showTab(tab) {
  state.activeTab = tab;
  $$('.tab-content').forEach(t => t.classList.add('hidden'));
  $$('.nav-btn').forEach(b => b.classList.remove('active'));
  $(`#${tab}-tab`).classList.remove('hidden');
  $(`#nav-${tab}`).classList.add('active');
  renderCurrentTab();
}

function renderCurrentTab() {
  if (state.activeTab === 'properties') renderProperties();
  else if (state.activeTab === 'matches')    renderMatches();
  else if (state.activeTab === 'settings')   renderSettings();
}

// ── Filter ────────────────────────────────────────────────────────────────────
function setFilter(filter) {
  state.activeFilter    = filter;
  state.activeTagFilter = null;
  $$('.filter-chip').forEach(c => c.classList.toggle('active', c.dataset.filter === filter));
  renderProperties();
}

function setTagFilter(tag) {
  state.activeTagFilter = (state.activeTagFilter === tag) ? null : tag;
  state.activeFilter    = state.activeTagFilter ? 'tag' : 'all';
  $$('.filter-chip').forEach(c => {
    c.classList.toggle('active',
      state.activeTagFilter
        ? c.dataset.tag === tag
        : c.dataset.filter === 'all'
    );
  });
  renderProperties();
}

function filteredProperties() {
  let props = state.properties;
  if (state.activeTagFilter) {
    props = props.filter(p => (getAnnotations(p.id).tags || []).includes(state.activeTagFilter));
  } else {
    switch (state.activeFilter) {
      case 'liked':   props = props.filter(p => isLikedByMe(p.id)); break;
      case 'matched': props = props.filter(p => isMatched(p.id)); break;
      case 'noted':   props = props.filter(p => getAnnotations(p.id).notes.length > 0); break;
      case 'new': {
        const today = new Date(); today.setHours(0,0,0,0);
        props = props.filter(p => p.first_seen && new Date(p.first_seen) >= today);
        break;
      }
    }
  }
  return props;
}

// ── Render: Properties tab ────────────────────────────────────────────────────
function renderProperties() {
  const container = $('#properties-list');
  if (!container) return;

  const props = filteredProperties();
  if (props.length === 0) {
    container.innerHTML = '';
    container.appendChild(emptyState(
      state.activeFilter === 'liked'   ? '💔' :
      state.activeFilter === 'matched' ? '🤝' :
      state.activeFilter === 'noted'   ? '📝' :
      state.activeFilter === 'new'     ? '🔍' :
      state.activeTagFilter            ? '🏷️' : '🏡',
      state.activeFilter === 'all' && !state.activeTagFilter
        ? 'Nog geen panden gevonden.<br>De dagelijkse scan loopt elke ochtend om 07:00.'
        : 'Geen panden in deze categorie.'
    ));
    return;
  }

  container.innerHTML = '';
  props.forEach(prop => container.appendChild(propertyCard(prop)));
}

function propertyCard(prop) {
  const liked   = isLikedByMe(prop.id);
  const matched = isMatched(prop.id);
  const score   = prop.ai_analysis?.score;
  const isNew   = isNewToday(prop.first_seen);
  const ann     = getAnnotations(prop.id);
  const hasNote = ann.notes.length > 0;
  const others  = matched
    ? Object.keys(state.likes[prop.id] || {}).filter(n => n !== state.user?.name)
    : [];

  const tagsHtml = ann.tags.length
    ? `<div class="card-tags">${ann.tags.map(t => `<span class="tag-chip">${esc(t)}</span>`).join('')}</div>`
    : '';

  // Risk chip — only show if there's at least one medium/high risk
  const riskLevel = worstRiskLevel(prop.government_data);
  const riskChipHtml = (riskLevel === 'high' || riskLevel === 'medium')
    ? `<span class="risk-chip risk-${riskLevel}">${riskLevel === 'high' ? '🔴 Risico' : '🟡 Risico'}</span>`
    : '';

  const card = el('div', `property-card${matched ? ' matched' : liked ? ' liked' : ''}`);
  card.innerHTML = `
    <div class="card-image">
      ${prop.images?.length
        ? `<img src="${esc(prop.images[0])}" alt="${esc(prop.title)}" loading="lazy">`
        : `<div class="card-image-placeholder"><span class="placeholder-icon">🏡</span><span>Geen foto</span></div>`}
      <div class="card-badges">
        <span class="card-badge badge-source">${esc(prop.source)}</span>
        ${isNew   ? '<span class="card-badge badge-new">Nieuw</span>' : ''}
        ${matched ? '<span class="card-badge badge-match">🎉 Match!</span>' : ''}
        ${hasNote ? '<span class="card-badge badge-noted">📝</span>' : ''}
      </div>
      ${riskChipHtml}
      ${score != null ? `<span class="score-badge ${scoreCls(score)}">⭐ ${score}/10</span>` : ''}
    </div>
    <div class="card-body">
      <div class="card-title">${esc(prop.title)}</div>
      ${prop.municipality ? `<div class="card-location">📍 ${esc(prop.municipality)}${prop.postal_code ? ` ${esc(prop.postal_code)}` : ''}</div>` : ''}
      <div class="card-price">${prop.price ? `€ ${fmtPrice(prop.price)}` : 'Prijs op aanvraag'}</div>
      <div class="card-stats">
        ${prop.bedrooms    ? `<span class="card-stat">🛏 ${prop.bedrooms} slpk</span>` : ''}
        ${prop.land_area   ? `<span class="card-stat">🌿 ${fmtArea(prop.land_area)}</span>` : ''}
        ${prop.living_area ? `<span class="card-stat">🏠 ${fmtArea(prop.living_area)}</span>` : ''}
      </div>
      ${tagsHtml}
      ${matched && others.length ? `<div style="font-size:.8rem;color:var(--rose);font-weight:600;margin-top:6px">❤️ Ook geliked door ${esc(others.join(' & '))}</div>` : ''}
    </div>
    <div class="card-footer">
      <button class="btn-detail" onclick="showDetail('${esc(prop.id)}')">Meer info ▶</button>
      <button class="btn-like ${liked ? 'liked' : ''}" onclick="handleLike(event,'${esc(prop.id)}')" aria-label="Like">
        <span class="heart">${liked ? '❤️' : '🤍'}</span>
      </button>
    </div>`;
  return card;
}

// ── Render: Matches tab ───────────────────────────────────────────────────────
function renderMatches() {
  const container = $('#matches-list');
  if (!container) return;

  const matchedIds   = getMatchedPropertyIds();
  const matchedProps = matchedIds.map(id => state.properties.find(p => p.id === id)).filter(Boolean);

  if (matchedProps.length === 0) {
    container.innerHTML = '';
    container.appendChild(emptyState('💞',
      'Nog geen matches.<br>Like een pand en zie of jullie het allebei geweldig vinden!'));
    return;
  }

  container.innerHTML = `<div class="match-header">🎉 ${matchedProps.length} match${matchedProps.length !== 1 ? 'es' : ''} gevonden!</div>`;
  matchedProps.forEach(prop => {
    const card = propertyCard(prop);
    const body = card.querySelector('.card-body');
    const userLikes = state.likes[prop.id] || {};
    const likedByHtml = Object.entries(userLikes)
      .map(([name, ts]) => `<strong>${esc(name)}</strong> op ${fmtDate(ts)}`)
      .join(' & ');
    body.appendChild(el('div', 'match-liked-by', `❤️ Geliked door: ${likedByHtml}`));
    container.appendChild(card);
  });
}

// ── Render: Settings tab ──────────────────────────────────────────────────────
function renderSettings() {
  const container = $('#settings-list');
  if (!container) return;
  const user = state.user || {};
  const tags = getAvailableTags();

  const tagsHtml = tags.map(t => `
    <span class="tag-manage-item">
      ${esc(t)}
      <button class="tag-del" onclick="handleDeleteAvailableTag('${esc(t)}')" aria-label="Verwijder tag">×</button>
    </span>`).join('');

  container.innerHTML = `
    <p class="settings-section-title">Mijn profiel</p>
    <div class="settings-list">
      <div class="settings-item">
        <div class="settings-item-left"><h4>Naam</h4><p>Wordt getoond bij likes & notities</p></div>
        <input id="s-name" type="text" value="${esc(user.name || '')}" placeholder="Jouw naam">
      </div>
      <div class="settings-item">
        <div class="settings-item-left">
          <h4>GitHub Token</h4><p>Vereist om te liken & noteren</p>
        </div>
        <input id="s-token" type="password" value="${user.token || ''}" placeholder="ghp_…" autocomplete="new-password">
      </div>
    </div>
    <p class="settings-note">
      Maak een token aan via
      <a href="https://github.com/settings/tokens/new?scopes=repo&description=Huizenjacht" target="_blank" rel="noopener">
        GitHub → Settings → Developer settings → Personal access tokens
      </a> (bereik <strong>repo</strong>).
    </p>
    <button class="btn-save" onclick="saveSettings()">Opslaan</button>
    <button class="btn-logout" onclick="logout()">Uitloggen</button>

    <p class="settings-section-title" style="margin-top:20px">Tags beheren</p>
    <p class="settings-note">Beschikbare tags die je aan panden kan toevoegen. Tap × om een tag te verwijderen.</p>
    <div class="tag-manage-list">${tagsHtml}</div>
    <div class="new-tag-row" style="margin-top:10px">
      <input class="new-tag-input" id="s-new-tag" type="text" placeholder="Nieuw tag…" maxlength="40">
      <button class="btn-add-tag" onclick="handleAddAvailableTag()">Toevoegen</button>
    </div>

    <p class="settings-section-title" style="margin-top:24px">Info</p>
    <p class="settings-note">
      ${state.properties.length} panden geladen &nbsp;·&nbsp;
      ${Object.keys(state.likes).length} geliked &nbsp;·&nbsp;
      ${getMatchedPropertyIds().length} matches<br>
      Data bijgewerkt door GitHub Actions elke ochtend om 07:00.
    </p>`;
}

function saveSettings() {
  const name  = $('#s-name')?.value.trim();
  const token = $('#s-token')?.value.trim();
  if (!name) { showToast('⚠️ Naam is verplicht'); return; }
  state.user = { name, token: token || '' };
  Store.set('huizenjacht_user', state.user);
  const pill = $('#user-pill');
  if (pill) pill.textContent = name;
  showToast('✅ Instellingen opgeslagen');
}

function logout() {
  if (!confirm('Ben je zeker dat je wil uitloggen?')) return;
  Store.remove('huizenjacht_user');
  state.user = null;
  state.likes = {};
  state.annotations = {};
  location.reload();
}

async function handleAddAvailableTag() {
  const input = $('#s-new-tag');
  const tag   = input?.value.trim();
  if (!tag) { showToast('⚠️ Voer een tagnaam in'); return; }
  if (getAvailableTags().includes(tag)) { showToast('⚠️ Tag bestaat al'); return; }
  await addAvailableTag(tag);
  if (input) input.value = '';
  renderSettings();
  showToast('🏷️ Tag toegevoegd');
}

async function handleDeleteAvailableTag(tag) {
  if (!confirm(`Tag "${tag}" verwijderen?`)) return;
  await removeAvailableTag(tag);
  renderSettings();
  showToast('🗑️ Tag verwijderd');
}

// ── Detail modal ──────────────────────────────────────────────────────────────
function showDetail(propertyId) {
  const prop = state.properties.find(p => p.id === propertyId);
  if (!prop) return;

  $$('.modal-overlay').forEach(m => m.remove());

  const liked = isLikedByMe(propertyId);
  const score = prop.ai_analysis?.score;
  const gov   = prop.government_data;
  const ann   = getAnnotations(propertyId);

  const overlay = el('div', 'modal-overlay');
  overlay.addEventListener('click', e => { if (e.target === overlay) closeModal(); });

  const sheet = el('div', 'modal-sheet');
  sheet.innerHTML = `
    <div class="modal-handle"></div>
    <div class="modal-header">
      <h2>${esc(prop.title)}</h2>
      <button class="btn-close" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-body">
      ${prop.images?.length
        ? `<div class="gallery">${prop.images.map(u => `<img src="${esc(u)}" alt="" loading="lazy">`).join('')}</div>`
        : ''}

      <!-- Price & location -->
      <div class="detail-section">
        <div class="detail-price">${prop.price ? `€ ${fmtPrice(prop.price)}` : 'Prijs op aanvraag'}</div>
        <div class="detail-meta">📍 ${esc([prop.address, prop.postal_code, prop.municipality].filter(Boolean).join(', ') || 'Onbekende locatie')}</div>
        <div class="detail-stats">
          ${statBox(prop.bedrooms    ? `${prop.bedrooms}` : '—', 'Slaapkamers')}
          ${statBox(prop.land_area   ? fmtArea(prop.land_area)   : '—', 'Perceel')}
          ${statBox(prop.living_area ? fmtArea(prop.living_area) : '—', 'Bewoonbaar')}
        </div>
        ${prop.features?.length ? `<div style="margin-top:10px;font-size:.82rem;color:var(--stone-600)">${prop.features.slice(0,8).map(f => `<span style="display:inline-block;background:var(--stone-100);border-radius:4px;padding:2px 8px;margin:2px">${esc(f)}</span>`).join('')}</div>` : ''}
      </div>

      <!-- Tags -->
      <div class="detail-section" id="modal-tags-section">
        <h3>Tags</h3>
        ${renderTagsSection(propertyId)}
      </div>

      <!-- Risks -->
      ${gov ? `
      <div class="detail-section">
        <h3>⚠️ Risico's</h3>
        ${renderRisksHtml(gov)}
        ${gov.source_url ? `<div style="margin-top:12px"><a href="${esc(gov.source_url)}" target="_blank" rel="noopener" class="risk-source">📍 Bekijk op Geopunt</a></div>` : ''}
      </div>` : ''}

      <!-- Planning & vergunningen -->
      ${gov ? `
      <div class="detail-section">
        <h3>Planning & vergunningen</h3>
        <div class="gov-grid">
          ${gov.zoning               != null ? govItem('Bestemmingszone', gov.zoning, null) : ''}
          ${gov.agricultural_zone    != null ? govItem('Agrarisch', gov.agricultural_zone ? '✅ Ja' : '❌ Nee', gov.agricultural_zone ? 'positive' : null) : ''}
          ${gov.animal_keeping_allowed != null ? govItem('Dieren houden', gov.animal_keeping_allowed ? '✅ Toegelaten' : '❌ Niet toegelaten', gov.animal_keeping_allowed ? 'positive' : 'negative') : ''}
          ${gov.bnb_possible         != null ? govItem('B&B mogelijk', gov.bnb_possible ? '✅ Waarschijnlijk' : '⚠️ Onduidelijk', gov.bnb_possible ? 'positive' : 'warning') : ''}
        </div>
      </div>` : ''}

      <!-- AI Analysis -->
      ${score != null ? `
      <div class="detail-section">
        <h3>AI Analyse</h3>
        <div class="score-row">
          <div class="score-circle ${scoreCls(score)}">${score}</div>
          <div class="score-summary">${esc(prop.ai_analysis?.summary || '')}</div>
        </div>
        ${(prop.ai_analysis?.pros?.length || prop.ai_analysis?.cons?.length) ? `
        <div class="pros-cons">
          <div class="pros-list">
            <h4>Voordelen</h4>
            <ul>${(prop.ai_analysis.pros || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul>
          </div>
          <div class="cons-list">
            <h4>Aandachtspunten</h4>
            <ul>${(prop.ai_analysis.cons || []).map(c => `<li>${esc(c)}</li>`).join('')}</ul>
          </div>
        </div>` : ''}
        ${prop.ai_analysis?.recommendations?.length ? `
        <div class="recommendations">
          <ul>${prop.ai_analysis.recommendations.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
        </div>` : ''}
      </div>` : ''}

      <!-- Description -->
      ${prop.description ? `
      <div class="detail-section">
        <h3>Beschrijving</h3>
        <p style="font-size:.88rem;color:var(--stone-600);line-height:1.6">${esc(prop.description.substring(0,600))}${prop.description.length > 600 ? '…' : ''}</p>
      </div>` : ''}

      <!-- Notes -->
      <div class="detail-section" id="modal-notes-section">
        <h3>Notities</h3>
        ${renderNotesSection(propertyId)}
      </div>

      <!-- Source -->
      <div class="detail-section" style="font-size:.78rem;color:var(--stone-400)">
        Bron: ${esc(prop.source)} &nbsp;·&nbsp; Gevonden: ${fmtDate(prop.first_seen)}
      </div>
    </div>

    <div class="modal-footer">
      <a class="btn-visit" href="${esc(prop.source_url)}" target="_blank" rel="noopener">
        🔗 Bekijk advertentie
      </a>
      <button class="btn-like-large ${liked ? 'liked' : ''}" id="modal-like-btn"
        onclick="handleLike(event,'${esc(prop.id)}',true)" aria-label="Like">
        ${liked ? '❤️' : '🤍'}
      </button>
    </div>`;

  overlay.appendChild(sheet);
  document.body.appendChild(overlay);

  // Store current propertyId on the modal for re-render
  sheet.dataset.propertyId = propertyId;
}

// ── Risks section HTML ────────────────────────────────────────────────────────
/**
 * Render a risk list from government_data.risks (array of RiskItem).
 * Falls back to a simple summary from the legacy flat fields when the risks
 * array is absent (older data in properties.json).
 */
function renderRisksHtml(gov) {
  const risks = gov.risks || [];

  // If the backend has already compiled a structured list, use it.
  if (risks.length > 0) {
    const items = risks.map(r => {
      const icon = r.level === 'high' ? '🔴' : r.level === 'medium' ? '🟡' : '🟢';
      const sourceLink = r.source_url
        ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener" class="risk-source">Meer info →</a>`
        : '';
      return `
        <div class="risk-item risk-${esc(r.level)}">
          <span class="risk-icon">${icon}</span>
          <div class="risk-body">
            <div class="risk-name">${esc(r.name)}</div>
            <div class="risk-detail">${esc(r.detail)}</div>
            ${sourceLink}
          </div>
        </div>`;
    }).join('');
    return `<div class="risk-list">${items}</div>`;
  }

  // Legacy fallback: build a minimal list from the flat fields.
  const legacyItems = [];
  if (gov.flood_risk) {
    legacyItems.push({ icon: '🔴', cls: 'risk-medium', name: 'Overstromingsrisico', detail: gov.flood_risk,
      url: 'https://www.waterinfo.be' });
  } else {
    legacyItems.push({ icon: '🟢', cls: 'risk-low', name: 'Overstromingsrisico', detail: 'Geen risico vastgesteld',
      url: 'https://www.waterinfo.be' });
  }
  if (gov.heritage_protected) {
    legacyItems.push({ icon: '🟡', cls: 'risk-medium', name: 'Erfgoedbescherming',
      detail: 'Verbouwingen vereisen toestemming Onroerend Erfgoed',
      url: 'https://inventaris.onroerenderfgoed.be' });
  }
  if (gov.nature_zone) {
    legacyItems.push({ icon: '🟡', cls: 'risk-medium', name: 'Natuur- of bosgebied',
      detail: 'Constructies en functiewijzigingen sterk beperkt', url: 'https://omgevingsloket.be' });
  }
  if (legacyItems.length === 0) return '<p style="font-size:.84rem;color:var(--stone-400)">Geen risico-informatie beschikbaar voor dit pand.</p>';
  return `<div class="risk-list">${legacyItems.map(r => `
    <div class="risk-item ${esc(r.cls)}">
      <span class="risk-icon">${r.icon}</span>
      <div class="risk-body">
        <div class="risk-name">${esc(r.name)}</div>
        <div class="risk-detail">${esc(r.detail)}</div>
        <a href="${esc(r.url)}" target="_blank" rel="noopener" class="risk-source">Meer info →</a>
      </div>
    </div>`).join('')}</div>`;
}

/** Return the worst risk level present in the risks array. */
function worstRiskLevel(gov) {
  const risks = gov?.risks || [];
  if (risks.some(r => r.level === 'high'))   return 'high';
  if (risks.some(r => r.level === 'medium')) return 'medium';
  if (gov?.flood_risk || gov?.heritage_protected) return 'medium';
  return 'low';
}

// ── Tags section HTML ─────────────────────────────────────────────────────────
function renderTagsSection(propertyId) {
  const ann       = getAnnotations(propertyId);
  const available = getAvailableTags();
  const propId    = esc(propertyId);

  const currentHtml = ann.tags.length
    ? ann.tags.map(t =>
        `<span class="tag-pill active" onclick="handleToggleTag('${propId}','${esc(t)}')">${esc(t)}</span>`
      ).join('')
    : `<span class="no-tags-hint">Nog geen tags — tap hieronder om toe te voegen</span>`;

  const pickerHtml = available.map(t =>
    `<button class="tag-picker-item ${ann.tags.includes(t) ? 'selected' : ''}"
       onclick="handleToggleTag('${propId}','${esc(t)}')">${esc(t)}</button>`
  ).join('');

  return `
    <div class="tags-display" id="tags-display-${propId}">${currentHtml}</div>
    <button class="btn-toggle-tags" onclick="toggleTagPicker('${propId}')">🏷️ Tags bewerken</button>
    <div class="tag-picker" id="tag-picker-${propId}">${pickerHtml}</div>
    <div class="new-tag-row" id="new-tag-row-${propId}" style="display:none">
      <input class="new-tag-input" id="new-tag-input-${propId}" type="text" placeholder="Nieuw tag…" maxlength="40">
      <button class="btn-add-tag" onclick="handleAddTagFromModal('${propId}')">+</button>
    </div>`;
}

function toggleTagPicker(propertyId) {
  const picker = $(`#tag-picker-${propertyId}`);
  const row    = $(`#new-tag-row-${propertyId}`);
  if (!picker) return;
  const open = picker.classList.toggle('open');
  if (row) row.style.display = open ? 'flex' : 'none';
}

async function handleToggleTag(propertyId, tag) {
  await toggleTag(propertyId, tag);
  // Re-render tags section in open modal
  const section = $('#modal-tags-section');
  if (section) {
    const h3 = section.querySelector('h3');
    section.innerHTML = '<h3>Tags</h3>' + renderTagsSection(propertyId);
    // Keep picker open
    const picker = $(`#tag-picker-${propertyId}`);
    if (picker) picker.classList.add('open');
    const row = $(`#new-tag-row-${propertyId}`);
    if (row) row.style.display = 'flex';
  }
  // Re-render card list (tag chips may have changed)
  renderCurrentTab();
  showToast('🏷️ Tag bijgewerkt');
}

async function handleAddTagFromModal(propertyId) {
  const input = $(`#new-tag-input-${propertyId}`);
  const tag   = input?.value.trim();
  if (!tag) return;
  if (getAvailableTags().includes(tag)) {
    await handleToggleTag(propertyId, tag);
    if (input) input.value = '';
    return;
  }
  await addAvailableTag(tag);
  await handleToggleTag(propertyId, tag);
  if (input) input.value = '';
}

// ── Notes section HTML ────────────────────────────────────────────────────────
function renderNotesSection(propertyId) {
  const ann   = getAnnotations(propertyId);
  const notes = ann.notes;
  const propId = esc(propertyId);

  const notesHtml = notes.length
    ? `<div class="notes-list">` +
      notes.map((n, i) => {
        const mine   = n.user === state.user?.name;
        const initials = (n.user || '?').charAt(0).toUpperCase();
        return `
          <div class="note-item ${mine ? 'mine' : ''}">
            <div class="note-header">
              <span class="note-avatar ${mine ? 'mine-avatar' : ''}">${initials}</span>
              <span class="note-meta"><strong>${esc(n.user)}</strong> · ${fmtDate(n.ts)}</span>
              ${mine ? `<button class="note-delete" onclick="handleDeleteNote('${propId}',${i})" aria-label="Verwijder">×</button>` : ''}
            </div>
            <div class="note-text">${esc(n.text)}</div>
          </div>`;
      }).join('') +
      `</div>`
    : `<p style="font-size:.84rem;color:var(--stone-400);margin-bottom:12px;font-style:italic">Nog geen notities — schrijf hieronder je eerste notitie.</p>`;

  return `
    ${notesHtml}
    <div class="note-form">
      <textarea id="note-input-${propId}" placeholder="Schrijf een notitie…" rows="3"></textarea>
      <button class="btn-add-note" onclick="handleAddNote('${propId}')">✏️ Toevoegen</button>
    </div>`;
}

async function handleAddNote(propertyId) {
  const ta = $(`#note-input-${propertyId}`);
  if (!ta) return;
  const text = ta.value.trim();
  if (!text) { showToast('⚠️ Notitie is leeg'); return; }
  ta.value = '';
  await addNote(propertyId, text);
  const section = $('#modal-notes-section');
  if (section) section.innerHTML = '<h3>Notities</h3>' + renderNotesSection(propertyId);
  renderCurrentTab();
}

async function handleDeleteNote(propertyId, index) {
  await deleteNote(propertyId, index);
  const section = $('#modal-notes-section');
  if (section) section.innerHTML = '<h3>Notities</h3>' + renderNotesSection(propertyId);
  renderCurrentTab();
}

function closeModal() {
  $$('.modal-overlay').forEach(m => m.remove());
}

// ── Like handler ──────────────────────────────────────────────────────────────
async function handleLike(event, propertyId, fromModal = false) {
  event.stopPropagation();
  await toggleLike(propertyId);
  const modalBtn = $('#modal-like-btn');
  if (modalBtn) {
    const liked = isLikedByMe(propertyId);
    modalBtn.textContent = liked ? '❤️' : '🤍';
    modalBtn.classList.toggle('liked', liked);
  }
}

// ── Setup form ────────────────────────────────────────────────────────────────
async function submitSetup() {
  const name  = $('#setup-name')?.value.trim();
  const token = $('#setup-token')?.value.trim();
  if (!name) { showToast('⚠️ Naam is verplicht'); return; }
  state.user = { name, token: token || '' };
  Store.set('huizenjacht_user', state.user);
  await startApp();
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtPrice(price) {
  return Number(price).toLocaleString('nl-BE', { maximumFractionDigits: 0 });
}
function fmtArea(m2) {
  if (m2 >= 10000) return `${(m2/10000).toFixed(1).replace('.0','')} ha`;
  return `${Number(m2).toLocaleString('nl-BE', { maximumFractionDigits: 0 })} m²`;
}
function fmtDate(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleDateString('nl-BE', { day:'numeric', month:'short', year:'numeric' });
}
function isNewToday(isoStr) {
  if (!isoStr) return false;
  const d = new Date(isoStr); d.setHours(0,0,0,0);
  const now = new Date(); now.setHours(0,0,0,0);
  return d >= now;
}
function scoreCls(score) {
  if (score >= 7) return 'score-high';
  if (score >= 4) return 'score-medium';
  return 'score-low';
}
function statBox(value, label) {
  return `<div class="detail-stat"><div class="stat-value">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div>`;
}
function govItem(label, value, sentiment) {
  return `<div class="gov-item ${sentiment || ''}"><div class="gov-label">${esc(label)}</div><div class="gov-value">${esc(String(value))}</div></div>`;
}
function emptyState(icon, message) {
  const d = el('div', 'empty-state');
  d.innerHTML = `<div class="empty-icon">${icon}</div><p>${message}</p>`;
  return d;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);

// Expose to inline onclick handlers
window.showTab                = showTab;
window.setFilter              = setFilter;
window.setTagFilter           = setTagFilter;
window.showDetail             = showDetail;
window.closeModal             = closeModal;
window.handleLike             = handleLike;
window.submitSetup            = submitSetup;
window.refreshData            = refreshData;
window.saveSettings           = saveSettings;
window.logout                 = logout;
window.handleAddNote          = handleAddNote;
window.handleDeleteNote       = handleDeleteNote;
window.handleToggleTag        = handleToggleTag;
window.handleAddTagFromModal  = handleAddTagFromModal;
window.toggleTagPicker        = toggleTagPicker;
window.handleAddAvailableTag  = handleAddAvailableTag;
window.handleDeleteAvailableTag = handleDeleteAvailableTag;
