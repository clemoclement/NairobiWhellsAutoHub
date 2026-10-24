"""Run with:  python -m unittest -v
Covers orders, double-selling protection, expiry, M-Pesa payments and admin order rules."""
import base64
import sqlite3
import threading
import time
import unittest

import db
from app import create_app
from mpesa import MpesaClient, MpesaError
from test_api import GOOD_USER, ApiTestCase

# Sample cars (by id): 1 Lamborghini 38.5M, 5 Mercedes C200 4.1M, 6 Corolla 3.6M (deposit 360,000)
COROLLA = 6
COROLLA_DEPOSIT = 360000


def new_customer(app, email):
    client = app.test_client()
    response = client.post("/api/register", json={**GOOD_USER, "email": email})
    assert response.status_code == 201, response.json
    return client


def new_admin(app):
    with app.app_context():
        db.create_user("Boss", "boss@example.com", "254700000000", "adminpass1", role="admin")
    client = app.test_client()
    assert client.post("/api/login", json={"email": "boss@example.com",
                                           "password": "adminpass1"}).status_code == 200
    return client


def callback_payload(checkout_id, code=0, amount=COROLLA_DEPOSIT, receipt="QWE123RTY4"):
    body = {"CheckoutRequestID": checkout_id, "ResultCode": code, "ResultDesc": "test"}
    if code == 0:
        body["CallbackMetadata"] = {"Item": [{"Name": "Amount", "Value": amount},
                                             {"Name": "MpesaReceiptNumber", "Value": receipt}]}
    return {"Body": {"stkCallback": body}}


class ShopTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = new_customer(self.app, "alice@example.com")
        self.bob = new_customer(self.app, "bob@example.com")

    def order(self, client, car_id=COROLLA):
        return client.post("/api/orders", json={"car_id": car_id})

    def car_status(self, car_id=COROLLA):
        return self.client.get(f"/api/cars/{car_id}").json["status"]

    def sql(self, statement, params=()):
        with self.app.app_context():
            return db.get_db().execute(statement, params).fetchall()

    def expire(self, order_id):
        self.sql("UPDATE orders SET expires_at = ? WHERE id = ?", (int(time.time()) - 5, order_id))

    def pay(self, client, order_id, **body):
        return client.post(f"/api/orders/{order_id}/pay", json=body)

    def simulate(self, client, checkout_id, outcome="success"):
        return client.post("/api/dev/mpesa/simulate",
                           json={"checkout_request_id": checkout_id, "outcome": outcome})

    def callback(self, payload, secret=None):
        secret = secret or self.app.config["MPESA_CALLBACK_SECRET"]
        return self.client.post(f"/api/mpesa/callback/{secret}", json=payload)

    def pay_and_succeed(self, client, order_id):
        checkout = self.pay(client, order_id).json["checkout_request_id"]
        self.simulate(client, checkout, "success")
        return checkout


# ===================================================================== ordering
class OrderTests(ShopTestCase):
    def test_order_reserves_the_car_and_sets_a_ten_percent_deposit(self):
        response = self.order(self.alice)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "pending")
        self.assertEqual(response.json["deposit"], COROLLA_DEPOSIT)
        self.assertEqual(self.car_status(), "reserved")

    def test_ordering_requires_login(self):
        self.assertEqual(self.order(self.client).status_code, 401)

    def test_bad_requests_are_rejected(self):
        self.assertEqual(self.alice.post("/api/orders", json={}).status_code, 400)
        self.assertEqual(self.alice.post("/api/orders", json={"car_id": "6"}).status_code, 400)
        self.assertEqual(self.order(self.alice, 999).status_code, 404)

    def test_second_customer_cannot_order_the_same_car(self):
        self.assertEqual(self.order(self.alice).status_code, 201)
        self.assertEqual(self.order(self.bob).status_code, 409)
        self.assertEqual(len(self.sql("SELECT id FROM orders")), 1)

    def test_same_customer_cannot_order_twice(self):
        self.order(self.alice)
        self.assertEqual(self.order(self.alice).status_code, 409)

    def test_two_simultaneous_orders_for_one_car_only_one_wins(self):
        for car_id in (1, 2, 3, 4):
            results, barrier = [], threading.Barrier(2)

            def attempt(client):
                barrier.wait()  # both customers click at exactly the same moment
                results.append(self.order(client, car_id).status_code)

            threads = [threading.Thread(target=attempt, args=(c,)) for c in (self.alice, self.bob)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(results), [201, 409], msg=f"car {car_id}")
        self.assertEqual(len(self.sql("SELECT id FROM orders")), 4)

    def test_database_itself_refuses_a_second_active_order(self):
        self.order(self.alice)
        with self.app.app_context():
            with self.assertRaises(sqlite3.IntegrityError):
                db.get_db().execute(
                    "INSERT INTO orders (user_id, car_id, deposit, created_at, expires_at) "
                    "VALUES (2, ?, 1, 0, 0)", (COROLLA,))

    def test_customers_only_see_their_own_orders(self):
        self.order(self.alice, COROLLA)
        self.order(self.bob, 5)
        mine = self.alice.get("/api/orders").json
        self.assertEqual([o["car"]["id"] for o in mine], [COROLLA])
        self.assertNotIn("customer_phone", mine[0])

    def test_customer_can_cancel_their_unpaid_order_and_the_car_is_freed(self):
        order_id = self.order(self.alice).json["id"]
        response = self.alice.post(f"/api/orders/{order_id}/cancel")
        self.assertEqual(response.json["status"], "cancelled")
        self.assertEqual(self.car_status(), "available")
        self.assertEqual(self.order(self.bob).status_code, 201)

    def test_cannot_cancel_someone_elses_order(self):
        order_id = self.order(self.alice).json["id"]
        self.assertEqual(self.bob.post(f"/api/orders/{order_id}/cancel").status_code, 404)
        self.assertEqual(self.car_status(), "reserved")

    def test_a_closed_order_cannot_be_cancelled_again(self):
        order_id = self.order(self.alice).json["id"]
        self.alice.post(f"/api/orders/{order_id}/cancel")
        self.assertEqual(self.alice.post(f"/api/orders/{order_id}/cancel").status_code, 409)


