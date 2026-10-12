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
      empty(container, deals ? 'Пока нет предложений с высокой оценкой' : 'Здесь появятся ваши объявления', deals ? 'Посмотрите весь каталог или добавьте ещё один поиск. Оценка — не гарантия выгодной покупки.' : 'Добавьте поиск или запустите разовый сбор.');
      return;
    }
    items.slice(0, 3).forEach(item => {
      const card = node('article', 'deal-card');
      const img = node('img', 'deal-image');
      img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
      img.src = safeUrl(item.main_image) || '/static/img/no-image.svg';
      img.addEventListener('error', () => { img.src = '/static/img/no-image.svg'; }, { once:true });
      const body = node('div', 'deal-body');
      const title = node('a', 'deal-title', item.title || 'Без названия');
      title.href = '/items?query=' + encodeURIComponent(item.title || item.id);
      body.append(node('div', 'deal-price', formatPrice(item.price)), title);
      if (item.deal_score != null) body.append(node('span', 'score-badge', item.deal_score + ' · Оценка'));
      body.append(node('p', 'workspace-muted', item.address || 'Место не указано'));
      card.append(img, body); container.append(card);
    });
  }
  function renderRecent(items) {
    const container = byId('overview-recent'); container.replaceChildren();
    if (!items.length) { empty(container, 'Пока нет объявлений', 'Начните с нового сбора — находки появятся здесь.'); return; }
    items.slice(0, 4).forEach(item => {
      const row = node('a', 'recent-row');
      row.href = '/items?query=' + encodeURIComponent(item.title || item.id);
      const img = node('img', '');
      img.src = safeUrl(item.main_image) || '/static/img/no-image.svg';
      img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
      img.addEventListener('error', () => { img.src = '/static/img/no-image.svg'; }, {once: true});
      const label = node('div', '');
      label.append(node('strong', '', item.title || 'Без названия'), node('small', '', item.address || 'Место не указано'));
      row.append(img, label, node('span', 'row-price', formatPrice(item.price)));
      container.append(row);
    });
  }
  function renderBudget(distribution) {
    const container = byId('overview-budget'); container.replaceChildren();
    const entries = [['under_10k', 'До 10 000 ₽'], ['10k_50k', '10 000 — 50 000 ₽'], ['50k_100k', '50 000 — 100 000 ₽'], ['over_100k', 'От 100 000 ₽']];
    const total = Object.values(distribution).reduce((sum, count) => sum + Number(count || 0), 0);
    if (!total) { empty(container, 'Данных пока нет', 'Соберите объявления, чтобы увидеть распределение.'); return; }
    entries.forEach(([key, name]) => {
      const count = Number(distribution[key] || 0);
      const row = node('div', 'budget-row');
      const label = node('div', '');
      label.append(node('span', '', name), node('strong', '', number(count)));
      const track = node('div', 'budget-track');
      const bar = node('span', ''); bar.style.width = Math.round(count / total * 100) + '%';
      track.append(bar); row.append(label, track); container.append(row);
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
      byId('overview-monitor').classList.toggle('running', monitor.is_running);
      text('overview-toggle', monitor.is_running ? 'Приостановить' : 'Включить');
      text('overview-checking', monitor.current_checking ? `Сейчас проверяем: ${monitor.current_checking}` : 'Новые объявления и изменения цен — в одном месте.');
      text('overview-last', formatRelativeTime(monitor.last_run));
      text('overview-next', monitor.is_running && monitor.next_run ? formatDate(monitor.next_run) : 'Не запланирована');
      text('overview-telegram', data.telegram_configured ? 'Telegram настроен · изменить параметры' : 'Подключите Telegram для уведомлений');
      renderSearches(data.searches || []);
      renderItems('overview-deals', data.top_deals || [], true);
      renderRecent(data.recent_items || []);
      renderBudget(data.price_distribution || {});
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
      byId('overview-toggle').disabled = !canMutate() || !loaded || acting;
      byId('overview-check').disabled = !canMutate() || !loaded || acting || !activeSearches;
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
      byId('overview-toggle').disabled = !canMutate() || !loaded;
      byId('overview-check').disabled = !canMutate() || !loaded || !activeSearches;
    }
  }
  byId('overview-toggle').addEventListener('click', () => action('/api/monitoring/toggle', 'Состояние мониторинга изменено'));
  byId('overview-check').addEventListener('click', () => action('/api/monitoring/check-all', 'Проверка запущена. Результаты появятся после завершения.'));
  byId('overview-retry').addEventListener('click', refresh);
  text('overview-date', new Date().toLocaleDateString('ru-RU', {day:'numeric', month:'long', weekday:'long'}));
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
