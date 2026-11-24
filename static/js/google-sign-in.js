// Firebase's official modular CDN SDK, pinned to one version for both modules.
// https://firebase.google.com/docs/web/alt-setup
// Firebase is transient here; the server establishes the lasting Django session.
const root = document.querySelector('[data-google-auth]');
const configElement = document.getElementById('firebase-web-config');

if (root && configElement) {
  const button = root.querySelector('[data-google-button]');
  const label = root.querySelector('[data-google-label]');
  const status = root.querySelector('[data-google-status]');
  let auth;
  let sdk;
  let signingIn = false;

  const showStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle('google-sign-in-error', isError);
  };

  const errorMessage = error => {
    const messages = {
      'auth/popup-blocked': 'Your browser blocked the Google window. Allow pop-ups for this site, then try again.',
      'auth/popup-closed-by-user': 'Google sign-in was cancelled. You can try again or use your username and password.',
      'auth/cancelled-popup-request': 'Please finish the Google sign-in window that is already open.',
      'auth/unauthorized-domain': 'Google sign-in is not enabled for this website address yet. Please use your username and password.',
      'auth/operation-not-allowed': 'Google sign-in is not available right now. Please use your username and password.',
      'auth/network-request-failed': 'Could not connect to Google. Check your connection and try again.',
      'auth/too-many-requests': 'Too many sign-in attempts. Please wait a moment before trying again.',
      'auth/account-exists-with-different-credential': 'This Google account uses another sign-in method. Please use your existing sign-in method.',
      'auth/user-disabled': 'This account cannot sign in. Please contact the site administrator.',
      'auth/web-storage-unsupported': 'Your browser is blocking the sign-in window. Try a regular browser tab, or use your username and password.'
    };
    if (error?.publicMessage) return error.publicMessage;
    if (error?.name === 'AbortError') return 'Sign-in took too long. Check your connection and try again.';
    return messages[error?.code] || 'Google sign-in could not be completed. Please try again or use your username and password.';
  };

  const publicError = message => Object.assign(new Error('Sign-in failed'), {publicMessage: message});

  button.addEventListener('click', async () => {
    if (!auth || !sdk || signingIn) return;
    signingIn = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    label.textContent = 'Signing in…';
    showStatus('Choose your Google account in the sign-in window.');
    let destination;
    try {
      // This must run directly from the user's click, after the SDK has loaded.
      const provider = new sdk.GoogleAuthProvider();
      provider.setCustomParameters({prompt: 'select_account'});
      const credential = await sdk.signInWithPopup(auth, provider);
      const idToken = await credential.user.getIdToken();
      const csrfToken = root.querySelector('[name=csrfmiddlewaretoken]')?.value;
      if (!csrfToken) throw publicError('This page has expired. Refresh it and try again.');
      showStatus('Completing your sign-in…');
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 25000);
      let response;
      try {
        response = await fetch(root.dataset.endpoint, {
          method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrfToken},
          body: JSON.stringify({id_token: idToken, next: root.dataset.next || ''}),
          signal: controller.signal
        });
      } finally {
        window.clearTimeout(timeout);
      }
      let result;
      try { result = await response.json(); } catch (_) {
        throw publicError(response.status === 403 ? 'This page has expired. Refresh it and try again.' : 'The site could not complete sign-in. Please try again.');
      }
      if (!response.ok) throw publicError(typeof result.error === 'string' ? result.error.slice(0, 500) : 'The site could not complete sign-in. Please try again.');
      if (typeof result.redirect !== 'string') throw publicError('The site could not complete sign-in. Please refresh and try again.');
      const redirect = new URL(result.redirect, window.location.origin);
      if (redirect.origin !== window.location.origin || !['http:', 'https:'].includes(redirect.protocol)) {
        throw publicError('The sign-in destination was not accepted. Please refresh and try again.');
      }
      destination = redirect.href;
      showStatus('Signed in. Opening your account…');
    } catch (error) {
      showStatus(errorMessage(error), true);
    } finally {
      // Never retain the Google/Firebase login or tokens in browser storage.
      await sdk.signOut(auth).catch(() => {});
      signingIn = false;
      if (!destination) {
        button.disabled = false;
        button.setAttribute('aria-busy', 'false');
        label.textContent = 'Continue with Google';
      }
    }
    if (destination) window.location.assign(destination);
  });

  (async () => {
    try {
      const config = JSON.parse(configElement.textContent);
      const [appSdk, authSdk] = await Promise.all([
        import('https://www.gstatic.com/firebasejs/13.4.0/firebase-app.js'),
        import('https://www.gstatic.com/firebasejs/13.4.0/firebase-auth.js')
      ]);
      sdk = authSdk;
      const app = appSdk.initializeApp(config, 'nk-food-google-sign-in');
      auth = sdk.initializeAuth(app, {
        persistence: sdk.inMemoryPersistence,
        popupRedirectResolver: sdk.browserPopupRedirectResolver
      });
      await auth.authStateReady();
      button.disabled = false;
      button.setAttribute('aria-busy', 'false');
      showStatus('');
    } catch (_) {
      button.disabled = true;
      button.setAttribute('aria-busy', 'false');
      showStatus('Google sign-in could not load. Refresh the page to retry, or use your username and password.', true);
    }
  })();
}
