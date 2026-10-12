'use strict';
const loginButton = document.getElementById('btn-login-submit');
const loginError = document.getElementById('login-error-box');
const loginErrorText = document.getElementById('login-error-text');
const passwordInput = document.getElementById('input-password');
let retryTimer = null;
function loginButtonText(text, icon = 'arrow-right') {
  loginButton.textContent = text + ' ';
  const glyph = document.createElement('i'); glyph.dataset.lucide = icon; loginButton.append(glyph);
  if (window.lucide) lucide.createIcons();
}
document.getElementById('login-show-password').addEventListener('click', event => {
  const visible = passwordInput.type === 'password';
  passwordInput.type = visible ? 'text' : 'password';
  const button = event.currentTarget;
  button.setAttribute('aria-pressed', String(visible));
  button.setAttribute('aria-label', visible ? 'Скрыть пароль' : 'Показать пароль');
  button.innerHTML = '<i data-lucide="' + (visible ? 'eye-off' : 'eye') + '"></i>';
  if (window.lucide) lucide.createIcons();
});
passwordInput.addEventListener('keyup', event => {
  document.getElementById('login-caps-lock').hidden = !event.getModifierState('CapsLock');
});
async function handleLogin(event) {
  event.preventDefault();
  if (loginButton.disabled) return;
  loginButton.disabled = true; loginError.hidden = true; loginButtonText('Входим…', 'loader-circle');
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: document.getElementById('input-username').value.trim(), password: passwordInput.value})
    });
    if (response.ok) {
      loginButtonText('Вход выполнен', 'check');
      // Authentication stays in the server's HttpOnly session cookie.
      try { localStorage.removeItem('access_token'); } catch {}
      location.href = '/'; return;
    }
    const data = await response.json().catch(() => ({}));
    loginErrorText.textContent = data.detail || 'Не удалось войти. Повторите попытку.';
    loginError.hidden = false;
    if (response.status === 429) {
      let remaining = Math.max(1, Math.min(300, Number(response.headers.get('Retry-After')) || 60));
      loginButtonText('Повторить через ' + remaining + ' с', 'clock');
      clearInterval(retryTimer);
      retryTimer = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
          clearInterval(retryTimer); retryTimer = null;
          loginButton.disabled = false; loginButtonText('Войти');
        } else loginButtonText('Повторить через ' + remaining + ' с', 'clock');
      }, 1000);
      return;
    }
  } catch {
    loginErrorText.textContent = 'Сервер недоступен. Проверьте, что приложение запущено.';
    loginError.hidden = false;
  }
  loginButton.disabled = false; loginButtonText('Войти');
}
if (window.lucide) lucide.createIcons();
