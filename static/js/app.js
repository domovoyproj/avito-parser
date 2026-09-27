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

    if (badgeEl && data.monitoring) {
      if (data.monitoring.is_running) {
        badgeEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
        badgeEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse-live"></span> Мониторинг активен';
      } else {
        badgeEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-700/60 text-slate-400 border border-slate-600/40';
        badgeEl.innerHTML = '<span class="w-2 h-2 rounded-full bg-slate-500"></span> Мониторинг на паузе';
      }
    }
  } catch (e) {
    // Ignore silent poll error
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
  setInterval(updateGlobalHeaderStatus, 15000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initGlobalApp);
} else {
  initGlobalApp();
}

// Mobile navigation remains keyboard accessible and closes on Escape.
const menuButton = document.getElementById('mobile-menu');
function setMobileMenu(open) {
  document.body.classList.toggle('nav-open', open);
  menuButton?.setAttribute('aria-expanded', String(open));
  const sidebar = document.getElementById('app-sidebar');
  if (sidebar) sidebar.inert = window.innerWidth < 768 && !open;
  if (open) sidebar?.querySelector('a')?.focus();
}
menuButton?.addEventListener('click', () => setMobileMenu(!document.body.classList.contains('nav-open')));
document.querySelector('.nav-shade')?.addEventListener('click', () => { setMobileMenu(false); menuButton?.focus(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && document.body.classList.contains('nav-open')) { setMobileMenu(false); menuButton?.focus(); } });
window.addEventListener('resize', () => setMobileMenu(false));
setMobileMenu(false);
