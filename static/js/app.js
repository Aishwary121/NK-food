(() => {
  'use strict';
  document.documentElement.classList.remove('no-js');
  const toggle = document.querySelector('[data-nav-toggle]');
  const navigation = document.getElementById('main-navigation');
  if (toggle && navigation) {
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      navigation.classList.toggle('is-open', open);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        toggle.setAttribute('aria-expanded', 'false');
        navigation.classList.remove('is-open');
      }
    });
  }
  document.querySelectorAll('[data-dismiss-message]').forEach(button => {
    button.addEventListener('click', () => button.closest('.alert')?.remove());
  });
  document.querySelectorAll('[data-print]').forEach(button => {
    button.addEventListener('click', () => window.print());
  });
  // Destructive/finalizing actions can opt into an accessible confirmation dialog.
  const dialog = document.getElementById('confirm-dialog');
  let pendingForm = null;
  let pendingSubmitter = null;
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', event => {
      const confirmation = event.submitter?.dataset.confirm || form.dataset.confirm;
      if (!confirmation) return;
      if (form.dataset.confirmed === 'true') return;
      if (!dialog || typeof dialog.showModal !== 'function') {
        if (!window.confirm(confirmation)) event.preventDefault();
        return;
      }
      event.preventDefault();
      pendingForm = form;
      pendingSubmitter = event.submitter;
      document.getElementById('confirm-message').textContent = confirmation;
      dialog.returnValue = '';
      dialog.showModal();
    });
  });
  if (dialog) {
    dialog.addEventListener('close', () => {
      if (dialog.returnValue === 'confirm' && pendingForm) {
        pendingForm.dataset.confirmed = 'true';
        if (pendingSubmitter) {
          pendingForm.requestSubmit(pendingSubmitter);
        } else {
          pendingForm.requestSubmit();
        }
      }
      pendingForm = null;
      pendingSubmitter = null;
    });
  }
  // Show progress only after validation/confirmation. Payment status always comes from Django.
  document.querySelectorAll('form[data-loading]').forEach(form => {
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      const button = event.submitter;
      if (button) {
        button.setAttribute('aria-busy', 'true');
        button.textContent = form.dataset.loading || 'Please wait…';
      }
    });
  });
})();
