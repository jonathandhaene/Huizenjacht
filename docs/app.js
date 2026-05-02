/**
 * Huizenjacht — collaborative mobile web app
 *
 * Architecture
 * ────────────
 * • Properties are stored in docs/data/properties.json (updated daily by
 *   GitHub Actions) and fetched via the GitHub raw-content URL (no auth needed
 *   for public repos).
 *
 * • Likes are stored in docs/data/likes.json.  The web app reads and writes
 *   this file via the GitHub Contents API, which requires a Personal Access
 *   Token (PAT) with "contents: write" on this repo.  The PAT is saved in
 *   localStorage and never leaves the browser.
 *
 * • A "match" occurs when both users have liked the same property.
 *
 * Setup (one-time per user)
 * ─────────────────────────
 * 1. Open the app.
 * 2. Enter your name and a GitHub PAT.
 * 3. Settings are saved to localStorage.
 */

'use strict';

// ── Configuration ─────────────────────────────────────────────────────────────
// These values are baked in at build time — they match the repo that hosts this app.
const CONFIG = {
  owner:  'jonathandhaene',
  repo:   'Huizenjacht',
  branch: 'main',
  propertiesPath: 'docs/data/properties.json',
  likesPath:      'docs/data/likes.json',
  // GitHub Pages base path ('' for apex domain, '/Huizenjacht' for user-page sub-path)
  basePath: '/Huizenjacht',
};

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  user:        null,   // { name, token }
  properties:  [],     // Array of property objects from properties.json
  likes:       {},     // { propertyId: { userName: "ISO timestamp", … }, … }
  likesSha:    null,   // GitHub blob SHA — required to update the file
  activeTab:   'properties',
  activeFilter:'all',
  loading:     false,
};

// ── GitHub API helpers ────────────────────────────────────────────────────────
const GitHub = {
  /** Fetch a file from the repo via the GitHub Contents API. */
  async getFile(path, token) {
    const url = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/contents/${path}?ref=${CONFIG.branch}`;
    const headers = { Accept: 'application/vnd.github+json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
    return res.json();  // { content (base64), sha, … }
  },

  /** Decode a base64-encoded file returned by the Contents API. */
  decode(file) {
    return JSON.parse(decodeURIComponent(escape(atob(file.content.replace(/\n/g, '')))));
  },

  /** Create or update a file in the repo via the Contents API. */
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
    return result.content.sha;  // Updated SHA for the next write
  },

  /** Fetch raw JSON without auth (works for public repos). */
  async fetchRaw(path) {
    const url = `https://raw.githubusercontent.com/${CONFIG.owner}/${CONFIG.repo}/${CONFIG.branch}/${path}?_=${Date.now()}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Raw fetch ${res.status}`);
    return res.json();
  },
};

