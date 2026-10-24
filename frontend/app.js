import { api } from './api.js';
import { formatPrice, escapeHtml, hoursLeft, debounce } from './util.js';

// Everything the page knows. The real data lives in the database on the server.
const state = {
  user: null,
  cars: [],
  orders: [],
  mpesaMode: 'mock',
  checkoutIds: {}, // order id -> payment id, only used by the mock M-Pesa buttons
};

const $ = (selector) => document.querySelector(selector);
const carName = (car) => escapeHtml(`${car.year} ${car.make} ${car.model}`);
const isUnpaid = (order) => ['pending', 'confirmed'].includes(order.status);

function showMessage(text) {
  const box = $('#message');
  box.textContent = text;
  box.classList.add('show');
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(() => { box.textContent = ''; box.classList.remove('show'); }, 6000);
}

// ---- Getting data from the server ----
async function loadCars() {
  const params = new URLSearchParams();
  const filters = {
    search: $('#search').value.trim(),
    category: $('#category').value,
    year: $('#year').value,
    max_price: $('#max-price').value,
  };
  for (const [key, value] of Object.entries(filters)) {
    if (value && value !== 'all') params.set(key, value);
  }
  try {
    state.cars = await api('/cars?' + params);
  } catch (error) {
    showMessage(error.message);
  }
  renderCars();
}

async function loadOrders() {
  try {
    state.orders = state.user ? await api('/orders') : [];
  } catch (error) {
    showMessage(error.message);
  }
  renderOrders();
}

const reloadAll = () => Promise.all([loadCars(), loadOrders()]);

// ---- Drawing the page ----
function carCard(car) {
  const available = car.status === 'available';
  return `
    <article class="card">
      <div class="photo" style="background:#e8eef5">🚗</div>
      <div class="body">
        <span class="badge ${car.status}">${car.status}</span>
        <h3>${carName(car)}</h3>
        <p class="muted">${escapeHtml(car.category)} · ${escapeHtml(car.condition)}</p>
        <p class="muted">${escapeHtml(car.specs)}</p>
        <p class="price">${formatPrice(car.price)}</p>
        <button data-reserve="${car.id}" ${available ? '' : 'disabled'}>
          ${available ? 'Reserve this car' : 'Not available'}
        </button>
      </div>
    </article>`;
}

function renderCars() {
  $('#cars').innerHTML = state.cars.length
    ? state.cars.map(carCard).join('')
    : '<p>No cars match your search.</p>';
}

function orderCard(order) {
  const unpaid = isUnpaid(order);
  const mockPending = state.mpesaMode === 'mock' && state.checkoutIds[order.id] && unpaid;
  return `
    <div class="order">
      <div>
        <strong>${carName(order.car)}</strong>
        <p class="muted">Deposit ${formatPrice(order.deposit)}
          ${unpaid ? `· pay within ${hoursLeft(order.expires_at)}h` : ''}
          ${order.receipt ? `· M-Pesa receipt ${escapeHtml(order.receipt)}` : ''}</p>
      </div>
      <span class="badge ${order.status}">${order.status}</span>
      ${unpaid ? `<div>
          <button data-pay="${order.id}">Pay deposit with M-Pesa</button>
          <button data-cancel="${order.id}">Cancel</button>
        </div>` : ''}
      ${mockPending ? `<div class="mock-box">
          <strong>Test mode:</strong> there is no real phone, so pretend to be M-Pesa:
          <button data-sim="success" data-order="${order.id}">I entered my PIN</button>
          <button data-sim="cancelled" data-order="${order.id}">Cancel the prompt</button>
        </div>` : ''}
    </div>`;
}

function renderOrders() {
  const box = $('#orders');
  if (!state.user) {
    box.innerHTML = '<p class="muted">Log in to see your orders.</p>';
  } else {
    box.innerHTML = state.orders.length
      ? state.orders.map(orderCard).join('')
      : '<p class="muted">You have no orders yet.</p>';
  }
}

function renderAccount() {
  const loggedIn = Boolean(state.user);
  $('#login-form').hidden = loggedIn || !$('#register-form').hidden;
  if (loggedIn) $('#register-form').hidden = true;
  const info = $('#user-info');
  info.hidden = !loggedIn;
  if (loggedIn) {
    info.textContent = `Hello, ${state.user.name} `;
    const logout = document.createElement('button');
    logout.textContent = 'Log out';
    logout.addEventListener('click', async () => {
      await api('/logout', { method: 'POST' });
      state.user = null;
      state.checkoutIds = {};
      renderAccount();
      await reloadAll();
    });
    info.append(logout);
  }
}

