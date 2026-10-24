"""The rules of the business. Routes in app.py stay thin and call these functions."""
import re
import sqlite3
import time

from db import transaction
from mpesa import MpesaError

HOLD_SECONDS = 24 * 60 * 60   # an unpaid reservation lasts 24 hours
DEPOSIT_RATE = 0.10           # booking deposit is 10% of the price
PAYMENT_GRACE = 10 * 60       # don't expire an order while a payment prompt is fresh
DUPLICATE_PROMPT_WINDOW = 90  # block a second prompt for the same order within 90 seconds
ACTIVE = ("pending", "confirmed", "paid")
ACTIVE_SQL = "('pending', 'confirmed', 'paid')"


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_phone(raw):
    """Turn 0712345678 or +254712345678 into 254712345678 (the format M-Pesa needs)."""
    digits = re.sub(r"[\s+-]", "", str(raw or ""))
    if re.fullmatch(r"0[71]\d{8}", digits):
        digits = "254" + digits[1:]
    return digits if re.fullmatch(r"254[71]\d{8}", digits) else None


def has_active_order(database, car_id):
    return database.execute(
        f"SELECT 1 FROM orders WHERE car_id = ? AND status IN {ACTIVE_SQL}", (car_id,)
    ).fetchone() is not None


# ------------------------------------------------------------------ reading
def order_rows(database, user_id=None, order_id=None, admin=False):
    """Orders as plain dictionaries. Customers never see other people's details."""
    where, params = [], []
    if user_id is not None:
        where.append("o.user_id = ?")
        params.append(user_id)
    if order_id is not None:
        where.append("o.id = ?")
        params.append(order_id)
    rows = database.execute(f"""
        SELECT o.id, o.status, o.deposit, o.created_at, o.expires_at,
               c.id AS car_id, c.year, c.make, c.model, c.price,
               u.name AS customer_name, u.phone AS customer_phone,
               EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id AND p.needs_refund = 1)
                   AS refund_needed,
               (SELECT p.mpesa_receipt FROM payments p
                 WHERE p.order_id = o.id AND p.status = 'success' AND p.needs_refund = 0
                 ORDER BY p.id DESC LIMIT 1) AS receipt
        FROM orders o
        JOIN cars c ON c.id = o.car_id
        JOIN users u ON u.id = o.user_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY o.id DESC""", params).fetchall()
    result = []
    for r in rows:
        item = {
            "id": r["id"], "status": r["status"], "deposit": r["deposit"],
            "created_at": r["created_at"], "expires_at": r["expires_at"],
            "receipt": r["receipt"] or "",
            "car": {"id": r["car_id"], "year": r["year"], "make": r["make"],
                    "model": r["model"], "price": r["price"]},
        }
        if admin:
            item.update(customer_name=r["customer_name"], customer_phone=r["customer_phone"],
                        refund_needed=bool(r["refund_needed"]))
        result.append(item)
    return result


# ----------------------------------------------------------------- ordering
def place_order(database, user_id, car_id, now=None):
    """Reserve a car for a customer. Two people can never both succeed.

    Inside the transaction the database is locked for writing, so the check
    "is the car available?" and the change "mark it reserved" happen together.
    """
    now = now or int(time.time())
    release_expired(database, now)
    try:
        with transaction(database):
            car = database.execute("SELECT id, price, status FROM cars WHERE id = ?",
                                   (car_id,)).fetchone()
            if car is None:
                raise ServiceError("Car not found.", 404)
            if car["status"] != "available":
                raise ServiceError("Sorry, this car is no longer available.", 409)
            database.execute("UPDATE cars SET status = 'reserved' WHERE id = ?", (car_id,))
            cursor = database.execute(
                """INSERT INTO orders (user_id, car_id, status, deposit, created_at, expires_at)
                   VALUES (?, ?, 'pending', ?, ?, ?)""",
                (user_id, car_id, round(car["price"] * DEPOSIT_RATE), now, now + HOLD_SECONDS))
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        # The unique index caught a double order that slipped past the check.
        raise ServiceError("Sorry, this car is no longer available.", 409)


def release_expired(database, now=None):
    """Cancel unpaid orders whose 24 hours are up and free their cars."""
    now = now or int(time.time())
    condition = """o.status IN ('pending', 'confirmed') AND o.expires_at < ?
                   AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id
                                   AND p.status = 'initiated' AND p.created_at > ?)"""
    args = (now, now - PAYMENT_GRACE)
    # Quick look first, so ordinary requests don't lock the database for nothing.
    if database.execute(f"SELECT 1 FROM orders o WHERE {condition}", args).fetchone() is None:
        return 0
    with transaction(database):
        rows = database.execute(f"SELECT o.id, o.car_id FROM orders o WHERE {condition}",
                                args).fetchall()
        for row in rows:
            database.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (row["id"],))
            database.execute("UPDATE cars SET status = 'available' "
                             "WHERE id = ? AND status = 'reserved'", (row["car_id"],))
    return len(rows)


def cancel_order(database, order_id, actor):
    with transaction(database):
        order = database.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        is_admin = actor["role"] == "admin"
        if order is None or (not is_admin and order["user_id"] != actor["id"]):
            raise ServiceError("Order not found.", 404)
        if order["status"] not in ACTIVE:
            raise ServiceError("This order is already closed.", 409)
        if order["status"] == "paid" and not is_admin:
            raise ServiceError("A paid order can only be cancelled by the dealership.", 403)
        database.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        database.execute("UPDATE cars SET status = 'available' WHERE id = ?", (order["car_id"],))
        # Money was taken, so someone must pay it back. Flag it for the admin.
        database.execute("UPDATE payments SET needs_refund = 1 "
                         "WHERE order_id = ? AND status = 'success'", (order_id,))


