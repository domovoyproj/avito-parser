'use strict';
let parserJobId = null;
let parserPoll = null;
let parserNoticeKey = '';
const parserTerminal = new Set(['completed', 'error', 'blocked', 'cancelled']);
const parserStatusNames = {pending:'Подготовка сбора', running:'Собираем объявления', waiting_human:'Авито ждёт ручной проверки', blocked:'Авито ограничил доступ', completed:'Сбор завершён', error:'Сбор прерван', cancelled:'Сбор остановлен'};

function parserSearchUrl() {
  const text = document.getElementById('parser-url').value.trim();
  if (!text) throw new Error('Введите название товара или ссылку на поиск.');
  if (/^(https?:\/\/|www\.)/i.test(text) || /^avito\.ru/i.test(text)) {
    const url = new URL(/^https?:/i.test(text) ? text : 'https://' + text);
    if (!['http:','https:'].includes(url.protocol) || !(url.hostname === 'avito.ru' || url.hostname.endsWith('.avito.ru')) || url.username || url.password) throw new Error('Ссылка должна вести на avito.ru.');
    return url.href;
  }
  return 'https://www.avito.ru/rossiya?q=' + encodeURIComponent(text);
}

function parserError(id, message) {
  const el = document.getElementById(id);
  el.hidden = !message;
  el.textContent = message || '';
}

async function startParsing(event) {
  event.preventDefault();
  parserError('parser-form-error', '');
  const button = document.getElementById('btn-start-parse');
  button.disabled = true;
  try {
    const data = await apiFetch('/api/parser/start', {method:'POST', body:JSON.stringify({
      url:parserSearchUrl(), max_pages:Number(document.getElementById('parser-max-pages').value),
      engine:document.getElementById('parser-engine').value,
      headless:document.getElementById('parser-headless').value === 'true',
      save_to_db:document.getElementById('parser-save-db').checked
    })});
    await attachParserJob(data.job_id);
  } catch (error) {
    parserError('parser-form-error', error.message);
    button.disabled = !canMutate();
  }
}

async function attachParserJob(id) {
  clearTimeout(parserPoll);
  parserJobId = id;
  sessionStorage.setItem('avito-parser-job', id);
  const url = new URL(location.href);
  url.searchParams.set('job', id);
  history.replaceState({}, '', url);
  document.getElementById('live-progress-panel').hidden = false;
  document.getElementById('results-panel').hidden = true;
  parserError('parser-job-error', '');
  await pollParserJob();
}

async function pollParserJob() {
  if (!parserJobId) return;
  const requestedJob = parserJobId;
  try {
    const job = await apiFetch('/api/parser/jobs/' + encodeURIComponent(parserJobId));
    if (requestedJob !== parserJobId) return;
    renderParserJob(job);
    if (!parserTerminal.has(job.status)) parserPoll = setTimeout(pollParserJob, 2000);
    updateAccessAttention();
  } catch (error) {
    parserError('parser-job-error', 'Не удалось получить состояние сбора. ' + error.message);
    document.getElementById('btn-start-parse').disabled = !canMutate();
    const button = document.createElement('button');
    button.className = 'button-secondary';
    button.textContent = 'Проверить снова';
    button.addEventListener('click', pollParserJob);
    document.getElementById('parser-job-error').appendChild(button);
  }
}

function renderParserJob(job) {
  const field = document.getElementById('parser-url');
  if (!field.value.trim()) {
    try { field.value = new URL(job.url).searchParams.get('q') || job.url; } catch { field.value = job.url || ''; }
  }
  const done = parserTerminal.has(job.status);
  document.getElementById('parser-progress-description').textContent = done ? (job.save_to_db && job.saved_count ? 'Полученные объявления сохранены. Их можно открыть в каталоге.' : 'Сбор остановлен. Полученные данные доступны в результатах этой задачи.') : 'Можно перейти на другую страницу — сбор продолжится на сервере.';
  document.getElementById('live-progress-panel').hidden = false;
  document.getElementById('btn-start-parse').disabled = !done || !canMutate();
  document.getElementById('parser-cancel').hidden = done;
  document.getElementById('live-status-text').textContent = job.outcome === 'partial' ? 'Сбор завершён частично' : (parserStatusNames[job.status] || 'Состояние сбора неизвестно');
  document.getElementById('live-page-val').textContent = (job.current_page || 0) + ' / ' + job.max_pages;
  document.getElementById('live-items-val').textContent = job.total_found ?? job.items_found ?? 0;
  document.getElementById('live-saved-val').textContent = job.saved_count ?? ((job.new_items_count || 0) + (job.price_dropped_count || 0));
  const elapsed = done ? job.elapsed_sec : (Date.now()/1000 - job.start_time);
  document.getElementById('live-timer-val').textContent = Math.max(0, Math.floor(elapsed || 0)) + ' с';
  const pct = Math.max(0, Math.min(100, job.progress_pct || 0));
  document.getElementById('live-progress-bar').style.width = pct + '%';
  document.querySelector('.progress-track').setAttribute('aria-valuenow', String(pct));
  document.getElementById('live-terminal-logs').textContent = (job.logs || []).join('\n');
  const challenge = ['blocked','waiting_human'].includes(job.status);
  document.getElementById('parser-access-panel').hidden = !challenge;
  document.getElementById('parser-human').hidden = job.status !== 'blocked';
  document.getElementById('parser-access-title').textContent = job.status === 'waiting_human' ? 'Пройдите проверку в открытом окне' : job.repeated_captcha ? 'Повторная блокировка · сбор остановлен' : 'Требуется проверка доступа';
  const remaining = job.waiting_until ? ' Осталось ' + Math.max(0, Math.ceil((job.waiting_until - Date.now()/1000)/60)) + ' мин.' : '';
  document.getElementById('parser-access-text').textContent = (job.access_message || 'Авито ограничил доступ. Автоматические попытки остановлены.') + remaining;
  parserError('parser-job-error', done && !challenge ? (job.errors || []).join(' ') : '');
  if (challenge && parserNoticeKey !== job.job_id + job.status) {
    parserNoticeKey = job.job_id + job.status;
    showToast(job.status === 'waiting_human' ? 'Ожидаем ручную проверку в открытом браузере.' : 'Авито ограничил доступ. Сбор остановлен.', 'warning');
  }
  if (done) renderParserResults(job);
}