// ---- Logging in and signing up ----
async function authenticate(path, body) {
  try {
    state.user = await api(path, { method: 'POST', body });
    $('#login-form').reset();
    $('#register-form').reset();
    $('#register-form').hidden = true;
    renderAccount();
    await reloadAll();
  } catch (error) {
    showMessage(error.message);
  }
}

$('#login-form').addEventListener('submit', (event) => {
  event.preventDefault();
  authenticate('/login', { email: $('#login-email').value, password: $('#login-password').value });
});

$('#register-form').addEventListener('submit', (event) => {
  event.preventDefault();
  authenticate('/register', {
    name: $('#reg-name').value,
    email: $('#reg-email').value,
    phone: $('#reg-phone').value,
    password: $('#reg-password').value,
  });
});

$('#show-register').addEventListener('click', () => {
  $('#login-form').hidden = true;
  $('#register-form').hidden = false;
});
$('#show-login').addEventListener('click', () => {
  $('#register-form').hidden = true;
  $('#login-form').hidden = false;
});

// ---- Searching ----
const search = debounce(loadCars);
$('#search').addEventListener('input', search);
$('#year').addEventListener('input', search);
$('#max-price').addEventListener('input', search);
$('#category').addEventListener('change', loadCars);

// ---- Reserving a car ----
$('#cars').addEventListener('click', async (event) => {
  const carId = Number(event.target.dataset.reserve);
  if (!carId) return;
  if (!state.user) return showMessage('Please log in or create an account first.');
  try {
    await api('/orders', { method: 'POST', body: { car_id: carId } });
    showMessage('Car reserved! Pay the deposit within 24 hours to keep it.');
  } catch (error) {
    showMessage(error.message); // e.g. someone else just reserved it
  }
  await reloadAll();
});

// ---- Paying, cancelling, and the test-mode buttons ----
function watchPayment(orderId) {
  // Ask the server every 3 seconds until M-Pesa has told it the result.
  let tries = 0;
  const timer = setInterval(async () => {
    tries++;
    await loadOrders();
    const order = state.orders.find((o) => o.id === orderId);
    if (!order || !isUnpaid(order) || tries >= 40) {
      clearInterval(timer);
      if (order && order.status === 'paid') {
        showMessage('Payment received. Thank you!');
        await loadCars();
      }
    }
  }, 3000);
}

$('#orders').addEventListener('click', async (event) => {
  const { pay, cancel, sim, order } = event.target.dataset;
  try {
    if (pay) {
      const suggestion = '0' + state.user.phone.slice(3);
      const phone = prompt('Phone number to send the M-Pesa prompt to:', suggestion);
      if (phone === null) return;
      const result = await api(`/orders/${pay}/pay`, { method: 'POST', body: { phone } });
      showMessage(result.message);
      if (result.checkout_request_id) state.checkoutIds[pay] = result.checkout_request_id;
      renderOrders();
      watchPayment(Number(pay));
    } else if (cancel) {
      if (!confirm('Cancel this reservation?')) return;
      await api(`/orders/${cancel}/cancel`, { method: 'POST' });
      showMessage('Reservation cancelled.');
      await reloadAll();
    } else if (sim) {
      await api('/dev/mpesa/simulate', {
        method: 'POST',
        body: { checkout_request_id: state.checkoutIds[order], outcome: sim },
      });
      delete state.checkoutIds[order];
      showMessage(sim === 'success' ? 'Payment received. Thank you!' : 'The payment was cancelled.');
      await reloadAll();
    }
  } catch (error) {
    showMessage(error.message);
    await reloadAll();
  }
});

// ---- Start ----
async function start() {
  try {
    const [config, categories] = await Promise.all([api('/config'), api('/categories')]);
    state.mpesaMode = config.mpesa_mode;
    $('#category').innerHTML = '<option value="all">All categories</option>' +
      categories.map((c) => `<option>${escapeHtml(c)}</option>`).join('');
    try { state.user = await api('/me'); } catch { state.user = null; } // not logged in is fine
  } catch (error) {
    showMessage(error.message);
  }
  renderAccount();
  await reloadAll();
  setInterval(reloadAll, 30000); // pick up other people's reservations
}
start();
