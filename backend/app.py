"""AutoHub backend: login and cars.  Run with:  flask --app app run --debug"""
import hmac
import os
import re
import secrets
import sqlite3
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash

import db
import services
from db import get_db, transaction
from mpesa import MpesaClient
from services import ServiceError, normalize_phone

BASE = Path(__file__).parent


def create_app(test_config=None):
    # A "factory" function: tests can call it to get a fresh app with its own database.
    app = Flask(__name__, static_folder=str(BASE.parent / "frontend"), static_url_path="")
    app.config.from_mapping(
        # Set a long random SECRET_KEY in production. It signs the login cookie.
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=str(BASE / "autohub.db"),
        SESSION_COOKIE_HTTPONLY=True,   # JavaScript cannot read the cookie
        SESSION_COOKIE_SAMESITE="Lax",  # other sites cannot send it along with requests
        MPESA_MODE=os.environ.get("MPESA_MODE", "mock"),  # mock, sandbox or production
        MPESA_CONSUMER_KEY=os.environ.get("MPESA_CONSUMER_KEY", ""),
        MPESA_CONSUMER_SECRET=os.environ.get("MPESA_CONSUMER_SECRET", ""),
        MPESA_SHORTCODE=os.environ.get("MPESA_SHORTCODE", ""),
        MPESA_PASSKEY=os.environ.get("MPESA_PASSKEY", ""),
        MPESA_PUBLIC_URL=os.environ.get("MPESA_PUBLIC_URL", ""),  # e.g. your ngrok https address
        MPESA_CALLBACK_SECRET=os.environ.get("MPESA_CALLBACK_SECRET", "dev-callback-secret"),
    )
    if test_config:
        app.config.update(test_config)
    app.mpesa = MpesaClient(app.config)

    db.init_app(app)
    with app.app_context():
        db.init_db()

    # ------------------------------------------------------------ helpers
    def error(message, status):
        return jsonify({"error": message}), status

    def current_user():
        user_id = session.get("user_id")
        if user_id is None:
            return None
        return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if current_user() is None:
                return error("Please log in.", 401)
            return view(*args, **kwargs)
        return wrapper

    def admin_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return error("Please log in.", 401)
            if user["role"] != "admin":
                return error("Admins only.", 403)
            return view(*args, **kwargs)
        return wrapper

    def user_json(user):
        return {"id": user["id"], "name": user["name"], "email": user["email"],
                "phone": user["phone"], "role": user["role"]}

    CAR_SELECT = """SELECT cars.id, cars.make, cars.model, cars.year, cars.price,
                           cars.condition, cars.specs, cars.status, categories.name AS category
                    FROM cars JOIN categories ON categories.id = cars.category_id"""

    def get_car(car_id):
        return get_db().execute(CAR_SELECT + " WHERE cars.id = ?", (car_id,)).fetchone()

    def is_whole_number(value):
        # In Python, True counts as the number 1, so exclude it explicitly.
        return isinstance(value, int) and not isinstance(value, bool)

    def clean_car(data, partial=False):
        """Check car fields from the request. Returns (values, error_message)."""
        values = {}
        for field in ("make", "model"):
            if field in data or not partial:
                text = str(data.get(field, "")).strip()
                if not text:
                    return None, f"{field.capitalize()} is required."
                values[field] = text
        if "year" in data or not partial:
            year = data.get("year")
            if not is_whole_number(year) or not 1990 <= year <= date.today().year + 1:
                return None, f"Year must be a number between 1990 and {date.today().year + 1}."
            values["year"] = year
        if "price" in data or not partial:
            price = data.get("price")
            if not is_whole_number(price) or price <= 0:
                return None, "Price must be a whole number more than zero."
            values["price"] = price
        if "condition" in data or not partial:
            if data.get("condition", "Used") not in ("New", "Used"):
                return None, "Condition must be New or Used."
            values["condition"] = data.get("condition", "Used")
        if "specs" in data or not partial:
            values["specs"] = str(data.get("specs", "")).strip()
        if "status" in data:
            if data["status"] not in ("available", "reserved", "sold"):
                return None, "Status must be available, reserved or sold."
            values["status"] = data["status"]
        if "category" in data or not partial:
            row = get_db().execute("SELECT id FROM categories WHERE name = ?",
                                   (data.get("category"),)).fetchone()
            if row is None:
                return None, "Choose a valid category."
            values["category_id"] = row["id"]
        return values, None

    # --------------------------------------------------------------- pages
    @app.get("/")
    def home():
        return send_from_directory(app.static_folder, "index.html")

    # ---------------------------------------------------------------- auth
    @app.post("/api/register")
    def register():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        phone = normalize_phone(data.get("phone"))
        password = str(data.get("password", ""))

        if not name:
            return error("Name is required.", 400)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return error("Enter a valid email address.", 400)
        if phone is None:
            return error("Enter a valid Kenyan phone number, e.g. 0712345678.", 400)
        if len(password) < 8:
            return error("Password must be at least 8 characters.", 400)

        try:
            user_id = db.create_user(name, email, phone, password)  # role is always 'customer'
        except sqlite3.IntegrityError:
            return error("That email is already registered.", 409)
        session.clear()
        session["user_id"] = user_id
        return jsonify(user_json(current_user())), 201

    @app.post("/api/login")
    def login():
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        # Same message for "no such email" and "wrong password" so attackers learn nothing.
        if user is None or not check_password_hash(user["password_hash"], str(data.get("password", ""))):
            return error("Wrong email or password.", 401)
        session.clear()
        session["user_id"] = user["id"]
        return jsonify(user_json(user))

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    @login_required
    def me():
        return jsonify(user_json(current_user()))

    # ---------------------------------------------------------------- cars
    @app.get("/api/categories")
    def categories():
        rows = get_db().execute("SELECT name FROM categories ORDER BY id").fetchall()
        return jsonify([r["name"] for r in rows])

    @app.get("/api/cars")
    def list_cars():
        services.release_expired(get_db())  # free cars whose reservation ran out
        # Build the WHERE part from whichever filters were sent.
        # The ? marks keep user input out of the SQL text (this blocks SQL injection).
        where, params = [], []
        search = request.args.get("search", "").strip().lower()
        if search:
            where.append("LOWER(cars.make || ' ' || cars.model || ' ' || cars.year) LIKE ?")
            params.append(f"%{search}%")
        if request.args.get("category"):
            where.append("categories.name = ?")
            params.append(request.args["category"])
        if request.args.get("status"):
            where.append("cars.status = ?")
            params.append(request.args["status"])
        for arg, column, op in (("max_price", "cars.price", "<="), ("min_price", "cars.price", ">="),
                                ("year", "cars.year", "=")):
            if request.args.get(arg):
                if not request.args[arg].isdigit():
                    return error(f"{arg} must be a number.", 400)
                where.append(f"{column} {op} ?")
                params.append(int(request.args[arg]))

        sql = CAR_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY cars.id"
        rows = get_db().execute(sql, params).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/cars/<int:car_id>")
    def car_detail(car_id):
        services.release_expired(get_db())
        car = get_car(car_id)
        return jsonify(dict(car)) if car else error("Car not found.", 404)

    @app.post("/api/cars")
    @admin_required
    def add_car():
        values, problem = clean_car(request.get_json(silent=True) or {})
        if problem:
            return error(problem, 400)
        database = get_db()
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cursor = database.execute(f"INSERT INTO cars ({columns}) VALUES ({marks})", list(values.values()))
        database.commit()
        return jsonify(dict(get_car(cursor.lastrowid))), 201

    @app.put("/api/cars/<int:car_id>")
    @admin_required
    def update_car(car_id):
        if get_car(car_id) is None:
            return error("Car not found.", 404)
        values, problem = clean_car(request.get_json(silent=True) or {}, partial=True)
        if problem:
            return error(problem, 400)
        if "status" in values and services.has_active_order(get_db(), car_id):
            return error("This car has an open order. Update the order instead.", 409)
        if values:
            database = get_db()
            changes = ", ".join(f"{column} = ?" for column in values)
            database.execute(f"UPDATE cars SET {changes} WHERE id = ?", [*values.values(), car_id])
            database.commit()
        return jsonify(dict(get_car(car_id)))

    @app.delete("/api/cars/<int:car_id>")
    @admin_required
    def delete_car(car_id):
        if get_car(car_id) is None:
            return error("Car not found.", 404)
        if get_db().execute("SELECT 1 FROM orders WHERE car_id = ?", (car_id,)).fetchone():
            return error("This car has order history, so it can't be deleted. Mark it Sold instead.", 409)
        database = get_db()
        database.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        database.commit()
        return jsonify({"ok": True})

    # -------------------------------------------------------------- orders
    @app.errorhandler(ServiceError)
    def handle_service_error(problem):
        return error(problem.message, problem.status)

    def order_json(order_id, user):
        return services.order_rows(get_db(), order_id=order_id, admin=user["role"] == "admin")[0]

    @app.get("/api/config")
    def config():
        return jsonify({"mpesa_mode": app.mpesa.mode})

    @app.post("/api/orders")
    @login_required
    def create_order():
        car_id = (request.get_json(silent=True) or {}).get("car_id")
        if not isinstance(car_id, int) or isinstance(car_id, bool):
            return error("car_id is required.", 400)
        user = current_user()
        order_id = services.place_order(get_db(), user["id"], car_id)
        return jsonify(order_json(order_id, user)), 201

    @app.get("/api/orders")
    @login_required
    def my_orders():
        services.release_expired(get_db())
        return jsonify(services.order_rows(get_db(), user_id=current_user()["id"]))

    @app.post("/api/orders/<int:order_id>/cancel")
    @login_required
    def cancel_order(order_id):
        user = current_user()
        services.cancel_order(get_db(), order_id, user)
        return jsonify(order_json(order_id, user))

    @app.post("/api/orders/<int:order_id>/pay")
    @login_required
    def pay_order(order_id):
        phone = (request.get_json(silent=True) or {}).get("phone")
        result = services.start_payment(get_db(), app.mpesa, current_user(), order_id, phone)
        body = {"message": "Check your phone and enter your M-Pesa PIN."}
        if app.mpesa.is_mock:
            body["checkout_request_id"] = result["checkout_request_id"]
        return jsonify(body), 202

    # ------------------------------------------------------------- M-Pesa
    @app.post("/api/mpesa/callback/<token>")
    def mpesa_callback(token):
        # Safaricom does not sign its messages, so the address itself carries a secret.
        if not hmac.compare_digest(token, app.config["MPESA_CALLBACK_SECRET"]):
            return error("Not found.", 404)
        services.process_callback(get_db(), request.get_json(silent=True) or {})
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})

    @app.post("/api/dev/mpesa/simulate")
    @login_required
    def simulate_mpesa():
        """Mock mode only: pretend to be Safaricom, so you can test without a real phone."""
        if not app.mpesa.is_mock:
            return error("Not found.", 404)
        data = request.get_json(silent=True) or {}
        user = current_user()
        payment = get_db().execute(
            "SELECT p.*, o.user_id FROM payments p JOIN orders o ON o.id = p.order_id "
            "WHERE p.checkout_request_id = ?", (data.get("checkout_request_id"),)).fetchone()
        if payment is None or (payment["user_id"] != user["id"] and user["role"] != "admin"):
            return error("Payment not found.", 404)
        callback = {"CheckoutRequestID": payment["checkout_request_id"]}
        if data.get("outcome") == "success":
            callback.update(ResultCode=0, ResultDesc="The service request is processed successfully.",
                            CallbackMetadata={"Item": [
                                {"Name": "Amount", "Value": payment["amount"]},
                                {"Name": "MpesaReceiptNumber", "Value": "MOCK" + secrets.token_hex(3).upper()},
                                {"Name": "PhoneNumber", "Value": int(payment["phone"])}]})
        else:
            callback.update(ResultCode=1032, ResultDesc="Request cancelled by user")
        services.process_callback(get_db(), {"Body": {"stkCallback": callback}})
        return jsonify({"ok": True})

    # -------------------------------------------------------- admin: orders
    @app.get("/api/admin/orders")
    @admin_required
    def admin_orders():
        services.release_expired(get_db())
        return jsonify(services.order_rows(get_db(), admin=True))

    @app.post("/api/admin/orders/<int:order_id>/status")
    @admin_required
    def admin_order_status(order_id):
        status = (request.get_json(silent=True) or {}).get("status")
        services.admin_set_status(get_db(), order_id, status, current_user())
        return jsonify(order_json(order_id, current_user()))

    @app.get("/api/admin/report")
    @admin_required
    def admin_report():
        return jsonify(services.sales_report(get_db()))

    # ---- NEW: edit a car ----
    @app.put("/api/admin/cars/<int:car_id>")
    @admin_required
    def admin_update_car(car_id):
        data = request.get_json(silent=True) or {}
        allowed = ["make", "model", "year", "category", "condition", "price", "specs"]
        fields = {k: data[k] for k in allowed if k in data}
        if not fields:
            return jsonify(error="Nothing to update"), 400
        sets = ", ".join(f"{k} = ?" for k in fields)
        db = get_db()
        with transaction(db):
            db.execute(f"UPDATE cars SET {sets} WHERE id = ?", (*fields.values(), car_id))
        return jsonify(ok=True)

    return app


app = create_app()
