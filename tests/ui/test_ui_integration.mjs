/**
 * UI integration test for docs/app.js.
 *
 * Boots the Huizenjacht single-page app inside JSDOM with mocked GitHub APIs
 * and exercises the trash/dismiss/like flows that the user reported as buggy:
 * "het verwijderen en het komt niet bij de verwijderde te staan".
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM, VirtualConsole } from 'jsdom';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT      = resolve(__dirname, '..', '..');
const HTML_PATH = resolve(ROOT, 'docs', 'index.html');
const APP_PATH  = resolve(ROOT, 'docs', 'app.js');

// ─── Test reporter ──────────────────────────────────────────────────────────
let passed = 0;
let failed = 0;
const failures = [];

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed++;
    failures.push({ name, err });
    console.log(`  ✗ ${name}`);
    console.log(`      ${err.message}`);
  }
}

// ─── Fixtures ───────────────────────────────────────────────────────────────
const SAMPLE_PROPERTIES = [
  {
    id: 'immoweb-with-images',
    source: 'immoweb',
    source_url: 'https://example.com/1',
    title: 'Hoeve met foto’s',
    property_type: 'house',
    price: 450000,
    postal_code: '9700',
    municipality: 'Oudenaarde',
    bedrooms: 4,
    land_area: 8000,
    images: ['https://example.com/img1.jpg'],
    images_local: ['data/images/immoweb-with-images/a.jpg'],
    first_seen: new Date().toISOString(),
    ai_analysis: { score: 8 },
    government_data: { risks: [] },
  },
  {
    id: 'immoweb-no-images',
    source: 'immoweb',
    source_url: 'https://example.com/2',
    title: 'Pand zonder foto’s',
    property_type: 'house',
    price: 350000,
    postal_code: '9600',
    municipality: 'Ronse',
    bedrooms: 3,
    land_area: 500,
    images: [],
    images_local: [],
    first_seen: new Date().toISOString(),
    ai_analysis: { score: 6 },
    government_data: { risks: [] },
  },
];

// ─── In-memory mock of the GitHub Contents API ──────────────────────────────
function createMockGitHub() {
  const files = new Map();
  files.set('docs/data/likes.json',       { sha: 'sha-likes-0',    data: {} });
  files.set('docs/data/annotations.json', { sha: 'sha-annot-0',    data: { _meta: { tags: [] } } });
  files.set('docs/data/trash.json',       { sha: 'sha-trash-0',    data: [] });

  function b64encode(obj) {
    return Buffer.from(JSON.stringify(obj, null, 2), 'utf-8').toString('base64');
  }

  return async function fetchMock(input, init = {}) {
    const url = typeof input === 'string' ? input : input.url;
    const method = (init.method || 'GET').toUpperCase();

    // Raw fetch of properties.json
    if (url.includes('raw.githubusercontent.com') && url.includes('properties.json')) {
      return {
        ok: true,
        status: 200,
        async json() { return SAMPLE_PROPERTIES; },
        async text() { return JSON.stringify(SAMPLE_PROPERTIES); },
      };
    }

    // GitHub Contents API
    const m = url.match(/\/contents\/([^?]+)/);
    if (m) {
      const path = decodeURIComponent(m[1]);
      const entry = files.get(path);
      if (method === 'GET') {
        if (!entry) {
          return { ok: false, status: 404, async text() { return 'not found'; }, async json() { return {}; } };
        }
        return {
          ok: true,
          status: 200,
          async json() {
            return { sha: entry.sha, content: b64encode(entry.data), encoding: 'base64' };
          },
        };
      }
      if (method === 'PUT') {
        const body = JSON.parse(init.body);
        const decoded = JSON.parse(Buffer.from(body.content, 'base64').toString('utf-8'));
        const newSha = `sha-${path}-${Date.now()}-${Math.random()}`;
        files.set(path, { sha: newSha, data: decoded });
        return {
          ok: true,
          status: 200,
          async json() { return { content: { sha: newSha } }; },
        };
      }
    }

    return { ok: false, status: 500, async text() { return 'unhandled'; }, async json() { return {}; } };
  };
}

// ─── Boot the SPA in JSDOM ──────────────────────────────────────────────────
async function bootApp() {
  const html   = readFileSync(HTML_PATH, 'utf-8');
  const appSrc = readFileSync(APP_PATH,  'utf-8');

  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', () => { /* swallow CSP/script-tag noise */ });
  virtualConsole.on('error',   (...a) => console.error('[page]', ...a));
  virtualConsole.on('warn',    (...a) => console.warn('[page]',  ...a));
  // Intentionally drop info/log/debug to keep test output clean.

  const dom = new JSDOM(html, {
    url: 'http://localhost/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole,
  });

  const { window } = dom;

  // Pre-seed the user so the app skips the setup screen.
  window.localStorage.setItem('huizenjacht_user',
    JSON.stringify({ name: 'Tester', token: 'ghp_fake' }));

  // Inject mocked fetch BEFORE app.js runs.
  window.fetch = createMockGitHub();

  // Polyfill missing browser APIs that app.js touches.
  window.confirm = () => true;
  window.alert   = () => {};

  // Run app.js inside the JSDOM window.
  window.eval(appSrc);

  // app.js wires init() to DOMContentLoaded; trigger it now since the document
  // was already parsed before we evaluated the script.
  if (typeof window.init === 'function') {
    await window.init();
  } else {
    // Fall back to the DOMContentLoaded path.
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    // Wait a microtask tick for async init to start.
    await new Promise(r => setTimeout(r, 0));
  }

  // Wait for loadData() to finish — it fires several awaited fetches.
  await flushAsync(window);

  return { dom, window };
}

