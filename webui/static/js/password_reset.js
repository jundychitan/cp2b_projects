/** Admin-only reset of the dec/amr password; never accepts an arbitrary password. */
let passwordResetBusy = false;

async function resetUserPassword() {
  if (!IS_ADMIN || passwordResetBusy) return;
  const target = document.getElementById('resetUserPasswordTarget');
  const username = target.value;
  if (!['dec', 'amr'].includes(username)) return;
  if (!confirm(`Reset the password for ${username} to 1234? `
      + 'Ask the user to change it after signing in.')) return;
  const button = document.getElementById('resetUserPasswordBtn');
  const alert = document.getElementById('resetUserPasswordAlert');
  passwordResetBusy = true;
  button.disabled = true;
  button.textContent = 'Resetting...';
  target.disabled = true;
  alert.classList.add('d-none');
  try {
    const res = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': button.dataset.csrfToken },
      body: JSON.stringify({ username }),
    });
    if (res.redirected || res.status === 401) {
      window.location.href = '/login';
      return;
    }
    const data = await res.json();
    alert.className = `alert alert-${res.ok ? 'success' : 'danger'}`;
    alert.textContent = res.ok ? data.message : (data.error || 'Password reset failed');
    alert.classList.remove('d-none');
  } catch (error) {
    alert.className = 'alert alert-danger';
    alert.textContent = 'Password reset result is unknown. Check connectivity before trying again.';
    alert.classList.remove('d-none');
  } finally {
    passwordResetBusy = false;
    button.disabled = false;
    button.textContent = 'Reset to 1234';
    target.disabled = false;
  }
}
