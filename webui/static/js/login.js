/** Provide submit feedback without changing native form validation or credentials. */
(function () {
  const form = document.getElementById('loginForm');
  const button = document.getElementById('loginButton');
  const progress = document.getElementById('loginProgress');
  let submitting = false;

  form.addEventListener('submit', event => {
    if (submitting) {
      event.preventDefault();
      return;
    }
    submitting = true;
    button.disabled = true;
    button.textContent = 'Signing in…';
    form.setAttribute('aria-busy', 'true');
    progress.textContent = 'Verifying credentials. Please wait…';
  });

  window.addEventListener('pageshow', () => {
    submitting = false;
    button.disabled = false;
    button.textContent = 'Login';
    form.removeAttribute('aria-busy');
    progress.textContent = '';
  });
})();