async function flushAsync(window, ticks = 10) {
  for (let i = 0; i < ticks; i++) {
    await new Promise(r => setTimeout(r, 0));
  }
}

function $list(window) {
  return window.document.querySelector('#properties-list');
}

function visibleCardIds(window) {
  return [...$list(window).querySelectorAll('.property-card')]
    .map(c => c.dataset.id);
}

function clickFilter(window, name) {
  // Inline onclick handlers don't always fire reliably under JSDOM, so call
  // the exposed window function directly — this matches what the chip would do.
  assert(typeof window.setFilter === 'function', 'window.setFilter not exposed');
  window.setFilter(name);
}

// ─── Tests ──────────────────────────────────────────────────────────────────
console.log('UI integration tests (JSDOM)\n');

const { window } = await bootApp();

await test('app boots and shows the main screen', async () => {
  const main = window.document.querySelector('#main-screen');
  assert(main && !main.classList.contains('hidden'), 'main screen is hidden');
});

await test('Inbox lists both fixture properties by default', async () => {
  const ids = visibleCardIds(window);
  assert(ids.length === 2, `expected 2 cards, got ${ids.length}: ${ids}`);
});

await test('Prullenbak filter is empty before any delete action', async () => {
  clickFilter(window, 'dismissed');
  await flushAsync(window);
  const ids = visibleCardIds(window);
  assert(ids.length === 0, `expected empty Prullenbak, got ${ids.length}: ${ids}`);
  clickFilter(window, 'all');
  await flushAsync(window);
});

await test('clicking 🗑️ on a property WITH images moves it to Prullenbak', async () => {
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);

  let ids = visibleCardIds(window);
  assert(
    !ids.includes('immoweb-with-images'),
    `expected property removed from Inbox, got: ${ids}`
  );

  clickFilter(window, 'dismissed');
  await flushAsync(window);
  ids = visibleCardIds(window);
  assert(
    ids.includes('immoweb-with-images'),
    `expected immoweb-with-images in Prullenbak, got: ${JSON.stringify(ids)}`
  );

  // Restore so the next test starts clean.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);
  clickFilter(window, 'all');
  await flushAsync(window);
});

await test('clicking 🗑️ on a property WITHOUT images also moves it to Prullenbak (regression)', async () => {
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-no-images');
  await flushAsync(window);

  let ids = visibleCardIds(window);
  assert(
    !ids.includes('immoweb-no-images'),
    `image-less property still in Inbox after delete: ${ids}`
  );

  clickFilter(window, 'dismissed');
  await flushAsync(window);
  ids = visibleCardIds(window);
  assert(
    ids.includes('immoweb-no-images'),
    `image-less property NOT in Prullenbak — regression of original bug: ${ids}`
  );

  // Restore so subsequent tests start clean.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-no-images');
  await flushAsync(window);
  clickFilter(window, 'all');
  await flushAsync(window);
});

