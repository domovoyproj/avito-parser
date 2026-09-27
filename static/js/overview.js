(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const text = (id, value) => { byId(id).textContent = value; };
  const number = value => Number(value || 0).toLocaleString('ru-RU');
  let loading = false;
  let acting = false;
  let loaded = false;
  let activeSearches = 0;
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  };
  function safeUrl(value, avitoOnly = false) {
    try {
      const url = new URL(value);
      if (!['https:', 'http:'].includes(url.protocol)) return null;
      if (avitoOnly && url.hostname !== 'avito.ru' && !url.hostname.endsWith('.avito.ru')) return null;
      return url.href;
    } catch { return null; }
  }
  function empty(container, heading, description) {
    const box = node('div', 'workspace-empty');
    box.style.gridColumn = '1 / -1';
    box.append(node('strong', '', heading), node('span', '', description));
    container.append(box);
  }
  function renderItems(id, items, deals = false) {
    const container = byId(id);
    container.replaceChildren();
    if (!items.length) {
      empty(container, deals ? 'Выгодные находки ещё впереди' : 'Здесь появятся ваши объявления', deals ? 'После сбора оценим предложения и покажем лучшие.' : 'Добавьте поиск или запустите разовый сбор.');
      return;
    }
    items.slice(0, 6).forEach(item => {
      const card = node('article', 'deal-card');
      const img = node('img', 'deal-image');
      img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
      img.src = safeUrl(item.main_image) || '/static/img/no-image.svg';
      img.addEventListener('error', () => { img.src = '/static/img/no-image.svg'; }, { once:true });
      const body = node('div', 'deal-body');
      body.append(node('span', 'state-pill', `${item.deal_score ?? '—'} / 100`));
      const url = safeUrl(item.url, true);
      const title = node(url ? 'a' : 'p', '', item.title || 'Без названия');
      if (url) { title.href = url; title.target = '_blank'; title.rel = 'noopener noreferrer'; }
      body.append(title, node('div', 'deal-price', formatPrice(item.price)), node('p', 'workspace-muted', item.address || 'Местоположение не указано'));
      card.append(img, body); container.append(card);
    });
  }
  function renderSearches(searches) {
    const container = byId('overview-searches'); container.replaceChildren();
    if (!searches.length) { empty(container, 'Пока нет поисков', 'Создайте поиск, чтобы отслеживать интересующие товары.'); return; }
    searches.slice(0, 5).forEach(search => {
      const row = node('div', 'search-row');
      const label = node('div', '');
      label.append(node('strong', '', search.name), node('p', '', `Проверка каждые ${search.check_interval_min} мин.`));
      row.append(label, node('span', `state-pill${search.enabled ? '' : ' paused'}`, search.enabled ? 'Активен' : 'На паузе'));
      container.append(row);
    });
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const data = await apiFetch('/api/dashboard', { signal:AbortSignal.timeout(15000) });
      const stats = data.stats;
      text('metric-items', number(stats.total_items));
      text('metric-today', `+${number(stats.items_today)} сегодня`);
      text('metric-deals', number(stats.total_hot_deals));
      text('metric-searches', number(stats.active_searches));
      text('metric-total-searches', `Всего поисков: ${number(stats.total_searches)}`);
      text('metric-drops', number(stats.total_price_drops));
      text('metric-drops-today', `${number(stats.price_drops_today)} сегодня`);
      byId('onboarding').hidden = stats.total_searches > 0 || stats.total_items > 0;
      const monitor = data.monitoring;
      text('overview-monitor', monitor.is_running ? 'Работает' : 'На паузе');
      text('overview-toggle', monitor.is_running ? 'Приостановить' : 'Включить');
      text('overview-checking', monitor.current_checking ? `Сейчас проверяем: ${monitor.current_checking}` : 'Новые объявления и изменения цен — в одном месте.');
      text('overview-last', formatRelativeTime(monitor.last_run));
      text('overview-next', monitor.is_running && monitor.next_run ? formatDate(monitor.next_run) : 'Не запланирована');
      text('overview-telegram', data.telegram_configured ? 'Telegram настроен · изменить параметры' : 'Подключите Telegram для уведомлений');
      renderSearches(data.searches || []);
      renderItems('overview-deals', data.top_deals || [], true);
      renderItems('overview-recent', data.recent_items || []);
      text('overview-updated', `Обновлено в ${new Date().toLocaleTimeString('ru-RU')} · Автообновление каждые 30 секунд`);
      byId('overview-error').hidden = true;
      loaded = true;
      activeSearches = stats.active_searches;
    } catch (error) {
      text('overview-error-text', 'Не удалось обновить данные. Проверьте подключение и повторите попытку.');
      byId('overview-error').hidden = false;
      text('overview-updated', loaded ? 'Показаны последние полученные данные. Соединение прервано.' : 'Данные пока недоступны.');
    } finally {
      loading = false;
      byId('overview-toggle').disabled = !loaded || acting;
      byId('overview-check').disabled = !loaded || acting || !activeSearches;
    }
  }
  async function action(path, message) {
    if (acting) return;
    acting = true;
    byId('overview-toggle').disabled = byId('overview-check').disabled = true;
    try {
      await apiFetch(path, { method:'POST' });
      showToast(message, 'success');
      await refresh();
    } catch (error) { showToast(error.message, 'error'); }
    finally {
      acting = false;
      byId('overview-toggle').disabled = !loaded;
      byId('overview-check').disabled = !loaded || !activeSearches;
    }
  }
  byId('overview-toggle').addEventListener('click', () => action('/api/monitoring/toggle', 'Состояние мониторинга изменено'));
  byId('overview-check').addEventListener('click', () => action('/api/monitoring/check-all', 'Проверка запущена. Результаты появятся после завершения.'));
  byId('overview-retry').addEventListener('click', refresh);
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