def admin_set_status(database, order_id, new_status, actor):
    if new_status == "cancelled":
        return cancel_order(database, order_id, actor)
    with transaction(database):
        order = database.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise ServiceError("Order not found.", 404)
        if new_status == "confirmed":
            if order["status"] != "pending":
                raise ServiceError("Only pending orders can be confirmed.", 409)
            database.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))
        elif new_status == "completed":
            if order["status"] != "paid":
                raise ServiceError("An order must be paid before it is completed.", 409)
            database.execute("UPDATE orders SET status = 'completed' WHERE id = ?", (order_id,))
            database.execute("UPDATE cars SET status = 'sold' WHERE id = ?", (order["car_id"],))
        else:
            raise ServiceError("Unknown status.", 400)


# ----------------------------------------------------------------- payments
def start_payment(database, mpesa, user, order_id, phone_raw, now=None):
    """Send the M-Pesa prompt to the customer's phone and record it."""
    now = now or int(time.time())
    order = database.execute("SELECT * FROM orders WHERE id = ? AND user_id = ?",
                             (order_id, user["id"])).fetchone()
    if order is None:
        raise ServiceError("Order not found.", 404)
    if order["status"] not in ("pending", "confirmed"):
        raise ServiceError("This order can't be paid right now.", 409)
    if order["expires_at"] < now:
        raise ServiceError("This reservation has expired.", 409)
    phone = normalize_phone(phone_raw or user["phone"])
    if phone is None:
        raise ServiceError("Enter a valid Kenyan phone number, e.g. 0712345678.", 400)
    recent = database.execute(
        "SELECT 1 FROM payments WHERE order_id = ? AND status = 'initiated' AND created_at > ?",
        (order_id, now - DUPLICATE_PROMPT_WINDOW)).fetchone()
    if recent:
        raise ServiceError("A payment prompt was just sent. Check your phone.", 409)

    try:
        result = mpesa.stk_push(phone, order["deposit"], order_id)
    except MpesaError as err:
        raise ServiceError(str(err), 502)

    database.execute(
        """INSERT INTO payments (order_id, amount, phone, checkout_request_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (order_id, order["deposit"], phone, result["checkout_request_id"], now))
    return result


def _same_amount(paid, expected):
    try:
        return int(float(paid)) == int(expected)
    except (TypeError, ValueError):
        return False


def process_callback(database, payload, now=None):
    """Handle the result M-Pesa sends back. Safe to call twice with the same message."""
    try:
        body = payload["Body"]["stkCallback"]
        checkout_id = body["CheckoutRequestID"]
        code = int(body["ResultCode"])
    except (KeyError, TypeError, ValueError):
        return None

    with transaction(database):
        payment = database.execute("SELECT * FROM payments WHERE checkout_request_id = ?",
                                   (checkout_id,)).fetchone()
        if payment is None or payment["status"] != "initiated":
            return None  # unknown, or already handled

        if code != 0:  # customer cancelled, wrong PIN, no money...
            database.execute("UPDATE payments SET status = 'failed', result_desc = ? WHERE id = ?",
                             (str(body.get("ResultDesc", ""))[:200], payment["id"]))
            return payment["id"]

        items = {i.get("Name"): i.get("Value")
                 for i in (body.get("CallbackMetadata") or {}).get("Item", [])}
        receipt = str(items.get("MpesaReceiptNumber", ""))
        order = database.execute("SELECT * FROM orders WHERE id = ?",
                                 (payment["order_id"],)).fetchone()

        if _same_amount(items.get("Amount"), payment["amount"]) \
                and order["status"] in ("pending", "confirmed"):
            database.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order["id"],))
            needs_refund = 0
        else:
            # Money arrived but we can't use it (wrong amount, order cancelled, paid twice).
            needs_refund = 1
        database.execute(
            "UPDATE payments SET status = 'success', mpesa_receipt = ?, result_desc = ?, "
            "needs_refund = ? WHERE id = ?",
            (receipt, str(body.get("ResultDesc", ""))[:200], needs_refund, payment["id"]))
        return payment["id"]


# ------------------------------------------------------------------ reports
def sales_report(database):
    """Only completed orders count as sales."""
    rows = database.execute("""
        SELECT cat.name AS category, COUNT(*) AS sold, SUM(c.price) AS revenue
        FROM orders o
        JOIN cars c ON c.id = o.car_id
        JOIN categories cat ON cat.id = c.category_id
        WHERE o.status = 'completed'
        GROUP BY cat.name ORDER BY sold DESC, revenue DESC""").fetchall()
    return {
        "cars_sold": sum(r["sold"] for r in rows),
        "revenue": sum(r["revenue"] for r in rows),
        "by_category": {r["category"]: r["sold"] for r in rows},
        "best_category": rows[0]["category"] if rows else None,
        "cars_available": database.execute(
            "SELECT COUNT(*) FROM cars WHERE status = 'available'").fetchone()[0],
        "deposits_collected": database.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments "
            "WHERE status = 'success' AND needs_refund = 0").fetchone()[0],
        "refunds_needed": database.execute(
            "SELECT COUNT(*) FROM payments WHERE needs_refund = 1").fetchone()[0],
    }
