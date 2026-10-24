import { api } from './api.js';
import { formatPrice, escapeHtml } from './util.js';

const state = { user: null, cars: [], orders: [], report: null };
const $ = (selector) => document.querySelector(selector);
const carName = (car) => escapeHtml(`${car.year} ${car.make} ${car.model}`);

function showMessage(text) {
  const box = $('#message');
  box.textContent = text;
  box.classList.add('show');
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(() => { box.textContent = ''; box.classList.remove('show'); }, 6000);
}

// ---- Getting data from the server ----
async function loadAll() {
  if (!state.user) return;
  try {
    [state.report, state.cars, state.orders] = await Promise.all([
      api('/admin/report'), api('/cars'), api('/admin/orders'),
    ]);
    renderAll();
  } catch (error) {
    showMessage(error.message);
    if (error.status === 401 || error.status === 403) { state.user = null; renderAll(); }
  }
}

// ---- Drawing the page ----
function renderReport() {
  const r = state.report;
  const stat = (label, value) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`;
  $('#report').innerHTML =
    stat('Cars sold', r.cars_sold) +
    stat('Revenue', formatPrice(r.revenue)) +
    stat('Best category', escapeHtml(r.best_category ?? 'None yet')) +
    stat('Cars available', r.cars_available) +
    stat('Deposits collected', formatPrice(r.deposits_collected)) +
    (r.refunds_needed ? stat('Refunds needed', r.refunds_needed) : '');
}

function renderCars() {
  $('#car-list').innerHTML = state.cars.map((car) => `
    <div class="order">
      <div>
        <strong>${carName(car)}</strong>
        <p class="muted">${escapeHtml(car.category)} · ${formatPrice(car.price)}</p>
      </div>
      <select data-status="${car.id}">
        ${['available', 'reserved', 'sold'].map((s) =>
          `<option ${s === car.status ? 'selected' : ''}>${s}</option>`).join('')}
      </select>
      <button data-delete="${car.id}">Delete</button>
    </div>`).join('') || '<p class="muted">No cars yet.</p>';
}

function orderButtons(order) {
  const button = (action, label) =>
    `<button data-order="${order.id}" data-action="${action}">${label}</button>`;
  if (order.status === 'pending') return button('confirmed', 'Confirm') + button('cancelled', 'Cancel');
  if (order.status === 'confirmed') return button('cancelled', 'Cancel');
  if (order.status === 'paid') return button('completed', 'Mark completed') + button('cancelled', 'Cancel');
  return '';
}

function renderOrders() {
  $('#order-list').innerHTML = state.orders.map((order) => `
    <div class="order">
      <div>
        <strong>${carName(order.car)}</strong>
        <p class="muted">${escapeHtml(order.customer_name)} · ${escapeHtml(order.customer_phone)}
          · deposit ${formatPrice(order.deposit)}
          ${order.receipt ? `· receipt ${escapeHtml(order.receipt)}` : ''}</p>
      </div>
      <span>
        <span class="badge ${order.status}">${order.status}</span>
        ${order.refund_needed ? '<span class="badge refund">refund needed</span>' : ''}
      </span>
      <div>${orderButtons(order)}</div>
    </div>`).join('') || '<p class="muted">No orders yet.</p>';
}

function renderAll() {
  const loggedIn = Boolean(state.user);
  $('#login').hidden = loggedIn;
  $('#dashboard').hidden = !loggedIn;
  if (!loggedIn) return;
  $('#who').textContent = `Logged in as ${state.user.name}.`;
  renderReport();
  renderOrders();
  renderCars();
}

// ---- Logging in ----
async function login(event) {
  event.preventDefault();
  try {
    const user = await api('/login', {
      method: 'POST', body: { email: $('#email').value, password: $('#password').value },
    });
    if (user.role !== 'admin') {
      // Stay logged in as a customer, but show no admin tools. The server refuses
      // every admin request from a customer anyway.
      return showMessage('That account is not an admin account.');
    }
    state.user = user;
    $('#login-form').reset();
    await loadAll();
  } catch (error) {
    showMessage(error.message);
  }
}
$('#login-form').addEventListener('submit', login);

$('#logout').addEventListener('click', async () => {
  await api('/logout', { method: 'POST' });
  state.user = null;
  renderAll();
});

// ---- Managing cars ----
$('#car-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/cars', {
      method: 'POST',
      body: {
        make: $('#make').value, model: $('#model').value,
        year: Number($('#year').value), category: $('#category').value,
        condition: $('#condition').value, price: Number($('#price').value),
        specs: $('#specs').value,
      },
    });
    event.target.reset();
    showMessage('Car added.');
  } catch (error) {
    showMessage(error.message);
  }
  await loadAll();
});

$('#car-list').addEventListener('change', async (event) => {
  const id = event.target.dataset.status;
  if (!id) return;
  try {
    await api(`/cars/${id}`, { method: 'PUT', body: { status: event.target.value } });
  } catch (error) {
    showMessage(error.message);
  }
  await loadAll();
});

$('#car-list').addEventListener('click', async (event) => {
  const id = event.target.dataset.delete;
  if (!id || !confirm('Delete this car?')) return;
  try {
    await api(`/cars/${id}`, { method: 'DELETE' });
    showMessage('Car deleted.');
  } catch (error) {
    showMessage(error.message);
  }
  await loadAll();
});

// ---- Managing orders ----
$('#order-list').addEventListener('click', async (event) => {
  const { order, action } = event.target.dataset;
  if (!order) return;
  try {
    await api(`/admin/orders/${order}/status`, { method: 'POST', body: { status: action } });
  } catch (error) {
    showMessage(error.message);
  }
  await loadAll();
});

// ---- Start ----
async function start() {
  try {
    const categories = await api('/categories');
    $('#category').innerHTML = categories.map((c) => `<option>${escapeHtml(c)}</option>`).join('');
    const me = await api('/me').catch(() => null); // not logged in is fine
    if (me && me.role === 'admin') state.user = me;
  } catch (error) {
    showMessage(error.message);
  }
  renderAll();
  await loadAll();
  setInterval(loadAll, 15000);               // new orders appear without refreshing
  window.addEventListener('focus', loadAll); // and when you switch back to this tab
}
start();
