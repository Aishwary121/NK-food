(() => {
  'use strict';
  const cart = document.querySelector('[data-cart]');
  if (!cart || typeof window.fetch !== 'function') return;
  const feedback = cart.querySelector('[data-cart-feedback]');
  const total = cart.querySelector('[data-cart-total]');
  const count = cart.querySelector('[data-cart-count]');
  const submit = cart.querySelector('[data-cart-submit]');
  let pending = false;
  let needsRefresh = false;

  const setControls = () => {
    cart.dataset.cartBusy = String(pending);
    cart.querySelectorAll('[data-cart-stepper]').forEach(form => {
      form.querySelectorAll('[data-cart-step]').forEach(button => {
        const blocked = needsRefresh || (button.value === '1' && form.dataset.canIncrease !== 'true');
        button.disabled = blocked;
        button.setAttribute('aria-disabled', String(pending || blocked));
      });
    });
    submit?.querySelectorAll('button').forEach(button => { button.disabled = pending || needsRefresh; });
  };
  submit?.addEventListener('submit', event => {
    if (pending || needsRefresh) {
      event.preventDefault();
      feedback.textContent = needsRefresh ? 'Refresh your cart before adding it to the group order.' : 'Please wait for the quantity change to finish.';
    }
  });

  cart.querySelectorAll('[data-cart-stepper]').forEach(form => {
    form.addEventListener('submit', async event => {
      const button = event.submitter;
      if (!button?.matches('[data-cart-step]')) return;
      event.preventDefault();
      if (pending || needsRefresh || button.disabled) return;
      const row = form.closest('[data-cart-row]');
      const error = row.querySelector('[data-cart-error]');
      const refresh = row.querySelector('[data-cart-refresh]');
      error.hidden = true;
      error.textContent = '';
      refresh.hidden = true;
      feedback.textContent = '';
      pending = true;
      form.setAttribute('aria-busy', 'true');
      setControls();
      const body = new FormData(form);
      body.set('step', button.value);
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(form.action, {
          method: 'POST', body, credentials: 'same-origin', cache: 'no-store',
          headers: {Accept: 'application/json'}, signal: controller.signal
        });
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Refresh required');
        const result = await response.json();
        if (!response.ok) {
          if (response.status !== 400 || typeof result.error !== 'string') throw new Error('Refresh required');
          error.textContent = result.error;
          error.hidden = false;
          return;
        }
        // These values are returned by Django; never calculate a payable total here.
        if (!Number.isInteger(result.quantity) || result.quantity < 0 || result.quantity > 999 ||
            !Number.isInteger(result.item_count) || result.item_count < 0 ||
            typeof result.removed !== 'boolean' || typeof result.can_increase !== 'boolean' ||
            !/^\d+\.\d{2}$/.test(result.subtotal) || !/^\d+\.\d{2}$/.test(result.total)) {
          throw new Error('Refresh required');
        }
        total.textContent = `₹${result.total}`;
        count.textContent = `${result.item_count} ${result.item_count === 1 ? 'dish' : 'dishes'} selected`;
        if (result.removed) {
          const hadFocus = row.contains(document.activeElement);
          const followingRow = row.nextElementSibling || row.previousElementSibling;
          row.remove();
          feedback.textContent = `${row.dataset.dishName} removed. Cart total ₹${result.total}.`;
          if (result.item_count === 0) {
            needsRefresh = true;
            window.location.reload();
            return;
          }
          // Move keyboard focus only when it was inside the removed row.
          const focusTarget = followingRow?.querySelector('[data-cart-step="-1"]') || submit?.querySelector('button');
          if (hadFocus && focusTarget) window.setTimeout(() => focusTarget.focus(), 0);
        } else {
          row.querySelector('[data-cart-quantity]').textContent = String(result.quantity);
          row.querySelector('[data-cart-subtotal]').textContent = `₹${result.subtotal}`;
          form.dataset.canIncrease = String(result.can_increase);
          const minus = form.querySelector('[data-cart-step="-1"]');
          minus.setAttribute('aria-label', result.quantity === 1 ? `Remove ${row.dataset.dishName} from cart` : `Decrease quantity of ${row.dataset.dishName}`);
          minus.title = result.quantity === 1 ? 'Remove from cart' : 'Decrease quantity';
          feedback.textContent = `${row.dataset.dishName}: quantity ${result.quantity}. Cart total ₹${result.total}.`;
        }
      } catch (_) {
        // A dropped response may follow a saved change. Require a refresh before
        // another mutation so the member cannot submit uncertain displayed totals.
        needsRefresh = true;
        error.textContent = 'Could not confirm this change. Refresh the cart before trying again.';
        error.hidden = false;
        refresh.hidden = false;
      } finally {
        window.clearTimeout(timeout);
        pending = false;
        form.removeAttribute('aria-busy');
        setControls();
      }
    });
  });
})();
