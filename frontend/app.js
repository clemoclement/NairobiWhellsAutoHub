import { SEED_CARS } from './data.js';
import { filterCars, placeOrder, payOrder, expireOrders, formatPrice } from './logic.js';

// ---- Saved data (stays in your browser until you clear it) ----
const KEY = 'autohub-v2';

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY));
    if (saved) return saved;
  } catch { /* ignore broken data */ }
  return { cars: structuredClone(SEED_CARS), orders: [], user: null };
}

const state = load();
const save = () => localStorage.setItem(KEY, JSON.stringify(state));
const $ = (selector) => document.querySelector(selector);

function showMessage(text) {
  const box = $('#message');
  box.textContent = text;
  box.classList.add('show');
  setTimeout(() => { box.textContent = ''; box.classList.remove('show'); }, 4000);
}

// ---- Drawing the page ----
const carName = (car) => `${car.year} ${car.make} ${car.model}`;

function carCard(car) {
  const available = car.status === 'available';
  return `
      <article class="card">
  <div class="car-img" style="background:${car.color}">
    <img src="${car.image}" alt="${car.year} ${car.make} ${car.model}">
  </div>
  <div class="body">
              <span class="badge ${car.status}">${car.status}</span>
        <h3>${carName(car)}</h3>
        <p class="muted">${car.category} · ${car.condition}</p>
        <p class="muted">${car.specs}</p>
        <p class="price">${formatPrice(car.price)}</p>
        <button data-reserve="${car.id}" ${available ? '' : 'disabled'}>
          ${available ? 'Reserve this car' : 'Not available'}
        </button>
      </div>
    </article>`;
}



// function carCard(car) {
//   const available = car.status === 'available';
//   return `
//     <article class="card">
//       <div class="car-img" style="background:${car.color}">
//         <img src="${car.image}" alt="${car.year} ${car.make} ${car.model}">
//       </div>
//       <div class="body">
//         <span class="badge ${car.status}">...</span>
//         <h3>${carName(car)}</h3>
//         ...
//         <button ...>...</button>
//       </div>
//     </article>`;
// }



function renderCars() {
  const cars = filterCars(state.cars, {
    search: $('#search').value,
    category: $('#category').value,
    maxPrice: Number($('#max-price').value) || Infinity,
  });
  $('#cars').innerHTML = cars.length
    ? cars.map(carCard).join('')
    : '<p>No cars match your search.</p>';
}

function renderOrders() {
  const box = $('#orders');
  if (!state.user) {
    box.innerHTML = '<p class="muted">Sign in to see your orders.</p>';
    return;
  }
  const mine = state.orders.filter((o) => o.customer.phone === state.user.phone);
  box.innerHTML = mine.length
    ? mine.map((order) => {
        const car = state.cars.find((c) => c.id === order.carId);
        const hours = Math.max(0, Math.round((order.expiresAt - Date.now()) / 3600000));
        return `
          <div class="order">
            <div>
              <strong>${carName(car)}</strong>
              <p class="muted">Deposit ${formatPrice(order.deposit)}
                ${order.status === 'pending' ? `· pay within ${hours}h` : ''}</p>
            </div>
            <span class="badge ${order.status}">${order.status}</span>
            ${order.status === 'pending'
              ? `<button data-pay="${order.id}">Pay deposit (demo M-Pesa)</button>` : ''}
          </div>`;
      }).join('')
    : '<p class="muted">You have no orders yet.</p>';
}

function renderAccount() {
  const info = $('#user-info');
  $('#signin-form').hidden = Boolean(state.user);
  info.hidden = !state.user;
  if (state.user) {
    info.textContent = `Hello, ${state.user.name} · `;
    const out = document.createElement('button');
    out.textContent = 'Sign out';
    out.addEventListener('click', () => { state.user = null; save(); renderAll(); });
    info.append(out);
  }
}

function renderAll() {
  renderAccount();
  renderCars();
  renderOrders();
}

// ---- Reacting to the user ----
$('#signin-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const name = $('#name').value.trim();
  const phone = $('#phone').value.trim();
  if (!/^(254|0)[71]\d{8}$/.test(phone)) {
    showMessage('Enter a valid Kenyan phone number, e.g. 0712345678.');
    return;
  }
  state.user = { name, phone };
  save();
  renderAll();
});

for (const id of ['#search', '#category', '#max-price']) {
  $(id).addEventListener('input', renderCars);
}

$('#cars').addEventListener('click', (event) => {
  const id = Number(event.target.dataset.reserve);
  if (!id) return;
  if (!state.user) return showMessage('Please sign in first.');
  const result = placeOrder(state.cars, state.orders, id, state.user);
  showMessage(result.ok ? 'Car reserved! Pay the deposit within 24 hours.' : result.error);
  save();
  renderAll();
});

$('#orders').addEventListener('click', (event) => {
  const id = Number(event.target.dataset.pay);
  if (!id) return;
  // Demo only: a real version asks the backend to send an M-Pesa STK Push.
  if (confirm('Demo: pretend the M-Pesa prompt was paid?')) {
    payOrder(state.orders, id);
    showMessage('Payment received. Thank you!');
    save();
    renderAll();
  }
});

// ---- Start ----
const categories = [...new Set(state.cars.map((c) => c.category))];
$('#category').innerHTML =
  '<option value="all">All categories</option>' +
  categories.map((c) => `<option>${c}</option>`).join('');

function releaseExpired() {
  if (expireOrders(state.cars, state.orders) > 0) { save(); renderAll(); }
}
releaseExpired();
setInterval(releaseExpired, 60 * 1000);
renderAll();
