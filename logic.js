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
