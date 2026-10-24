// Business rules live here, with no HTML. That makes them easy to test.

export const HOLD_HOURS = 24;
export const DEPOSIT_RATE = 0.1; // 10% booking deposit

export const formatPrice = (n) => 'KES ' + n.toLocaleString('en-KE');

// Returns the cars matching the search, category and max price.
export function filterCars(cars, { search = '', category = 'all', maxPrice = Infinity } = {}) {
  const q = search.trim().toLowerCase();
  return cars.filter((car) => {
    const text = `${car.make} ${car.model} ${car.year}`.toLowerCase();
    return (
      text.includes(q) &&
      (category === 'all' || car.category === category) &&
      car.price <= maxPrice
    );
  });
}

// Reserve a car. A car that is not available can never be ordered again.
export function placeOrder(cars, orders, carId, customer, now = Date.now()) {
  const car = cars.find((c) => c.id === carId);
  if (!car) return { ok: false, error: 'Car not found.' };
  if (car.status !== 'available') {
    return { ok: false, error: 'Sorry, this car is no longer available.' };
  }
  car.status = 'reserved';
  const order = {
    id: orders.length + 1,
    carId,
    customer,
    status: 'pending',
    deposit: Math.round(car.price * DEPOSIT_RATE),
    createdAt: now,
    expiresAt: now + HOLD_HOURS * 60 * 60 * 1000,
  };
  orders.push(order);
  return { ok: true, order };
}

// Mark an order as paid (the car stays reserved).
export function payOrder(orders, orderId) {
  const order = orders.find((o) => o.id === orderId);
  if (!order || order.status !== 'pending') return false;
  order.status = 'paid';
  return true;
}

// Cancel unpaid orders whose time is up, and free their cars.
export function expireOrders(cars, orders, now = Date.now()) {
  let released = 0;
  for (const order of orders) {
    if (order.status === 'pending' && order.expiresAt < now) {
      order.status = 'cancelled';
      cars.find((c) => c.id === order.carId).status = 'available';
      released++;
    }
  }
  return released;
}

// ---------------------------------------------------------------
// Admin rules
// ---------------------------------------------------------------

export const CATEGORIES = ['Supercars', 'SUVs', 'Sedans', 'Small Personal Cars'];
const EMOJI = { Supercars: '🏎️', SUVs: '🚙', Sedans: '🚘', 'Small Personal Cars': '🚗' };
const ACTIVE = ['pending', 'confirmed', 'paid'];

// Stops text typed by an admin from being treated as HTML.
export const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

export function addCar(cars, data) {
  const make = String(data.make ?? '').trim();
  const model = String(data.model ?? '').trim();
  const maxYear = new Date().getFullYear() + 1;
  if (!make || !model) return { ok: false, error: 'Make and model are required.' };
  if (!(data.year >= 1990 && data.year <= maxYear)) return { ok: false, error: `Year must be between 1990 and ${maxYear}.` };
  if (!(data.price > 0)) return { ok: false, error: 'Price must be more than zero.' };
  if (!CATEGORIES.includes(data.category)) return { ok: false, error: 'Choose a category.' };

  const car = {
    id: Math.max(0, ...cars.map((c) => c.id)) + 1,
    make, model,
    year: data.year,
    category: data.category,
    condition: data.condition ?? 'Used',
    price: data.price,
    specs: String(data.specs ?? '').trim(),
    status: 'available',
    emoji: EMOJI[data.category],
    color: '#e8eef5',
  };
  cars.push(car);
  return { ok: true, car };
}

const hasActiveOrder = (orders, carId) =>
  orders.some((o) => o.carId === carId && ACTIVE.includes(o.status));

// A car with an open order cannot be deleted.
export function deleteCar(cars, orders, carId) {
  if (hasActiveOrder(orders, carId)) return { ok: false, error: 'This car has an open order. Cancel the order first.' };
  const index = cars.findIndex((c) => c.id === carId);
  if (index === -1) return { ok: false, error: 'Car not found.' };
  cars.splice(index, 1);
  return { ok: true };
}

// Manual status change. Cars with an open order are controlled by that order.
export function setCarStatus(cars, orders, carId, status) {
  const car = cars.find((c) => c.id === carId);
  if (!car) return { ok: false, error: 'Car not found.' };
  if (hasActiveOrder(orders, carId)) return { ok: false, error: 'This car has an open order. Update the order instead.' };
  car.status = status;
  return { ok: true };
}

// Moves an order forward and keeps the car status in step with it.
export function setOrderStatus(cars, orders, orderId, newStatus) {
  const order = orders.find((o) => o.id === orderId);
  if (!order) return { ok: false, error: 'Order not found.' };
  const car = cars.find((c) => c.id === order.carId);

  if (newStatus === 'confirmed') {
    if (order.status !== 'pending') return { ok: false, error: 'Only pending orders can be confirmed.' };
    order.status = 'confirmed';
  } else if (newStatus === 'completed') {
    if (order.status !== 'paid') return { ok: false, error: 'An order must be paid before it is completed.' };
    order.status = 'completed';
    if (car) car.status = 'sold';
  } else if (newStatus === 'cancelled') {
    if (!ACTIVE.includes(order.status)) return { ok: false, error: 'This order is already closed.' };
    order.status = 'cancelled';
    if (car) car.status = 'available';
  } else {
    return { ok: false, error: 'Unknown status.' };
  }
  return { ok: true };
}

// Numbers for the sales report. Only completed orders count as sales.
export function salesReport(cars, orders) {
  const byCategory = {};
  let revenue = 0;
  const done = orders.filter((o) => o.status === 'completed');
  for (const order of done) {
    const car = cars.find((c) => c.id === order.carId);
    if (!car) continue;
    revenue += car.price;
    byCategory[car.category] = (byCategory[car.category] ?? 0) + 1;
  }
  const best = Object.entries(byCategory).sort((a, b) => b[1] - a[1])[0];
  return { carsSold: done.length, revenue, byCategory, bestCategory: best ? best[0] : null };
}
