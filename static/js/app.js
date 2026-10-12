// Same-origin mutations use the session's double-submit CSRF token.
const nativeFetch = window.fetch.bind(window);
function canMutate() { return document.body?.dataset.role !== 'viewer'; }
document.addEventListener('DOMContentLoaded', () => {
  if (canMutate()) return;
  const protect = () => document.querySelectorAll('button[onclick],button[data-write]').forEach(button => {
    if (button.hasAttribute('data-write') || /(?:save|delete|clear|toggleFavorite|toggleModalFavorite|toggleHide|hideItem|toggleSearchItem|openEditSearch|executeBatch|openAddSearch|triggerCheck|checkSearchNow|toggleGlobalMonitoring|toggleMonitoring|requestItemAI|refreshItemDetails|startParsing|cancelParsing|retryHuman)/i.test(button.getAttribute('onclick') || '')) {
      button.disabled = true;
      button.title = 'Просмотр: изменение данных недоступно';
    }
  });
  protect();
  new MutationObserver(protect).observe(document.body, {childList:true,subtree:true});
});
function htmlText(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
}
function safeDomUrl(value, fallback = '#') {
  try {
    const url = new URL(value, location.origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : fallback;
  } catch { return fallback; }
}
function safeResourceUrl(value, fallback = '#') { return htmlText(safeDomUrl(value, fallback)); }
window.fetch = (input, options = {}) => {
  const request = input instanceof Request ? input : null;
  const url = new URL(request ? request.url : String(input), location.href);
  const method = String(options.method || (request && request.method) || 'GET').toUpperCase();
  if (url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const headers = new Headers(options.headers || (request && request.headers));
    const token = document.cookie.split('; ').find(value => value.startsWith('avito_csrf='));
    if (token) headers.set('X-CSRF-Token', decodeURIComponent(token.slice('avito_csrf='.length)));
    options = {...options, headers};
  }
  return nativeFetch(input, options);
};
function escapeToastText(value) { const el = document.createElement("span"); el.textContent = String(value); return el.innerHTML; }
// Avito Parser Pro - Core JavaScript Helpers

// ==============================================================================
// TOAST NOTIFICATIONS SYSTEM
// ==============================================================================
function showToast(message, type = 'info', title = '') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const id = 'toast_' + Date.now();
  const toast = document.createElement('div');
  toast.id = id;

  let bgClass = 'bg-slate-800 border-slate-700 text-slate-200';
  let iconHtml = '<i data-lucide="info" class="w-5 h-5 text-blue-400"></i>';

  if (type === 'success') {
    bgClass = 'bg-slate-800 border-emerald-500/40 text-emerald-100';
    iconHtml = '<i data-lucide="check-circle-2" class="w-5 h-5 text-emerald-400"></i>';
    if (!title) title = 'Успешно';
  } else if (type === 'error') {
    bgClass = 'bg-slate-800 border-rose-500/40 text-rose-100';
    iconHtml = '<i data-lucide="alert-circle" class="w-5 h-5 text-rose-400"></i>';
    if (!title) title = 'Ошибка';
  } else if (type === 'warning') {
    bgClass = 'bg-slate-800 border-amber-500/40 text-amber-100';
    iconHtml = '<i data-lucide="alert-triangle" class="w-5 h-5 text-amber-400"></i>';
    if (!title) title = 'Внимание';
  } else {
    if (!title) title = 'Информация';
  }

  toast.className = `flex items-start gap-3 p-4 rounded-xl border shadow-xl transition-all duration-300 transform translate-y-2 opacity-0 ${bgClass}`;
  toast.innerHTML = `
    <div class="flex-shrink-0 mt-0.5">${iconHtml}</div>
    <div class="flex-1 text-sm">
      <div class="font-semibold text-white">${escapeToastText(title)}</div>
      <div class="text-slate-300 mt-0.5 text-xs">${escapeToastText(message)}</div>
    </div>
    <button onclick="dismissToast('${id}')" class="text-slate-400 hover:text-white transition">
      <i data-lucide="x" class="w-4 h-4"></i>
    </button>
  `;

  container.appendChild(toast);
  if (window.lucide) lucide.createIcons();

  // Trigger animation
  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  });

  // Auto dismiss after 4.5s
  setTimeout(() => {
    dismissToast(id);
  }, 4500);
}

function dismissToast(id) {
  const toast = document.getElementById(id);
  if (!toast) return;
  toast.classList.add('opacity-0', 'translate-x-4');
  setTimeout(() => toast.remove(), 300);
}


// ==============================================================================
// FORMATTING UTILITIES
// ==============================================================================
function formatPrice(num) {
  if (num === null || num === undefined || isNaN(num)) return 'Цена не указана';
  return new Intl.NumberFormat('ru-RU').format(num) + ' ₽';
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  } catch (e) {
    return dateStr;
  }
}

function formatRelativeTime(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffSec = Math.round((now - d) / 1000);
    if (diffSec < 60) return 'только что';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)} мин. назад`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} ч. назад`;
    return formatDate(dateStr);
  } catch (e) {
    return dateStr;
  }
}


// ==============================================================================
// GLOBAL API HELPER
// ==============================================================================
async function apiFetch(url, options = {}) {
  try {
    const res = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {})
      },
      ...options
    });

    if (!res.ok) {
      let errData = {};
      try {
        errData = await res.json();
      } catch (e) {}
      throw new Error(errData.detail || `Ошибка сервера (${res.status})`);
    }

    // Check if JSON response
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return await res.json();
    }
    return res;
  } catch (error) {
    console.error(`API Fetch Error (${url}):`, error);
    throw error;
  }
}