function renderParserResults(job) {
  document.getElementById('results-panel').hidden = false;
  const items = job.items || [];
  document.getElementById('results-summary-text').textContent = 'Найдено: ' + (job.total_found || 0) + '. Новых в каталоге: ' + (job.new_items_count || 0) + '. Снижений цены: ' + (job.price_dropped_count || 0) + '.';
  document.getElementById('parser-catalog-link').hidden = !job.save_to_db || !job.saved_count;
  document.getElementById('parser-result-items').innerHTML = items.slice(0,12).map(item =>
    '<article class="deal-card"><img class="deal-image" src="' + safeResourceUrl(item.main_image || '/static/img/no-image.svg') + '" alt="" loading="lazy" onerror="this.onerror=null;this.src=\'/static/img/no-image.svg\'"><div class="deal-body"><strong class="product-price">' + htmlText(formatPrice(item.price)) + '</strong><h3 class="deal-title">' + htmlText(item.title) + '</h3><p class="workspace-muted">' + htmlText(item.address || 'Местоположение не указано') + '</p><a class="button-secondary" href="' + safeResourceUrl(item.url) + '" target="_blank" rel="noopener noreferrer">Открыть на Авито</a></div></article>'
  ).join('');
  const empty = document.getElementById('parser-empty-message');
  empty.hidden = items.length > 0;
  empty.textContent = job.outcome === 'empty' ? 'По этому запросу объявлений не найдено. Попробуйте другое название или снимите фильтры.' : 'Объявления не получены. Это не пустая выдача: сначала восстановите доступ к Авито.';
}

async function cancelParsing() {
  const button = document.getElementById('parser-cancel');
  button.disabled = true;
  try { await apiFetch('/api/parser/' + encodeURIComponent(parserJobId) + '/cancel', {method:'POST'}); await pollParserJob(); }
  catch (error) { parserError('parser-job-error', error.message); }
  finally { button.disabled = !canMutate(); }
}

async function retryHuman() {
  const button = document.getElementById('parser-human');
  button.disabled = true;
  try {
    const job = await apiFetch('/api/parser/jobs/' + encodeURIComponent(parserJobId) + '/retry-human', {method:'POST'});
    await attachParserJob(job.job_id);
  } catch (error) { parserError('parser-job-error', error.message + ' Видимое окно работает на компьютере, где запущен сервер.'); }
  finally { button.disabled = !canMutate(); }
}

async function addAsMonitoredSearch() {
  try {
    const url = parserSearchUrl();
    const name = prompt('Название постоянного поиска:', document.getElementById('parser-url').value.trim().slice(0,80));
    if (!name?.trim()) return;
    await apiFetch('/api/searches', {method:'POST', body:JSON.stringify({name:name.trim(),url,check_interval_min:10,enabled:true})});
    showToast('Поиск сохранён. Управлять расписанием можно в разделе «Поиски».','success');
  } catch (error) { parserError('parser-form-error', error.message); }
}

document.getElementById('parser-form').addEventListener('submit', startParsing);
document.getElementById('parser-cancel').addEventListener('click', cancelParsing);
document.getElementById('parser-human').addEventListener('click', retryHuman);
document.getElementById('parser-add-monitor').addEventListener('click', addAsMonitoredSearch);
document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
  document.getElementById('parser-url').value = button.dataset.preset;
  document.getElementById('parser-url').focus();
}));
const parserParams = new URLSearchParams(location.search);
if (parserParams.has('url')) document.getElementById('parser-url').value = parserParams.get('url');
if (parserParams.get('manual') === '1') document.getElementById('parser-headless').value = 'false';
const rememberedJob = parserParams.get('job') || (!parserParams.has('url') ? sessionStorage.getItem('avito-parser-job') : null);
if (rememberedJob) attachParserJob(rememberedJob);
window.addEventListener('pagehide', () => clearTimeout(parserPoll));