# ===================================================================== expiry
class ExpiryTests(ShopTestCase):
    def test_unpaid_order_is_released_after_24_hours(self):
        order_id = self.order(self.alice).json["id"]
        self.expire(order_id)
        self.assertEqual(self.car_status(), "available")
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "cancelled")
        self.assertEqual(self.order(self.bob).status_code, 201)

    def test_fresh_order_is_not_released(self):
        self.order(self.alice)
        self.assertEqual(self.car_status(), "reserved")

    def test_paid_order_never_expires(self):
        order_id = self.order(self.alice).json["id"]
        self.pay_and_succeed(self.alice, order_id)
        self.expire(order_id)
        self.assertEqual(self.car_status(), "reserved")
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "paid")

    def test_order_is_kept_while_a_payment_prompt_is_in_flight(self):
        order_id = self.order(self.alice).json["id"]
        self.pay(self.alice, order_id)
        self.expire(order_id)
        self.assertEqual(self.car_status(), "reserved")


# ==================================================================== payments
class PaymentTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.order_id = self.order(self.alice).json["id"]

    def test_pay_sends_a_prompt_and_records_it(self):
        response = self.pay(self.alice, self.order_id)
        self.assertEqual(response.status_code, 202)
        rows = self.sql("SELECT amount, phone, status FROM payments")
        self.assertEqual(tuple(rows[0]), (COROLLA_DEPOSIT, "254712345678", "initiated"))

    def test_successful_payment_marks_the_order_paid_and_stores_the_receipt(self):
        self.pay_and_succeed(self.alice, self.order_id)
        order = self.alice.get("/api/orders").json[0]
        self.assertEqual(order["status"], "paid")
        self.assertTrue(order["receipt"].startswith("MOCK"))
        self.assertEqual(self.car_status(), "reserved")

    def test_cancelled_prompt_leaves_the_order_pending_and_allows_another_try(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        self.simulate(self.alice, checkout, "cancelled")
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "pending")
        self.assertEqual(self.sql("SELECT status FROM payments")[0][0], "failed")
        self.assertEqual(self.pay(self.alice, self.order_id).status_code, 202)

    def test_second_prompt_within_90_seconds_is_blocked(self):
        self.pay(self.alice, self.order_id)
        self.assertEqual(self.pay(self.alice, self.order_id).status_code, 409)

    def test_cannot_pay_someone_elses_order(self):
        self.assertEqual(self.pay(self.bob, self.order_id).status_code, 404)

    def test_cannot_pay_an_expired_order(self):
        self.sql("UPDATE orders SET expires_at = ? WHERE id = ?", (int(time.time()) - 5, self.order_id))
        self.assertEqual(self.pay(self.alice, self.order_id).status_code, 409)

    def test_cannot_pay_an_order_twice(self):
        self.pay_and_succeed(self.alice, self.order_id)
        self.assertEqual(self.pay(self.alice, self.order_id).status_code, 409)

    def test_bad_phone_number_is_rejected(self):
        self.assertEqual(self.pay(self.alice, self.order_id, phone="123").status_code, 400)

    def test_a_different_phone_can_be_used_and_is_stored_in_mpesa_format(self):
        self.pay(self.alice, self.order_id, phone="0799 999 999")
        self.assertEqual(self.sql("SELECT phone FROM payments")[0][0], "254799999999")

    def test_simulate_cannot_touch_another_customers_payment(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        self.assertEqual(self.simulate(self.bob, checkout).status_code, 404)

    # ---- the callback Safaricom sends ----
    def test_callback_with_the_wrong_secret_is_refused(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        response = self.callback(callback_payload(checkout), secret="guessed-wrong")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "pending")

    def test_callback_with_the_right_secret_marks_the_order_paid(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        self.assertEqual(self.callback(callback_payload(checkout)).json["ResultCode"], 0)
        order = self.alice.get("/api/orders").json[0]
        self.assertEqual((order["status"], order["receipt"]), ("paid", "QWE123RTY4"))

    def test_repeated_callbacks_are_ignored(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        self.callback(callback_payload(checkout))
        self.callback(callback_payload(checkout, code=1032))  # a late, contradicting message
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "paid")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM payments WHERE status = 'success'")[0][0], 1)

    def test_callback_for_an_unknown_payment_changes_nothing(self):
        response = self.callback(callback_payload("ws_CO_DOES_NOT_EXIST"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "pending")

    def test_garbage_callback_is_handled_politely(self):
        self.assertEqual(self.callback({"hello": "world"}).status_code, 200)
        self.assertEqual(self.callback({}).status_code, 200)

    def test_wrong_amount_does_not_mark_the_order_paid_and_is_flagged_for_refund(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        self.callback(callback_payload(checkout, amount=5))
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "pending")
        admin = new_admin(self.app)
        self.assertTrue(admin.get("/api/admin/orders").json[0]["refund_needed"])

    def test_payment_arriving_after_cancellation_is_flagged_for_refund(self):
        checkout = self.pay(self.alice, self.order_id).json["checkout_request_id"]
        self.alice.post(f"/api/orders/{self.order_id}/cancel")
        self.callback(callback_payload(checkout))
        self.assertEqual(self.alice.get("/api/orders").json[0]["status"], "cancelled")
        self.assertEqual(self.car_status(), "available")
        self.assertEqual(self.sql("SELECT needs_refund FROM payments")[0][0], 1)


class RealMpesaModeTests(unittest.TestCase):
    CONFIG = {"MPESA_MODE": "sandbox", "MPESA_CONSUMER_KEY": "key", "MPESA_CONSUMER_SECRET": "secret",
              "MPESA_SHORTCODE": "174379", "MPESA_PASSKEY": "thepasskey",
              "MPESA_PUBLIC_URL": "https://example.ngrok.app/", "MPESA_CALLBACK_SECRET": "s" * 24}

    def make_client(self, reply):
        client, sent = MpesaClient(self.CONFIG), {}
        client._token = lambda: "TOKEN"

        def fake_post(path, payload, token):
            sent.update(path=path, payload=payload, token=token)
            return reply
        client._post = fake_post
        return client, sent

    def test_stk_push_builds_the_request_daraja_expects(self):
        client, sent = self.make_client({"ResponseCode": "0", "CheckoutRequestID": "ws_CO_1"})
        result = client.stk_push("254712345678", 360000, 7)
        payload = sent["payload"]
        self.assertEqual(result["checkout_request_id"], "ws_CO_1")
        self.assertEqual(sent["path"], "/mpesa/stkpush/v1/processrequest")
        self.assertEqual(sent["token"], "TOKEN")
        self.assertEqual((payload["Amount"], payload["PartyA"], payload["PhoneNumber"]),
                         (360000, "254712345678", "254712345678"))
        self.assertEqual(payload["CallBackURL"],
                         "https://example.ngrok.app/api/mpesa/callback/" + "s" * 24)
        decoded = base64.b64decode(payload["Password"]).decode()
        self.assertEqual(decoded, "174379thepasskey" + payload["Timestamp"])

    def test_rejection_from_mpesa_becomes_a_clear_error(self):
        client, _ = self.make_client({"ResponseCode": "1", "ResponseDescription": "Rejected"})
        with self.assertRaises(MpesaError):
            client.stk_push("254712345678", 100, 1)

    def test_real_mode_refuses_to_start_with_the_default_callback_secret(self):
        with self.assertRaises(RuntimeError):
            MpesaClient({**self.CONFIG, "MPESA_CALLBACK_SECRET": "dev-callback-secret"})

    def test_missing_credentials_give_a_clear_error_and_hide_the_simulator(self):
        import os, tempfile
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            app = create_app({"TESTING": True, "DATABASE": path, "MPESA_MODE": "sandbox",
                              "MPESA_CALLBACK_SECRET": "z" * 24})
            alice = new_customer(app, "alice@example.com")
            order_id = alice.post("/api/orders", json={"car_id": COROLLA}).json["id"]
            response = alice.post(f"/api/orders/{order_id}/pay", json={})
            self.assertEqual(response.status_code, 502)
            self.assertIn("not configured", response.json["error"])
            self.assertEqual(alice.post("/api/dev/mpesa/simulate", json={}).status_code, 404)
            self.assertEqual(alice.get("/api/config").json["mpesa_mode"], "sandbox")
        finally:
            os.remove(path)


# ==================================================================== admin
class AdminOrderTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.admin = new_admin(self.app)
        self.order_id = self.order(self.alice).json["id"]

    def set_status(self, status):
        return self.admin.post(f"/api/admin/orders/{self.order_id}/status", json={"status": status})

    def test_customers_cannot_use_admin_routes(self):
        self.assertEqual(self.alice.get("/api/admin/orders").status_code, 403)
        self.assertEqual(self.alice.get("/api/admin/report").status_code, 403)
        self.assertEqual(self.alice.post(f"/api/admin/orders/{self.order_id}/status",
                                         json={"status": "completed"}).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/orders").status_code, 401)

    def test_admin_sees_every_order_with_customer_details(self):
        self.order(self.bob, 5)
        orders = self.admin.get("/api/admin/orders").json
        self.assertEqual(len(orders), 2)
        self.assertEqual({o["customer_name"] for o in orders}, {"Amina"})
        self.assertEqual(orders[0]["customer_phone"], "254712345678")

    def test_admin_can_confirm_a_pending_order_but_not_a_paid_one(self):
        self.assertEqual(self.set_status("confirmed").json["status"], "confirmed")
        self.assertEqual(self.set_status("confirmed").status_code, 409)

    def test_a_confirmed_order_can_still_be_paid(self):
        self.set_status("confirmed")
        self.assertEqual(self.pay(self.alice, self.order_id).status_code, 202)

    def test_an_unpaid_order_cannot_be_completed(self):
        self.assertEqual(self.set_status("completed").status_code, 409)
        self.assertEqual(self.car_status(), "reserved")

    def test_completing_a_paid_order_marks_the_car_sold(self):
        self.pay_and_succeed(self.alice, self.order_id)
        self.assertEqual(self.set_status("completed").json["status"], "completed")
        self.assertEqual(self.car_status(), "sold")
        self.assertEqual(self.order(self.bob).status_code, 409)

    def test_cancelling_a_paid_order_frees_the_car_and_flags_the_refund(self):
        self.pay_and_succeed(self.alice, self.order_id)
        self.assertEqual(self.alice.post(f"/api/orders/{self.order_id}/cancel").status_code, 403)
        self.assertEqual(self.set_status("cancelled").json["status"], "cancelled")
        self.assertEqual(self.car_status(), "available")
        self.assertTrue(self.admin.get("/api/admin/orders").json[0]["refund_needed"])

    def test_unknown_status_and_unknown_order_are_rejected(self):
        self.assertEqual(self.set_status("teleported").status_code, 400)
        self.assertEqual(self.admin.post("/api/admin/orders/999/status",
                                         json={"status": "confirmed"}).status_code, 404)

    def test_report_counts_only_completed_orders(self):
        empty = self.admin.get("/api/admin/report").json
        self.assertEqual((empty["cars_sold"], empty["best_category"]), (0, None))
        self.pay_and_succeed(self.alice, self.order_id)
        self.set_status("completed")
        self.order(self.bob, 5)  # reserved but never completed
        report = self.admin.get("/api/admin/report").json
        self.assertEqual(report["cars_sold"], 1)
        self.assertEqual(report["revenue"], 3600000)
        self.assertEqual(report["best_category"], "Sedans")
        self.assertEqual(report["deposits_collected"], COROLLA_DEPOSIT)
        self.assertEqual(report["cars_available"], 6)

    def test_car_with_an_open_order_cannot_have_its_status_changed_by_hand(self):
        response = self.admin.put(f"/api/cars/{COROLLA}", json={"status": "available"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.car_status(), "reserved")
        self.set_status("cancelled")
        self.assertEqual(self.admin.put(f"/api/cars/{COROLLA}", json={"status": "sold"}).status_code, 200)

    def test_car_with_order_history_cannot_be_deleted(self):
        self.assertEqual(self.admin.delete(f"/api/cars/{COROLLA}").status_code, 409)
        self.set_status("cancelled")
        self.assertEqual(self.admin.delete(f"/api/cars/{COROLLA}").status_code, 409)
        self.assertEqual(self.admin.delete("/api/cars/1").status_code, 200)


if __name__ == "__main__":
    unittest.main()
