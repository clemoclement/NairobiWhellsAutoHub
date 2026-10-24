import { SEED_CARS } from './data.js';

// Both the customer page and the admin page read and write here,
// so a reservation made on one page shows up on the other.
const KEY = 'autohub-v1';

export function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY));
    if (saved) return saved;
  } catch { /* ignore broken data */ }
  return { cars: structuredClone(SEED_CARS), orders: [], user: null };
}

export function saveState(state) {
  localStorage.setItem(KEY, JSON.stringify(state));
}
