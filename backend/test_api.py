"""Run with:  python -m unittest -v
Each test gets a brand-new temporary database, so tests never affect each other."""
import os
import tempfile
import unittest

import db
from app import create_app

GOOD_USER = {"name": "Amina", "email": "amina@example.com", "phone": "0712345678", "password": "secret123"}
GOOD_CAR = {"make": "Honda", "model": "Fit", "year": 2020, "category": "Small Personal Cars",
            "price": 1200000, "condition": "Used", "specs": "1.3L"}


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = create_app({"TESTING": True, "DATABASE": self.path})
        self.client = self.app.test_client()

    def tearDown(self):
        os.remove(self.path)

    def make_admin(self):
        with self.app.app_context():
            db.create_user("Boss", "boss@example.com", "254700000000", "adminpass1", role="admin")
        return self.client.post("/api/login", json={"email": "boss@example.com", "password": "adminpass1"})

    def register(self, **overrides):
        return self.client.post("/api/register", json={**GOOD_USER, **overrides})


class RegisterAndLoginTests(ApiTestCase):
    def test_register_creates_customer_and_logs_in(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["role"], "customer")
        self.assertEqual(self.client.get("/api/me").json["email"], "amina@example.com")

    def test_phone_is_stored_in_mpesa_format(self):
        self.assertEqual(self.register().json["phone"], "254712345678")

    def test_user_cannot_make_themselves_admin(self):
        self.assertEqual(self.register(role="admin").json["role"], "customer")

    def test_password_is_not_stored_in_plain_text(self):
        self.register()
        with self.app.app_context():
            stored = db.get_db().execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("secret123", stored)

    def test_duplicate_email_is_rejected(self):
        self.register()
        self.assertEqual(self.register().status_code, 409)

    def test_short_password_is_rejected(self):
        self.assertEqual(self.register(password="short").status_code, 400)

    def test_bad_phone_is_rejected(self):
        self.assertEqual(self.register(phone="12345").status_code, 400)

    def test_bad_email_is_rejected(self):
        self.assertEqual(self.register(email="not-an-email").status_code, 400)

    def test_login_with_correct_password(self):
        self.register()
        self.client.post("/api/logout")
        response = self.client.post("/api/login", json={"email": "AMINA@example.com", "password": "secret123"})
        self.assertEqual(response.status_code, 200)

    def test_login_with_wrong_password(self):
        self.register()
        self.client.post("/api/logout")
        response = self.client.post("/api/login", json={"email": GOOD_USER["email"], "password": "wrongpass"})
        self.assertEqual(response.status_code, 401)

    def test_unknown_email_gets_the_same_error_as_wrong_password(self):
        self.register()
        self.client.post("/api/logout")
        wrong_pw = self.client.post("/api/login", json={"email": GOOD_USER["email"], "password": "nope"})
        no_user = self.client.post("/api/login", json={"email": "ghost@example.com", "password": "nope"})
        self.assertEqual(wrong_pw.json, no_user.json)

    def test_me_requires_login(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_logout_ends_the_session(self):
        self.register()
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/me").status_code, 401)


class CarReadTests(ApiTestCase):
    def test_lists_the_eight_sample_cars(self):
        self.assertEqual(len(self.client.get("/api/cars").json), 8)

    def test_filter_by_category(self):
        cars = self.client.get("/api/cars?category=SUVs").json
        self.assertEqual({c["category"] for c in cars}, {"SUVs"})
        self.assertEqual(len(cars), 2)

    def test_search_is_case_insensitive(self):
        self.assertEqual(len(self.client.get("/api/cars?search=TOYOTA").json), 2)

    def test_filter_by_max_price(self):
        cars = self.client.get("/api/cars?max_price=2000000").json
        self.assertTrue(all(c["price"] <= 2000000 for c in cars))
        self.assertEqual(len(cars), 2)

    def test_filter_by_year(self):
        self.assertEqual(len(self.client.get("/api/cars?year=2021").json), 2)

    def test_filters_combine(self):
        self.assertEqual(len(self.client.get("/api/cars?search=toyota&category=Sedans").json), 1)

    def test_non_numeric_price_is_rejected(self):
        self.assertEqual(self.client.get("/api/cars?max_price=abc").status_code, 400)

    def test_sql_injection_attempt_is_harmless(self):
        response = self.client.get("/api/cars?search=' OR 1=1 --")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, [])

    def test_car_detail(self):
        self.assertEqual(self.client.get("/api/cars/1").json["make"], "Lamborghini")

    def test_unknown_car_is_404(self):
        self.assertEqual(self.client.get("/api/cars/999").status_code, 404)

    def test_categories_list(self):
        self.assertEqual(len(self.client.get("/api/categories").json), 4)


class CarAdminTests(ApiTestCase):
    def test_anonymous_cannot_add_car(self):
        self.assertEqual(self.client.post("/api/cars", json=GOOD_CAR).status_code, 401)

    def test_customer_cannot_add_car(self):
        self.register()
        self.assertEqual(self.client.post("/api/cars", json=GOOD_CAR).status_code, 403)

    def test_customer_cannot_delete_car(self):
        self.register()
        self.assertEqual(self.client.delete("/api/cars/1").status_code, 403)
        self.assertEqual(len(self.client.get("/api/cars").json), 8)

    def test_admin_can_add_car(self):
        self.make_admin()
        response = self.client.post("/api/cars", json=GOOD_CAR)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "available")
        self.assertEqual(len(self.client.get("/api/cars").json), 9)

    def test_admin_add_rejects_bad_data(self):
        self.make_admin()
        for bad in ({"make": " "}, {"year": 1850}, {"price": 0}, {"price": "cheap"},
                    {"price": True}, {"category": "Boats"}, {"condition": "Broken"}):
            response = self.client.post("/api/cars", json={**GOOD_CAR, **bad})
            self.assertEqual(response.status_code, 400, msg=f"{bad} should be rejected")

    def test_admin_can_update_price_only(self):
        self.make_admin()
        response = self.client.put("/api/cars/1", json={"price": 36000000})
        self.assertEqual(response.json["price"], 36000000)
        self.assertEqual(response.json["make"], "Lamborghini")

    def test_admin_can_change_status(self):
        self.make_admin()
        self.assertEqual(self.client.put("/api/cars/1", json={"status": "sold"}).json["status"], "sold")

    def test_admin_update_rejects_bad_status(self):
        self.make_admin()
        self.assertEqual(self.client.put("/api/cars/1", json={"status": "gone"}).status_code, 400)

    def test_update_unknown_car_is_404(self):
        self.make_admin()
        self.assertEqual(self.client.put("/api/cars/999", json={"price": 5}).status_code, 404)

    def test_admin_can_delete_car(self):
        self.make_admin()
        self.assertEqual(self.client.delete("/api/cars/1").status_code, 200)
        self.assertEqual(self.client.get("/api/cars/1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
