import { loadState, saveState } from './storage.js';
import {
  CATEGORIES, addCar, deleteCar, setCarStatus, setOrderStatus,
  salesReport, formatPrice, escapeHtml,
} from './logic.js';

// DEMO ONLY: anyone can read this PIN in the browser.
// Real login must be checked by the backend.
const ADMIN_PIN = '1234';

const state = loadState();
const save = () => saveState(state);
const $ = (selector) => document.querySelector(selector);
const carName = (car) => escapeHtml(`${car.year} ${car.make} ${car.model}`);

function showMessage(text) {
  const box = $('#message');
  box.textContent = text;
  box.classList.add('show');
  setTimeout(() => { box.textContent = ''; box.classList.remove('show'); }, 4000);
}

// ---- Drawing the page ----
function renderReport() {
  const r = salesReport(state.cars, state.orders);
  const available = state.cars.filter((c) => c.status === 'available').length;
  const stat = (label, value) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`;
  $('#report').innerHTML =
    stat('Cars sold', r.carsSold) +
    stat('Revenue', formatPrice(r.revenue)) +
    stat('Best category', escapeHtml(r.bestCategory ?? 'None yet')) +
    stat('Cars available', available);
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
  const btn = (action, label) => `<button data-order="${order.id}" data-action="${action}">${label}</button>`;
  if (order.status === 'pending') return btn('confirmed', 'Confirm') + btn('cancelled', 'Cancel');
  if (order.status === 'confirmed') return btn('cancelled', 'Cancel');
  if (order.status === 'paid') return btn('completed', 'Mark completed') + btn('cancelled', 'Cancel');
  return '';
}

function renderOrders() {
  $('#order-list').innerHTML = state.orders.map((order) => {
    const car = state.cars.find((c) => c.id === order.carId);
    return `
      <div class="order">
        <div>
          <strong>${car ? carName(car) : 'Deleted car'}</strong>
          <p class="muted">${escapeHtml(order.customer.name)} · ${escapeHtml(order.customer.phone)}
            · deposit ${formatPrice(order.deposit)}</p>
        </div>
        <span class="badge ${order.status}">${order.status}</span>
        <div>${orderButtons(order)}</div>
      </div>`;
  }).join('') || '<p class="muted">No orders yet.</p>';
}

function renderAll() {
  const loggedIn = sessionStorage.getItem('admin') === 'yes';
  $('#login').hidden = loggedIn;
  $('#dashboard').hidden = !loggedIn;
  if (!loggedIn) return;
  renderReport();
  renderCars();
  renderOrders();
}

// ---- Reacting to the admin ----
$('#login-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if ($('#pin').value === ADMIN_PIN) {
    sessionStorage.setItem('admin', 'yes');
    $('#pin').value = '';
    renderAll();
  } else {
    showMessage('Wrong PIN.');
  }
});

$('#logout').addEventListener('click', () => {
  sessionStorage.removeItem('admin');
  renderAll();
});

$('#car-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const result = addCar(state.cars, {
    make: $('#make').value,
    model: $('#model').value,
    year: Number($('#year').value),
    category: $('#category').value,
    condition: $('#condition').value,
    price: Number($('#price').value),
    specs: $('#specs').value,
  });
  if (!result.ok) return showMessage(result.error);
  save();
  event.target.reset();
  showMessage('Car added.');
  renderAll();
});

$('#car-list').addEventListener('change', (event) => {
  const id = Number(event.target.dataset.status);
  if (!id) return;
  const result = setCarStatus(state.cars, state.orders, id, event.target.value);
  if (!result.ok) showMessage(result.error);
  save();
  renderAll();
});

$('#car-list').addEventListener('click', (event) => {
  const id = Number(event.target.dataset.delete);
  if (!id || !confirm('Delete this car?')) return;
  const result = deleteCar(state.cars, state.orders, id);
  showMessage(result.ok ? 'Car deleted.' : result.error);
  save();
  renderAll();
});

$('#order-list').addEventListener('click', (event) => {
  const id = Number(event.target.dataset.order);
  if (!id) return;
  const result = setOrderStatus(state.cars, state.orders, id, event.target.dataset.action);
  if (!result.ok) showMessage(result.error);
  save();
  renderAll();
});

// If you changed something on the customer page, refresh the data when you return here.
// window.addEventListener('focus', () => {
//   Object.assign(state, loadState());
//   renderAll();
// });


function refreshFromStorage() {
  Object.assign(state, loadState());
  renderAll();
}
window.addEventListener('focus', refreshFromStorage);
window.addEventListener('storage', refreshFromStorage);   // fires when another tab saves data
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshFromStorage();
});

// ---- Start ----
$('#category').innerHTML = CATEGORIES.map((c) => `<option>${c}</option>`).join('');
renderAll();