// ==============================================================================
// GLOBAL HEADER STATUS POLLER
// ==============================================================================
async function updateGlobalHeaderStatus() {
  try {
    const data = await apiFetch('/api/dashboard');
    const badgeEl = document.getElementById('global-monitor-status-badge');
    const itemsCntEl = document.getElementById('global-items-count');
    const searchesCntEl = document.getElementById('global-searches-count');

    if (itemsCntEl && data.stats) {
      itemsCntEl.innerText = data.stats.total_items.toLocaleString('ru-RU');
    }
    if (searchesCntEl && data.stats) {
      searchesCntEl.innerText = `${data.stats.active_searches} / ${data.stats.total_searches}`;
    }

    const sidebarItems = document.getElementById('sidebar-total-items-pill');
    const sidebarSearches = document.getElementById('sidebar-active-searches-pill');
    if (sidebarItems && data.stats) sidebarItems.textContent = data.stats.total_items.toLocaleString('ru-RU');
    if (sidebarSearches && data.stats) sidebarSearches.textContent = data.stats.active_searches.toLocaleString('ru-RU');
    if (badgeEl && data.monitoring) {
      if (data.monitoring.is_running) {
        badgeEl.className = 'header-status running';
        badgeEl.innerHTML = '<span class="status-dot"></span><span>Мониторинг включён</span>';
      } else {
        badgeEl.className = 'header-status';
        badgeEl.innerHTML = '<span class="status-dot"></span><span>Мониторинг на паузе</span>';
      }
    }
  } catch (e) {
    const badge = document.getElementById('global-monitor-status-badge');
    if (badge) {
      badge.className = 'header-status disconnected';
      badge.innerHTML = '<span class="status-dot"></span><span>Нет соединения</span>';
    }
  }
}

async function handleLogout() {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  window.location.href = '/login';
}


window.showToast = showToast;
window.dismissToast = dismissToast;
window.formatPrice = formatPrice;
window.formatDate = formatDate;
window.formatRelativeTime = formatRelativeTime;
window.apiFetch = apiFetch;
window.updateGlobalHeaderStatus = updateGlobalHeaderStatus;
window.handleLogout = handleLogout;
// Initialize on page load
function initGlobalApp() {
  if (window.lucide) {
    lucide.createIcons();
  }
  updateGlobalHeaderStatus();
  updateAccessAttention();
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select,[contenteditable]')) return;
    const field = document.getElementById('global-search');
    if (field) { event.preventDefault(); field.focus(); }
  });
  setInterval(updateGlobalHeaderStatus, 15000);
  setInterval(updateAccessAttention, 10000);
}

async function updateAccessAttention() {
  const banner = document.getElementById('access-attention');
  if (!banner) return;
  try {
    const data = await apiFetch('/api/parser/attention');
    const entry = data.jobs?.find(job => job.status === 'waiting_human') || data.jobs?.at(-1) || data.searches?.[0];
    banner.hidden = !entry;
    if (!entry) { document.title = document.title.replace(/^! /, ''); return; }
    const description = document.getElementById('access-attention-text');
    if (description.textContent !== entry.message) description.textContent = entry.message;
    const dismiss = document.getElementById('access-dismiss');
    dismiss.hidden = !entry.job_id || entry.status === 'waiting_human' || !canMutate();
    dismiss.dataset.jobId = entry.job_id || '';
    document.getElementById('access-attention-link').href = entry.job_id ? '/parser?job=' + encodeURIComponent(entry.job_id) : '/parser?manual=1&url=' + encodeURIComponent(entry.url);
    if (!document.title.startsWith('! ')) document.title = '! ' + document.title;
  } catch { /* A parser-specific warning must not replace the page's connection state. */ }
}

async function dismissAccessAttention() {
  const job = document.getElementById('access-dismiss').dataset.jobId;
  if (!job) return;
  try {
    await apiFetch('/api/parser/jobs/' + encodeURIComponent(job) + '/dismiss', {method:'POST'});
    await updateAccessAttention();
  } catch (error) { showToast(error.message, 'error'); }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initGlobalApp);
} else {
  initGlobalApp();
}

// Mobile navigation remains keyboard accessible and closes on Escape.
const menuButton = document.getElementById('mobile-menu');
let menuReturnFocus = null;
function setMobileMenu(open) {
  if (open) menuReturnFocus = document.activeElement;
  document.body.classList.toggle('nav-open', open);
  menuButton?.setAttribute('aria-expanded', String(open));
  const sidebar = document.getElementById('app-sidebar');
  if (sidebar) sidebar.inert = window.innerWidth < 768 && !open;
  if (open) sidebar?.querySelector('a')?.focus();
  else if (menuReturnFocus?.isConnected) { menuReturnFocus.focus(); menuReturnFocus = null; }
}
menuButton?.addEventListener('click', () => setMobileMenu(!document.body.classList.contains('nav-open')));
document.querySelector('.nav-shade')?.addEventListener('click', () => { setMobileMenu(false); menuButton?.focus(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && document.body.classList.contains('nav-open')) { setMobileMenu(false); menuButton?.focus(); } });
window.addEventListener('resize', () => setMobileMenu(false));
setMobileMenu(false);
