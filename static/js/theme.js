(() => {
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.querySelectorAll('.theme-switch').forEach(button => {
      button.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
      button.setAttribute('aria-label', button.textContent);
    });
  }
  let theme = 'dark';
  try { theme = localStorage.getItem('avito-theme') || theme; } catch {}
  apply(theme);
  document.addEventListener('DOMContentLoaded', () => {
    apply(theme);
    document.querySelectorAll('.theme-switch').forEach(button => button.addEventListener('click', () => {
      theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      apply(theme);
      try { localStorage.setItem('avito-theme', theme); } catch {}
    }));
    document.querySelectorAll('.nav-item.active').forEach(link => link.setAttribute('aria-current', 'page'));
    document.querySelectorAll('[id$="-modal"]').forEach(modal => {
      modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true');
      const heading = modal.querySelector('h2, h3');
      if (heading) { heading.id ||= modal.id + '-heading'; modal.setAttribute('aria-labelledby', heading.id); }
    });
  });
})();
