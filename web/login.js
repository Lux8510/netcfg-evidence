const form = document.querySelector('#login-form');
const errorNode = document.querySelector('#login-error');

form.onsubmit = async (event) => {
  event.preventDefault();
  errorNode.textContent = '';
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username: document.querySelector('#username').value.trim(),
      password: document.querySelector('#password').value,
    }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    errorNode.textContent = payload?.error?.message || '登录失败';
    return;
  }
  location.href = '/';
};
