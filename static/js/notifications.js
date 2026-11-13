(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const node = (tag, text, className = '') => { const el = document.createElement(tag); if (text != null) el.textContent = text; el.className = className; return el; };
  const labels = {pending:'В очереди', sending:'Отправляется', sent:'Доставлено', failed:'Ошибка', skipped:'Пропущено'};
  const reasons = {'quiet hours':'Тихие часы', 'telegram disabled':'Telegram не подключён', 'stale or unavailable':'Цена изменилась или лот недоступен', 'delivery error':'Не удалось связаться с сервисом', 'invalid payload':'Ошибка данных события'};
  let busy = false;
  let repeating = false;
  let chats = [];
  let prefsBusy = false;
  const fields = {min_deal_score:'score', below_market_pct:'below-pct', quiet_hours_start:'start', quiet_hours_end:'end', notify_new:'new', notify_drops:'drops', only_below_market:'below', delivery_only:'delivery', quiet_hours_enabled:'quiet'};
  const isCheck = key => ['notify_new','notify_drops','only_below_market','delivery_only','quiet_hours_enabled'].includes(key);
  function populatePreferences() {
    const chat=chats.find(chat => String(chat.chat_id) === byId('prefs-chat').value);
    if (!chat) return;
    Object.entries(fields).forEach(([key,id]) => {byId('prefs-'+id)[isCheck(key) ? 'checked' : 'value'] = chat[key];});
  }
  async function loadPreferences() {
    if (prefsBusy) return;
    prefsBusy=true; byId('prefs-reload').disabled=true;
    try {
      const data=await apiFetch('/api/notifications/preferences', {signal:AbortSignal.timeout(15000)});
      chats=data.chats || [];
      byId('prefs-chat').replaceChildren(...chats.map(chat => {const option=node('option','Чат ' + chat.chat_id); option.value=String(chat.chat_id); return option;}));
      byId('prefs-form').hidden=!chats.length;
      byId('prefs-status').textContent=chats.length ? 'Правила загружены. После изменений нажмите «Сохранить правила».' : 'Добавьте ID получателей и токен бота в настройках Telegram. Затем загрузите правила.';
      populatePreferences();
    } catch { byId('prefs-status').textContent='Не удалось загрузить правила. Нажмите «Загрузить правила», чтобы повторить.'; }
    finally { prefsBusy=false; byId('prefs-reload').disabled=false; }
  }
  byId('prefs-chat').addEventListener('change',populatePreferences);
  byId('prefs-reload').addEventListener('click',loadPreferences);
  byId('prefs-preset').addEventListener('click',() => {
    byId('prefs-score').value=70; byId('prefs-new').checked=true; byId('prefs-drops').checked=true;
    byId('prefs-status').textContent='Выбраны новые лоты и снижения цены с оценкой от 70. Сохраните правила, чтобы применить.';
  });
  byId('prefs-form').addEventListener('submit',async event => {
    event.preventDefault(); if (prefsBusy) return;
    prefsBusy=true; byId('prefs-save').disabled=true; byId('prefs-chat').disabled=true; byId('prefs-reload').disabled=true;
    const chatId=byId('prefs-chat').value;
    const payload=Object.fromEntries(Object.entries(fields).map(([key,id]) => [key, isCheck(key) ? byId('prefs-'+id).checked : Number(byId('prefs-'+id).value)]));
    try {
      const saved=await apiFetch('/api/notifications/preferences/' + encodeURIComponent(chatId),{method:'POST',body:JSON.stringify(payload)});
      chats=chats.map(chat => String(chat.chat_id) === chatId ? saved : chat);
      byId('prefs-status').textContent='Правила сохранены. Они применяются перед отправкой каждого уведомления.';
      showToast('Правила уведомлений сохранены','success');
    } catch(error) { byId('prefs-status').textContent='Не удалось сохранить правила: ' + error.message; }
    finally { prefsBusy=false; byId('prefs-save').disabled=false; byId('prefs-chat').disabled=false; byId('prefs-reload').disabled=false; }
  });
  const stamp = value => value ? formatDate(new Date(value * 1000).toISOString()) : '—';
  function render(events) {
    const container = byId('inbox-events'); container.replaceChildren();
    if (!events.length) { const empty = node('div', null, 'workspace-empty'); empty.append(node('strong','Пока нет уведомлений'), node('span','Они появятся после новых находок или снижения цены. Проверьте подключения и включите поиск.')); container.append(empty); return; }
    const wrap = node('div', null, 'inbox-table-wrap');
    const table = node('table', null, 'inbox-table');
    const head = node('thead'), heading = node('tr');
    ['Находка', 'Канал', 'Состояние', 'Создано', 'Действие'].forEach(text => {const th=node('th',text); th.scope='col'; heading.append(th);});
    head.append(heading); table.append(head);
    const body = node('tbody');
    events.forEach(event => {
      const row = node('tr'), title = node('td');
      title.append(node('strong', event.title), node('small', `${event.search_name} · ${event.type === 'price_drop' ? 'Снижение цены' : 'Новая находка'}`), node('small', formatPrice(event.price)));
      const state = node('td'); state.append(node('span', labels[event.status] || event.status, `delivery-state ${event.status}`));
      if (event.last_error) state.append(node('small', reasons[event.last_error] || (/^HTTP \d{3}$/.test(event.last_error) ? event.last_error : 'Проверьте настройки подключения')));
      if (event.status === 'pending' && event.available_at * 1000 > Date.now()) state.append(node('small', 'Повтор после ' + stamp(event.available_at)));
      const action = node('td');
      if (event.status === 'failed') { const retry = node('button', 'Повторить', 'button-secondary'); retry.disabled = repeating; retry.addEventListener('click', async () => {
        repeating = true; retry.disabled = true;
        try { await apiFetch(`/api/notifications/${event.id}/retry`, {method:'POST'}); showToast('Уведомление возвращено в очередь', 'success'); }
        catch(error) { showToast(error.message, 'error'); }
        finally { repeating = false; retry.disabled = false; await refresh(); }
      }); action.append(retry); }
      else if (event.url) { try { const url = new URL(event.url); if (url.protocol === 'https:' && (url.hostname === 'avito.ru' || url.hostname.endsWith('.avito.ru')) && !url.username && !url.password) { const link=node('a','Открыть ↗'); link.href=url.href; link.target='_blank'; link.rel='noopener noreferrer'; action.append(link); } } catch {} }
      row.append(title, node('td', event.channel === 'telegram' ? 'Telegram' : 'Webhook'), state, node('td', stamp(event.created_at)), action); body.append(row);
    });
    table.append(body); wrap.append(table); container.append(wrap);
  }
  async function refresh() {
    if (busy) return;
    busy=true; byId('inbox-refresh').disabled=true; byId('inbox-filter').disabled=true;
    try {
      const filter=byId('inbox-filter').value;
      const data=await apiFetch('/api/notifications' + (filter ? '?status=' + encodeURIComponent(filter) : ''), {signal:AbortSignal.timeout(15000)});
      const counts=data.counts || {};
      byId('inbox-pending').textContent=((counts.pending || 0) + (counts.sending || 0)).toLocaleString('ru-RU');
      ['sent','failed','skipped'].forEach(key => {byId('inbox-'+key).textContent=Number(counts[key] || 0).toLocaleString('ru-RU');});
      byId('inbox-channels').textContent=`Telegram: ${data.telegram_configured ? 'настроен' : 'не подключён'} · Webhook: ${data.webhook_configured ? 'настроен' : 'не подключён'}. Наличие настроек не подтверждает доставку.`;
      render(data.events || []); byId('inbox-error').hidden=true;
    } catch { byId('inbox-error').hidden=false; byId('inbox-channels').textContent='Статус подключений недоступен. Повторите загрузку.'; }
    finally { busy=false; byId('inbox-refresh').disabled=false; byId('inbox-filter').disabled=false; }
  }
  ['inbox-retry','inbox-refresh'].forEach(id => byId(id).addEventListener('click',refresh));
  byId('inbox-filter').addEventListener('change',refresh);
  setInterval(() => { if (!document.hidden) refresh(); },15000);
  refresh();
  loadPreferences();
})();
