import { apiFetch, setCsrfToken } from './client';

export async function fetchSession() {
  const res = await fetch('/api/v1/auth/session', { credentials: 'same-origin' });
  if (res.ok) {
    const data = await res.json();
    if (data.csrf_token) setCsrfToken(data.csrf_token);
    return data;
  }
  return null;
}

export async function fetchAuthConfig() {
  const res = await fetch('/api/v1/auth/config', { credentials: 'same-origin' });
  if (res.ok) return await res.json();
  return null;
}

export async function loginWithGoogle(credential) {
  const res = await fetch('/api/v1/auth/google', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ credential }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Google sign-in failed');
  }
  if (data.csrf_token) setCsrfToken(data.csrf_token);
  return data;
}

export async function loginWithDemo(username, password) {
  const res = await fetch('/api/v1/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: username || 'demo', password: password || '' }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Sign-in failed. Please verify credentials.');
  }
  if (data.csrf_token) setCsrfToken(data.csrf_token);
  return data;
}

export async function onboardUser({ name, phone_number, business_name }) {
  const res = await apiFetch('/api/v1/auth/onboard', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, phone_number, business_name }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to complete profile onboarding');
  }
  if (data.csrf_token) setCsrfToken(data.csrf_token);
  return data;
}

export async function logoutUser() {
  try {
    await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' });
  } catch (_) {}
  setCsrfToken('');
}
