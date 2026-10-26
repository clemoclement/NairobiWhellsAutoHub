// Small helpers shared by the customer page and the admin page.

export const formatPrice = (n) => 'KES ' + Number(n).toLocaleString('en-KE');

// Stops text from the database being treated as HTML.
export const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

// The server stores times as Unix seconds. This gives the hours left, rounded up.
export const hoursLeft = (expiresAt, now = Date.now()) =>
  Math.max(0, Math.ceil((expiresAt * 1000 - now) / 3600000));

// Wait until the user stops typing before running a function.
export function debounce(fn, ms = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