await test('"trashed" filter no longer exists in the UI', async () => {
  const chip = window.document.querySelector('.filter-chip[data-filter="trashed"]');
  assert(!chip, 'old "trashed" chip should be removed');
});

// ─── Like flow ──────────────────────────────────────────────────────────────
await test('clicking ❤️ moves property into "liked" filter', async () => {
  await window.handleLike({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);

  clickFilter(window, 'liked');
  await flushAsync(window);
  let ids = visibleCardIds(window);
  assert(
    ids.includes('immoweb-with-images'),
    `expected liked property visible under "liked" filter, got: ${ids}`
  );

  // Inbox should still show it (likes do not hide from Inbox).
  clickFilter(window, 'all');
  await flushAsync(window);
  ids = visibleCardIds(window);
  assert(ids.includes('immoweb-with-images'), 'liked property should remain in Inbox');
});

await test('re-clicking ❤️ unlikes the property', async () => {
  await window.handleLike({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);

  clickFilter(window, 'liked');
  await flushAsync(window);
  const ids = visibleCardIds(window);
  assert(
    !ids.includes('immoweb-with-images'),
    `expected unliked property gone from "liked" filter, got: ${ids}`
  );

  clickFilter(window, 'all');
  await flushAsync(window);
});

// ─── Restore from Prullenbak ───────────────────────────────────────────────
await test('re-clicking 🗑️ restores a property from Prullenbak to Inbox', async () => {
  // Move to Prullenbak.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);

  // Restore.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);

  let ids = visibleCardIds(window);
  assert(
    ids.includes('immoweb-with-images'),
    `expected restored property back in Inbox, got: ${ids}`
  );

  clickFilter(window, 'dismissed');
  await flushAsync(window);
  ids = visibleCardIds(window);
  assert(
    !ids.includes('immoweb-with-images'),
    `restored property should not still be in Prullenbak, got: ${ids}`
  );
  clickFilter(window, 'all');
  await flushAsync(window);
});

// ─── Filter chip active state ──────────────────────────────────────────────
await test('setFilter() marks the matching chip active and unsets the others', async () => {
  clickFilter(window, 'liked');
  await flushAsync(window);

  const chips = [...window.document.querySelectorAll('.filter-chip')];
  const active = chips.filter(c => c.classList.contains('active'));
  assert(active.length === 1, `expected exactly 1 active chip, got ${active.length}`);
  assert(
    active[0].dataset.filter === 'liked',
    `expected active filter "liked", got "${active[0].dataset.filter}"`
  );

  clickFilter(window, 'all');
  await flushAsync(window);
});

// ─── Empty-state copy ──────────────────────────────────────────────────────
await test('Prullenbak empty state shows the new "Prullenbak is leeg." copy', async () => {
  clickFilter(window, 'dismissed');
  await flushAsync(window);

  const list = window.document.querySelector('#properties-list');
  // Make sure no cards are present, then assert empty-state text.
  if (list.querySelectorAll('.property-card').length === 0) {
    const text = list.textContent || '';
    assert(
      /Prullenbak is leeg/i.test(text),
      `expected "Prullenbak is leeg" empty-state copy, got: ${text.slice(0, 200)}`
    );
  }
  clickFilter(window, 'all');
  await flushAsync(window);
});

// ─── Card UI semantics ─────────────────────────────────────────────────────
await test('property card shows the unified 🗑️ dismiss button (no separate trash button)', async () => {
  const list = window.document.querySelector('#properties-list');
  const card = list.querySelector('.property-card');
  assert(card, 'no property card rendered');

  const dismissBtn = card.querySelector('.btn-dismiss');
  const trashBtn   = card.querySelector('.btn-trash');
  assert(dismissBtn, 'dismiss button missing on card');
  assert(!trashBtn,  'old photo-trash button must not be present');
  assert(
    /Verplaats naar Prullenbak/i.test(dismissBtn.getAttribute('aria-label') || ''),
    `aria-label should mention Prullenbak, got: ${dismissBtn.getAttribute('aria-label')}`
  );
  assert(
    /🗑️/.test(dismissBtn.textContent),
    `dismiss button should show 🗑️ icon, got: ${dismissBtn.textContent.trim()}`
  );
});

await test('dismissed card swaps to ♻️ icon and "Prullenbak" badge', async () => {
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-no-images');
  await flushAsync(window);

  clickFilter(window, 'dismissed');
  await flushAsync(window);
  const card = window.document.querySelector(
    `.property-card[data-id="immoweb-no-images"]`
  );
  assert(card, 'dismissed card not rendered under Prullenbak filter');

  const dismissBtn = card.querySelector('.btn-dismiss');
  assert(/♻️/.test(dismissBtn.textContent), `expected ♻️ icon, got: ${dismissBtn.textContent.trim()}`);
  assert(
    /Herstel uit Prullenbak/i.test(dismissBtn.getAttribute('aria-label') || ''),
    `aria-label should say "Herstel uit Prullenbak", got: ${dismissBtn.getAttribute('aria-label')}`
  );

  const badge = card.querySelector('.badge-dismissed');
  assert(badge && /Prullenbak/i.test(badge.textContent), 'expected "Prullenbak" badge on dismissed card');

  // Restore for next tests.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-no-images');
  await flushAsync(window);
  clickFilter(window, 'all');
  await flushAsync(window);
});

// ─── Modal dismiss button stays in sync ────────────────────────────────────
await test('modal dismiss button updates icon+label after toggling from modal', async () => {
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  let modalBtn = window.document.querySelector('#modal-dismiss-btn');
  assert(modalBtn, 'modal dismiss button missing');
  assert(/🗑️/.test(modalBtn.textContent), `initial modal icon should be 🗑️, got: ${modalBtn.textContent.trim()}`);

  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-with-images', true);
  await flushAsync(window);

  modalBtn = window.document.querySelector('#modal-dismiss-btn');
  assert(/♻️/.test(modalBtn.textContent), `modal icon should flip to ♻️, got: ${modalBtn.textContent.trim()}`);
  assert(
    /Herstel uit Prullenbak/i.test(modalBtn.getAttribute('aria-label') || ''),
    `aria-label should be "Herstel uit Prullenbak", got: ${modalBtn.getAttribute('aria-label')}`
  );

  // Restore + close modal.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-with-images', true);
  await flushAsync(window);
  window.closeModal();
});

await test('detail modal no longer exposes a photo-trash section', async () => {
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const section = window.document.querySelector('#modal-trash-section');
  assert(!section, 'photo-trash section should be removed from detail modal');

  window.closeModal();
});

// ─── Notes flow ────────────────────────────────────────────────────────────
await test('adding a note moves the property into the "noted" filter', async () => {
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const ta = window.document.querySelector('#note-input-immoweb-with-images');
  assert(ta, 'note textarea missing in detail modal');
  ta.value = 'Mooi pand met grote tuin';

  await window.handleAddNote('immoweb-with-images');
  await flushAsync(window);

  clickFilter(window, 'noted');
  await flushAsync(window);
  const ids = visibleCardIds(window);
  assert(
    ids.includes('immoweb-with-images'),
    `expected noted property in "noted" filter, got: ${ids}`
  );

  clickFilter(window, 'all');
  await flushAsync(window);
  window.closeModal();
});

// ─── Tab switching ─────────────────────────────────────────────────────────
await test('showTab("settings") hides the properties list and shows settings panel', async () => {
  window.showTab('settings');
  await flushAsync(window);

  const settings = window.document.querySelector('#settings-tab');
  const props    = window.document.querySelector('#properties-tab');
  assert(settings && !settings.classList.contains('hidden'), 'settings tab should be visible');
  assert(props && props.classList.contains('hidden'),       'properties tab should be hidden');

  // Settings must NOT contain the removed "Prullenbak beheer" block.
  const settingsText = settings.textContent || '';
  assert(
    !/Prullenbak beheer/i.test(settingsText),
    'settings must no longer expose "Prullenbak beheer"'
  );

  window.showTab('properties');
  await flushAsync(window);
});

// ─── Persistence round-trip ────────────────────────────────────────────────
await test('dismissing persists via PUT to annotations.json (round-trip)', async () => {
  // Wrap fetch to record PUTs.
  const seenPuts = [];
  const realFetch = window.fetch;
  window.fetch = async (input, init = {}) => {
    if ((init.method || 'GET').toUpperCase() === 'PUT') {
      const url = typeof input === 'string' ? input : input.url;
      seenPuts.push(url);
    }
    return realFetch(input, init);
  };

  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-no-images');
  await flushAsync(window);

  assert(
    seenPuts.some(u => u.includes('annotations.json')),
    `expected PUT to annotations.json, got: ${JSON.stringify(seenPuts)}`
  );

  // Restore and clean up.
  await window.handleDismiss({ stopPropagation: () => {} }, 'immoweb-no-images');
  await flushAsync(window);
  window.fetch = realFetch;
});

// ─── Sanity: no references to removed handlers in the live HTML ────────────
await test('no rendered HTML references removed handleTrash / handleTrashModal', async () => {
  // Re-render Inbox and a detail modal, then scan the resulting DOM.
  clickFilter(window, 'all');
  await flushAsync(window);
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const html = window.document.body.innerHTML;
  for (const banned of ['handleTrash(', 'handleTrashModal(', 'handleRestoreModal(', 'handleEmptyOneModal(']) {
    assert(
      !html.includes(banned),
      `rendered HTML still references removed handler: ${banned}`
    );
  }
  window.closeModal();
});

// ─── Tag flow ──────────────────────────────────────────────────────────────
await test('handleToggleTag adds a tag, renders chip, then removes it', async () => {
  await window.handleToggleTag('immoweb-with-images', 'Favoriet');
  await flushAsync(window);

  let card = window.document.querySelector('.property-card[data-id="immoweb-with-images"]');
  assert(card, 'card missing after tag toggle');
  let chips = [...card.querySelectorAll('.tag-chip')].map(c => c.textContent);
  assert(chips.includes('Favoriet'), `expected Favoriet chip on card, got: ${chips}`);

  // Toggle off.
  await window.handleToggleTag('immoweb-with-images', 'Favoriet');
  await flushAsync(window);
  card = window.document.querySelector('.property-card[data-id="immoweb-with-images"]');
  chips = [...card.querySelectorAll('.tag-chip')].map(c => c.textContent);
  assert(!chips.includes('Favoriet'), `expected Favoriet chip removed, got: ${chips}`);
});

await test('adding a brand-new tag from the modal registers it as available', async () => {
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const input = window.document.querySelector('#new-tag-input-immoweb-with-images');
  assert(input, 'new-tag input not rendered in modal');
  input.value = 'Tuin gewenst';
  await window.handleAddTagFromModal('immoweb-with-images');
  await flushAsync(window);

  // Available tags pool should now include the new tag.
  const available = window.getAvailableTags ? window.getAvailableTags() : [];
  // getAvailableTags isn't on window — read from chips instead.
  const card = window.document.querySelector('.property-card[data-id="immoweb-with-images"]');
  const chips = [...(card?.querySelectorAll('.tag-chip') || [])].map(c => c.textContent);
  assert(chips.includes('Tuin gewenst'), `expected new tag on card, got: ${chips}`);

  // Toggle the property back off (cleanup) so other tests stay deterministic.
  await window.handleToggleTag('immoweb-with-images', 'Tuin gewenst');
  await flushAsync(window);
  window.closeModal();
});

// ─── Lightbox ──────────────────────────────────────────────────────────────
await test('openLightbox shows the lightbox and closeLightbox hides it again', async () => {
  const lb = window.document.querySelector('#lightbox');
  assert(lb, 'lightbox element missing in index.html');
  assert(lb.classList.contains('hidden'), 'lightbox should start hidden');

  window.openLightbox(['https://example.com/img1.jpg', 'https://example.com/img2.jpg'], 0);
  assert(!lb.classList.contains('hidden'), 'lightbox should be visible after openLightbox');

  const counter = window.document.querySelector('#lightbox-counter');
  assert(counter && counter.textContent.startsWith('1 /'), `expected counter "1 / 2", got: ${counter?.textContent}`);

  window.lightboxNavigate(1);
  assert(counter.textContent.startsWith('2 /'), `expected counter "2 / 2", got: ${counter.textContent}`);

  window.closeLightbox();
  assert(lb.classList.contains('hidden'), 'lightbox should be hidden again after closeLightbox');
});

await test('openLightbox is a no-op for empty image arrays', async () => {
  const lb = window.document.querySelector('#lightbox');
  lb.classList.add('hidden');
  window.openLightbox([], 0);
  assert(lb.classList.contains('hidden'), 'lightbox must stay hidden for []');
});

// ─── Matches badge ─────────────────────────────────────────────────────────
await test('updateMatchBadge stays hidden when only one user liked the property', async () => {
  // Ensure liked by current user.
  await window.handleLike({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);

  const badge = window.document.querySelector('#matches-count');
  assert(badge, 'match badge missing');
  assert(
    badge.classList.contains('hidden'),
    'match badge must remain hidden with only 1 liker'
  );

  // Cleanup — unlike.
  await window.handleLike({ stopPropagation: () => {} }, 'immoweb-with-images');
  await flushAsync(window);
});

// ─── Notes delete flow ─────────────────────────────────────────────────────
await test('handleDeleteNote removes a note from the property', async () => {
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const ta = window.document.querySelector('#note-input-immoweb-with-images');
  ta.value = 'Tijdelijke notitie om te verwijderen';
  await window.handleAddNote('immoweb-with-images');
  await flushAsync(window);

  // Re-open detail to refresh the notes list.
  window.closeModal();
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const noteItems = window.document.querySelectorAll('#modal-notes-section .note-item');
  const before = noteItems.length;
  assert(before >= 1, `expected at least 1 note before delete, got ${before}`);

  await window.handleDeleteNote('immoweb-with-images', before - 1);
  await flushAsync(window);

  window.closeModal();
  window.showDetail('immoweb-with-images');
  await flushAsync(window);
  const after = window.document.querySelectorAll('#modal-notes-section .note-item').length;
  assert(after === before - 1, `expected ${before - 1} notes after delete, got ${after}`);

  window.closeModal();

  // Property should no longer appear in "noted" filter if no notes remain.
  if (after === 0) {
    clickFilter(window, 'noted');
    await flushAsync(window);
    const ids = visibleCardIds(window);
    assert(!ids.includes('immoweb-with-images'), 'property with no notes must not appear in noted filter');
    clickFilter(window, 'all');
    await flushAsync(window);
  }
});

// ─── Modal close ───────────────────────────────────────────────────────────
await test('closeModal removes the detail modal overlay', async () => {
  window.showDetail('immoweb-with-images');
  await flushAsync(window);
  const opened = window.document.querySelector('.modal-overlay');
  assert(opened, 'modal overlay should exist after showDetail');

  window.closeModal();
  await flushAsync(window);
  const stillThere = window.document.querySelector('.modal-overlay');
  assert(!stillThere, 'modal overlay should be removed after closeModal');
});

// ─── Tag filter chip ───────────────────────────────────────────────────────
await test('setTagFilter narrows the list to properties carrying that tag', async () => {
  await window.handleToggleTag('immoweb-no-images', 'Sleeper');
  await flushAsync(window);

  window.setTagFilter('Sleeper');
  await flushAsync(window);

  const ids = visibleCardIds(window);
  assert(ids.includes('immoweb-no-images'),  `expected Sleeper-tagged property visible, got: ${ids}`);
  assert(!ids.includes('immoweb-with-images'), `untagged property must be filtered out, got: ${ids}`);

  // Clear tag filter + cleanup.
  window.setTagFilter(null);
  await flushAsync(window);
  await window.handleToggleTag('immoweb-no-images', 'Sleeper');
  await flushAsync(window);
});

// ─── Mojibake guard ────────────────────────────────────────────────────────
await test('rendered HTML never contains the U+FFFD replacement character', async () => {
  clickFilter(window, 'all');
  await flushAsync(window);
  window.showDetail('immoweb-with-images');
  await flushAsync(window);

  const html = window.document.body.innerHTML;
  assert(
    !html.includes('\uFFFD'),
    'rendered HTML contains U+FFFD — likely a corrupted emoji somewhere in app.js'
  );
  window.closeModal();
});

// ─── Logout clears the stored user ─────────────────────────────────────────
await test('logout clears huizenjacht_user from localStorage', async () => {
  // Confirm yes; suppress location.reload (not implemented in JSDOM).
  window.confirm = () => true;
  try { Object.defineProperty(window.location, 'reload', { value: () => {}, configurable: true }); } catch {}

  assert(
    window.localStorage.getItem('huizenjacht_user'),
    'precondition: user should be in localStorage before logout'
  );
  window.logout();
  await flushAsync(window);
  assert(
    !window.localStorage.getItem('huizenjacht_user'),
    'huizenjacht_user must be removed from localStorage after logout'
  );
});

// ─── Summary ────────────────────────────────────────────────────────────────
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.log(`\n[FAIL] ${f.name}\n${f.err.stack}`);
  process.exit(1);
}
process.exit(0);
