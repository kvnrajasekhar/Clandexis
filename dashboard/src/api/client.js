/**
 * Base API client with CSRF token support and automatic session handling.
 */

let activeCsrfToken = '';
let onUnauthorizedCallback = null;

export function setCsrfToken(token) {
  activeCsrfToken = token || '';
}

export function setOnUnauthorized(callback) {
  onUnauthorizedCallback = callback;
}

export async function apiFetch(url, options = {}) {
  const headers = {
    ...options.headers,
  };

  if (activeCsrfToken) {
    headers['X-CSRF-Token'] = activeCsrfToken;
  }

  const response = await fetch(url, {
    ...options,
    credentials: 'same-origin',
    headers,
  });

  if (response.status === 401 && onUnauthorizedCallback) {
    onUnauthorizedCallback();
  }

  return response;
}
