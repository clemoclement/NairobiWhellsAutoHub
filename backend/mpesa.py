"""M-Pesa STK Push (Safaricom Daraja API).

Three modes, chosen by the MPESA_MODE environment variable:
  mock        (default) no internet or credentials needed; you press "simulate" buttons.
  sandbox     the real Daraja test environment.
  production  the real thing, with real money.
"""
import base64
import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}
REQUIRED = ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE",
            "MPESA_PASSKEY", "MPESA_PUBLIC_URL")


class MpesaError(Exception):
    pass


class MpesaClient:
    def __init__(self, config):
        self.config = config
        self.mode = config["MPESA_MODE"]
        if self.mode not in ("mock", *BASE_URLS):
            raise RuntimeError("MPESA_MODE must be mock, sandbox or production.")
        if self.mode != "mock" and config["MPESA_CALLBACK_SECRET"] == "dev-callback-secret":
            raise RuntimeError("Set a long random MPESA_CALLBACK_SECRET before using real M-Pesa.")

    @property
    def is_mock(self):
        return self.mode == "mock"

    def stk_push(self, phone, amount, order_id):
        """Ask M-Pesa to show a payment prompt on the customer's phone."""
        if self.is_mock:
            return {"checkout_request_id": "ws_CO_MOCK_" + uuid.uuid4().hex[:16],
                    "merchant_request_id": "mock"}

        missing = [name for name in REQUIRED if not self.config.get(name)]
        if missing:
            raise MpesaError("M-Pesa is not configured: " + ", ".join(missing))

        # Daraja wants East Africa Time (UTC+3), formatted like 20261021143000.
        timestamp = datetime.now(timezone(timedelta(hours=3))).strftime("%Y%m%d%H%M%S")
        shortcode = self.config["MPESA_SHORTCODE"]
        password = base64.b64encode(
            f"{shortcode}{self.config['MPESA_PASSKEY']}{timestamp}".encode()).decode()
        callback = (f"{self.config['MPESA_PUBLIC_URL'].rstrip('/')}"
                    f"/api/mpesa/callback/{self.config['MPESA_CALLBACK_SECRET']}")
        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback,
            "AccountReference": f"ORDER{order_id}",
            "TransactionDesc": "Car booking deposit",
        }
        data = self._post("/mpesa/stkpush/v1/processrequest", payload, self._token())
        if str(data.get("ResponseCode")) != "0" or "CheckoutRequestID" not in data:
            raise MpesaError(data.get("errorMessage") or data.get("ResponseDescription")
                             or "M-Pesa rejected the request.")
        return {"checkout_request_id": data["CheckoutRequestID"],
                "merchant_request_id": data.get("MerchantRequestID", "")}

    # ---- low-level web requests (kept separate so tests can replace them) ----
    def _token(self):
        key, secret = self.config["MPESA_CONSUMER_KEY"], self.config["MPESA_CONSUMER_SECRET"]
        basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
        data = self._request("/oauth/v1/generate?grant_type=client_credentials",
                             headers={"Authorization": f"Basic {basic}"})
        if "access_token" not in data:
            raise MpesaError("M-Pesa login failed. Check your consumer key and secret.")
        return data["access_token"]

    def _post(self, path, payload, token):
        return self._request(path, payload, {"Authorization": f"Bearer {token}"})

    def _request(self, path, payload=None, headers=None):
        url = BASE_URLS[self.mode] + path
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as err:
            try:
                return json.loads(err.read())
            except ValueError:
                raise MpesaError(f"M-Pesa returned HTTP {err.code}.")
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            raise MpesaError("Could not reach M-Pesa. Please try again.")
