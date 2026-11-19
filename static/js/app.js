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
  // A waiting member sees the payment button when the host finalizes or saves
  // UPI details. Checking status never launches an app or changes a payment.
  const waitingPayment = document.querySelector('[data-payment-status-url]');
  if (waitingPayment && typeof window.fetch === 'function') {
    let checking = false;
    let refreshing = false;
    const checkPaymentStatus = async () => {
      if (document.hidden || checking || refreshing) return;
      checking = true;
      try {
        const response = await fetch(waitingPayment.dataset.paymentStatusUrl, {
          credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}
        });
        if (!response.ok) return;
        const status = await response.json();
        if (typeof status.state === 'string' && status.state !== waitingPayment.dataset.paymentState) {
          refreshing = true;
          window.location.reload();
        }
      } catch (_) {
        // The visible Refresh payment status link remains the fallback.
      } finally {
        checking = false;
      }
    };
    window.setInterval(checkPaymentStatus, 15000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) checkPaymentStatus();
    });
    window.addEventListener('pageshow', event => {
      if (event.persisted) checkPaymentStatus();
    });
  }
  // Keep the host's demo status board in sync with member clicks. A poll is
  // read-only and must never interrupt a form being edited or submitted.
  const collectorBoard = document.querySelector('[data-collector-status-url]');
  if (collectorBoard && typeof window.fetch === 'function') {
    let checking = false;
    let refreshing = false;
    let editing = false;
    let submitting = false;
    collectorBoard.querySelectorAll('form').forEach(form => {
      form.addEventListener('input', () => { editing = true; });
      form.addEventListener('submit', event => {
        if (!event.defaultPrevented) submitting = true;
      });
    });
    const checkCollectorStatus = async () => {
      if (document.hidden || checking || refreshing || editing || submitting) return;
      if (document.activeElement?.matches('input, textarea, select, [contenteditable="true"]')) return;
      checking = true;
      try {
        const response = await fetch(collectorBoard.dataset.collectorStatusUrl, {
          credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}
        });
        if (!response.ok) return;
        const status = await response.json();
        if (typeof status.revision === 'string' && status.revision !== collectorBoard.dataset.collectorRevision && !editing && !submitting) {
          refreshing = true;
          window.location.reload();
        }
      } catch (_) {
        // Refresh status remains available when a network check fails.
      } finally {
        checking = false;
      }
    };
    window.setInterval(checkCollectorStatus, 8000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) checkCollectorStatus();
    });
    window.addEventListener('pageshow', event => {
      if (event.persisted) checkCollectorStatus();
    });
  }
})();
