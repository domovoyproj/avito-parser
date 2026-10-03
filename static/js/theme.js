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
      let returnFocus = null;
      let wasOpen = false;
      const visible = () => !modal.classList.contains('hidden') && getComputedStyle(modal).display !== 'none';
      const focusables = () => [...modal.querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(el => el.offsetParent !== null && !el.disabled);
      new MutationObserver(() => {
        const open = visible();
        if (open && !wasOpen) { returnFocus = document.activeElement; (focusables()[0] || modal).focus(); }
        if (!open && wasOpen && returnFocus?.isConnected) returnFocus.focus();
        wasOpen = open;
      }).observe(modal, {attributes: true, attributeFilter: ['class', 'style']});
      modal.tabIndex = -1;
      modal.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
          const close = modal.querySelector('button[onclick*="close"], button[aria-label*="Закрыть"]');
          if (close) close.click();
        }
        if (event.key !== 'Tab') return;
        const elements = focusables();
        const first = elements[0], last = elements.at(-1);
        if (!first) { event.preventDefault(); modal.focus(); }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
    });
  });
})();
