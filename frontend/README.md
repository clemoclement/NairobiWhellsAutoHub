# AutoHub – car sales system

```
autohub/
  backend/    Flask + SQLite  (app.py, db.py, services.py, mpesa.py, tests)
  frontend/   index.html, admin.html, app.js, admin.js, api.js, util.js, styles.css
```

## Run it
1. `cd backend`
2. `pip install -r requirements.txt`
3. `python -m unittest` (81 tests should pass)
4. `flask --app app create-admin` (make your admin login)
5. `flask --app app run --debug`
6. Open **http://localhost:5000** (shop) and **http://localhost:5000/admin.html** (admin).

Do not use Live Server for this version. The pages must be opened through Flask,
because Flask also answers the `/api/...` requests.

## M-Pesa modes (environment variable MPESA_MODE)
| Mode | What happens |
|---|---|
| `mock` (default) | No internet or keys. Yellow "test mode" buttons pretend to be M-Pesa. |
| `sandbox` | Real Daraja test environment. |
| `production` | Real money. |

For `sandbox` set these before starting Flask (Windows: `set NAME=value`, Mac/Linux: `export NAME=value`):
`MPESA_MODE`, `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, `MPESA_PASSKEY`,
`MPESA_PUBLIC_URL` (a public https address such as your ngrok URL), `MPESA_CALLBACK_SECRET` (a long random string).
Safaricom sends payment results to `MPESA_PUBLIC_URL/api/mpesa/callback/<MPESA_CALLBACK_SECRET>`.

## Before going live
- Set a long random `SECRET_KEY` and `MPESA_CALLBACK_SECRET`; serve over HTTPS.
- Add login rate limiting, and set `SESSION_COOKIE_SECURE=True`.
- Optionally confirm each payment with Daraja's STK Query before marking it paid.
- Back up `autohub.db` regularly (or move to PostgreSQL).
- Old practice files (`data.js`, `storage.js`, `logic.js`) are no longer needed.