// ── Local storage helpers ─────────────────────────────────────────────────────
const Store = {
  get(key)       { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
  set(key, val)  { localStorage.setItem(key, JSON.stringify(val)); },
  remove(key)    { localStorage.removeItem(key); },
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
  // Load saved credentials
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

  // Update user pill in header
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
  // Load properties (no auth needed for public repo)
  try {
    state.properties = await GitHub.fetchRaw(CONFIG.propertiesPath);
  } catch {
    state.properties = [];
  }

  // Load likes (need SHA for future writes → use API)
  try {
    const file = await GitHub.getFile(CONFIG.likesPath, state.user.token);
    state.likes    = GitHub.decode(file);
    state.likesSha = file.sha;
  } catch {
    state.likes    = {};
    state.likesSha = null;
  }
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

  // Optimistic update
  if (!state.likes[propertyId]) state.likes[propertyId] = {};
  if (alreadyLiked) {
    delete state.likes[propertyId][name];
    if (Object.keys(state.likes[propertyId]).length === 0) delete state.likes[propertyId];
    showToast('💔 Like verwijderd');
  } else {
    state.likes[propertyId][name] = new Date().toISOString();
    showToast('❤️ Geliked!');

    // Check for a new match immediately
    const likeCount = Object.keys(state.likes[propertyId]).length;
    if (likeCount >= 2) {
      const prop = state.properties.find(p => p.id === propertyId);
      showMatchCelebration(prop);
    }
  }

  // Re-render to reflect optimistic state
  renderCurrentTab();
  updateMatchBadge();

  // Persist to GitHub
  if (!state.user.token) {
    showToast('⚠️ Geen token ingesteld — likes worden lokaal opgeslagen');
    return;
  }
  try {
    const sha = await persistLikes();
    state.likesSha = sha;
  } catch (err) {
    console.error('Like persist failed:', err);
    // If conflict (someone else wrote at the same time), reload and retry once
    if (err.message.includes('409') || err.message.includes('conflict')) {
      showToast('🔄 Conflict — opnieuw proberen…');
      await loadData();
      await toggleLike(propertyId);  // retry
    } else {
      showToast('⚠️ Like kon niet opgeslagen worden');
    }
  }
}

async function persistLikes() {
  const userName = state.user.name;
  const message  = `❤️ ${userName} liked a property`;
  return GitHub.updateFile(
    CONFIG.likesPath,
    state.likes,
    state.likesSha,
    message,
    state.user.token,
  );
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
    <p>${prop ? `<strong>${prop.title}</strong><br>` : ''}Jullie vinden dit allebei leuk!</p>
    <button onclick="this.parentElement.remove()">Bekijk het pand</button>
  `;
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
  else if (state.activeTab === 'matches') renderMatches();
  else if (state.activeTab === 'settings') renderSettings();
}

// ── Filter ────────────────────────────────────────────────────────────────────
function setFilter(filter) {
  state.activeFilter = filter;
  $$('.filter-chip').forEach(c => c.classList.toggle('active', c.dataset.filter === filter));
  renderProperties();
}

function filteredProperties() {
  switch (state.activeFilter) {
    case 'liked':   return state.properties.filter(p => isLikedByMe(p.id));
    case 'matched': return state.properties.filter(p => isMatched(p.id));
    case 'new': {
      const today = new Date(); today.setHours(0,0,0,0);
      return state.properties.filter(p => {
        if (!p.first_seen) return false;
        return new Date(p.first_seen) >= today;
      });
    }
    default: return state.properties;
  }
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
      state.activeFilter === 'new'     ? '🔍' : '🏡',
      state.activeFilter === 'all'
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
  const others  = matched
    ? Object.keys(state.likes[prop.id] || {}).filter(n => n !== state.user?.name)
    : [];

  const card = el('div', `property-card${matched ? ' matched' : liked ? ' liked' : ''}`);
  card.innerHTML = `
    <div class="card-image">
      ${prop.images?.length
        ? `<img src="${prop.images[0]}" alt="${esc(prop.title)}" loading="lazy">`
        : `<div class="card-image-placeholder"><span class="placeholder-icon">🏡</span><span>Geen foto</span></div>`
      }
      <div class="card-badges">
        <span class="card-badge badge-source">${esc(prop.source)}</span>
        ${isNew    ? '<span class="card-badge badge-new">Nieuw</span>' : ''}
        ${matched  ? '<span class="card-badge badge-match">🎉 Match!</span>' : ''}
      </div>
      ${score != null ? `<span class="score-badge ${scoreCls(score)}">⭐ ${score}/10</span>` : ''}
    </div>
    <div class="card-body">
      <div class="card-title">${esc(prop.title)}</div>
      ${prop.municipality ? `<div class="card-location">📍 ${esc(prop.municipality)}${prop.postal_code ? ` ${esc(prop.postal_code)}` : ''}</div>` : ''}
      <div class="card-price">${prop.price ? `€ ${fmtPrice(prop.price)}` : 'Prijs op aanvraag'}</div>
      <div class="card-stats">
        ${prop.bedrooms   ? `<span class="card-stat">🛏 ${prop.bedrooms} slpk</span>` : ''}
        ${prop.land_area  ? `<span class="card-stat">🌿 ${fmtArea(prop.land_area)}</span>` : ''}
        ${prop.living_area? `<span class="card-stat">🏠 ${fmtArea(prop.living_area)}</span>` : ''}
      </div>
      ${matched && others.length ? `<div style="font-size:.8rem;color:var(--red);font-weight:600">❤️ Ook geliked door ${esc(others.join(' & '))}</div>` : ''}
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

  const matchedIds = getMatchedPropertyIds();
  const matchedProps = matchedIds
    .map(id => state.properties.find(p => p.id === id))
    .filter(Boolean);

  if (matchedProps.length === 0) {
    container.innerHTML = '';
    container.appendChild(emptyState('💞',
      'Nog geen matches.<br>Like een pand en zie of jullie het allebei geweldig vinden!'
    ));
    return;
  }

  container.innerHTML = `<div class="match-header">🎉 ${matchedProps.length} match${matchedProps.length !== 1 ? 'es' : ''} gevonden!</div>`;
  matchedProps.forEach(prop => {
    const card = propertyCard(prop);
    // Add "liked by" line in body
    const body = card.querySelector('.card-body');
    const userLikes = state.likes[prop.id] || {};
    const likedByHtml = Object.entries(userLikes)
      .map(([name, ts]) => `<strong>${esc(name)}</strong> op ${fmtDate(ts)}`)
      .join(' & ');
    const likedDiv = el('div', 'match-liked-by', `❤️ Geliked door: ${likedByHtml}`);
    body.appendChild(likedDiv);
    container.appendChild(card);
  });
}

// ── Render: Settings tab ──────────────────────────────────────────────────────
function renderSettings() {
  const container = $('#settings-list');
  if (!container) return;

  const user = state.user || {};
  container.innerHTML = `
    <p class="settings-section-title">Mijn profiel</p>
    <div class="settings-list">
      <div class="settings-item">
        <div class="settings-item-left"><h4>Naam</h4><p>Wordt getoond bij likes</p></div>
        <input id="s-name" type="text" value="${esc(user.name || '')}" placeholder="Jouw naam">
      </div>
      <div class="settings-item">
        <div class="settings-item-left">
          <h4>GitHub Token</h4>
          <p>Vereist om te liken</p>
        </div>
        <input id="s-token" type="password" value="${user.token || ''}" placeholder="ghp_…" autocomplete="new-password">
      </div>
    </div>
    <p class="settings-note">
      Maak een token aan via
      <a href="https://github.com/settings/tokens/new?scopes=repo&description=Huizenjacht" target="_blank" rel="noopener">
        GitHub → Settings → Developer settings → Personal access tokens
      </a>.
      Selecteer het <strong>repo</strong> bereik. Sla het veilig op — het wordt enkel in je browser bewaard.
    </p>
    <button class="btn-save" onclick="saveSettings()">Opslaan</button>
    <button class="btn-logout" onclick="logout()">Uitloggen</button>
    <p class="settings-section-title" style="margin-top:20px">Info</p>
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
  state.user = { name, token };
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
  location.reload();
}

// ── Detail modal ──────────────────────────────────────────────────────────────
function showDetail(propertyId) {
  const prop = state.properties.find(p => p.id === propertyId);
  if (!prop) return;

  $$('.modal-overlay').forEach(m => m.remove());

  const liked = isLikedByMe(propertyId);
  const score = prop.ai_analysis?.score;
  const gov   = prop.government_data;

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
        ? `<div class="gallery">${prop.images.map(u => `<img src="${u}" alt="" loading="lazy">`).join('')}</div>`
        : ''
      }

      <!-- Price & location -->
      <div class="detail-section">
        <div class="detail-price">${prop.price ? `€ ${fmtPrice(prop.price)}` : 'Prijs op aanvraag'}</div>
        <div class="detail-meta">
          📍 ${esc([prop.address, prop.postal_code, prop.municipality].filter(Boolean).join(', ') || 'Onbekende locatie')}
        </div>
        <div class="detail-stats">
          ${statBox(prop.bedrooms ? `${prop.bedrooms}` : '—', 'Slaapkamers')}
          ${statBox(prop.land_area ? fmtArea(prop.land_area) : '—', 'Perceel')}
          ${statBox(prop.living_area ? fmtArea(prop.living_area) : '—', 'Bewoonbaar')}
        </div>
        ${prop.features?.length ? `<div style="margin-top:10px;font-size:.82rem;color:var(--gray-600)">${prop.features.slice(0,8).map(f => `<span style="display:inline-block;background:var(--gray-100);border-radius:4px;padding:2px 8px;margin:2px">${esc(f)}</span>`).join('')}</div>` : ''}
      </div>

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

      <!-- Government data -->
      ${gov ? `
      <div class="detail-section">
        <h3>Overheidsgegevens</h3>
        <div class="gov-grid">
          ${gov.zoning          != null ? govItem('Bestemmingszone', gov.zoning, null) : ''}
          ${gov.agricultural_zone != null ? govItem('Agrarisch', gov.agricultural_zone ? '✅ Ja' : '❌ Nee', gov.agricultural_zone ? 'positive' : null) : ''}
          ${gov.animal_keeping_allowed != null ? govItem('Dieren houden', gov.animal_keeping_allowed ? '✅ Toegelaten' : '❌ Niet toegelaten', gov.animal_keeping_allowed ? 'positive' : 'negative') : ''}
          ${gov.bnb_possible    != null ? govItem('B&B mogelijk', gov.bnb_possible ? '✅ Waarschijnlijk' : '⚠️ Onduidelijk', gov.bnb_possible ? 'positive' : 'warning') : ''}
          ${gov.flood_risk      != null ? govItem('Overstromingsrisico', gov.flood_risk || 'Laag', gov.flood_risk ? 'warning' : 'positive') : ''}
          ${gov.heritage_protected != null ? govItem('Erfgoed', gov.heritage_protected ? '⚠️ Beschermd' : '✅ Vrij', gov.heritage_protected ? 'warning' : 'positive') : ''}
        </div>
        ${gov.source_url ? `<div style="margin-top:10px"><a href="${gov.source_url}" target="_blank" rel="noopener" style="color:var(--green);font-size:.82rem;text-decoration:underline">📍 Bekijk op Geopunt</a></div>` : ''}
      </div>` : ''}

      <!-- Description -->
      ${prop.description ? `
      <div class="detail-section">
        <h3>Beschrijving</h3>
        <p style="font-size:.88rem;color:var(--gray-600);line-height:1.6">${esc(prop.description.substring(0, 600))}${prop.description.length > 600 ? '…' : ''}</p>
      </div>` : ''}

      <!-- Source info -->
      <div class="detail-section" style="font-size:.78rem;color:var(--gray-400)">
        Bron: ${esc(prop.source)} &nbsp;·&nbsp;
        Gevonden: ${fmtDate(prop.first_seen)}
      </div>
    </div>

    <div class="modal-footer">
      <a class="btn-visit" href="${prop.source_url}" target="_blank" rel="noopener">
        🔗 Bekijk advertentie
      </a>
      <button class="btn-like-large ${liked ? 'liked' : ''}" id="modal-like-btn" onclick="handleLike(event,'${esc(prop.id)}',true)" aria-label="Like">
        ${liked ? '❤️' : '🤍'}
      </button>
    </div>`;

  overlay.appendChild(sheet);
  document.body.appendChild(overlay);
}

function closeModal() {
  $$('.modal-overlay').forEach(m => m.remove());
}

// ── Like handler (called from both card and modal) ────────────────────────────
async function handleLike(event, propertyId, fromModal = false) {
  event.stopPropagation();
  await toggleLike(propertyId);

  // Update modal button if open
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
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

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
window.showTab       = showTab;
window.setFilter     = setFilter;
window.showDetail    = showDetail;
window.closeModal    = closeModal;
window.handleLike    = handleLike;
window.submitSetup   = submitSetup;
window.refreshData   = refreshData;
window.saveSettings  = saveSettings;
window.logout        = logout;
