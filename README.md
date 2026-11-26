# SmartPark - AI Car Parking System (website)

A Flask website that sits on top of the `parking.py` engine from the CSC203B report
(simulated ANPR, BFS slot allocation, EMA demand pricing, tickets, reservations, slot faults).

## Run it

```bash
pip install -r requirements.txt
python app.py            # then open http://127.0.0.1:5000
python -m unittest -v    # runs the 18 report test cases (TC01-TC18) + 2 extra checks
```

Sample admin login: `admin` / `admin123` (change it with the env var `SMARTPARK_ADMIN_PASSWORD`
before the first run). To register another admin, enter the admin code `ADMIN2026` on the register page
(`SMARTPARK_ADMIN_CODE` changes it). Anyone else registers as a normal user.

## Deploy online

This is a Python (Flask) server, so it needs a host that runs Python. **Netlify only serves static
files and JavaScript functions, which is why the Netlify URL shows "Page not found".**

### Render (free, recommended)

1. Push this repo to GitHub (already done: `sriharshas0809/smartpark`).
2. Go to https://dashboard.render.com, then **New > Blueprint**, and pick this repo.
   Render reads `render.yaml` and creates the web service.
3. When asked, set `SMARTPARK_ADMIN_PASSWORD` (the admin login password) and optionally
   `SMARTPARK_ADMIN_CODE`. `SMARTPARK_SECRET` is generated for you.
4. Wait for the build, then open the `https://smartpark-xxxx.onrender.com` URL Render gives you.

Notes: the free plan sleeps after ~15 minutes idle (the first visit takes ~30 s to wake up), and its
disk is temporary, so `smartpark.db` (users, history) resets on each redeploy or restart.

### Other hosts

- **Railway / Heroku-style hosts** use the `Procfile`.
- **PythonAnywhere**: upload the files, create a Flask web app pointing the WSGI file at
  `from app import app as application`, and run `pip install -r requirements.txt`. Its disk is
  persistent, so the SQLite database survives restarts.

Always run a single worker process (as the configs do): lot state is held in memory.

## Requirement map

| Req | Where it lives |
| --- | --- |
| FR1 register / login (admin, user) | `/register`, `/login`, roles in `app.py` |
| FR2 real-time slot detection | Lot map polls every 4 s; admin "Simulate stray vehicle" mimics a sensor alert |
| FR3 availability display | Home board, `/lots`, `/lot/<id>` map |
| FR4 search and filter by area | `/lots` (area text and vehicle type filters) |
| FR5 book in advance | Lot page, Reserve tab (bay held 15 min, auto-released) |
| FR6 entry and exit with ticket | Lot page, Entry gate and Exit and pay tabs |
| FR7 payment method | UPI, UPI Lite, Net Banking, Wallet, EMI (simulated, no card data stored) |
| FR8 notifications | `/notifications` and the badge in the navbar |
| FR9 admin manage slots, pricing, reports | `/admin` |
| FR10 history | `/history` |
| FR11 turn-by-turn guidance | Click any bay: BFS route on the map plus written steps |

## Files

```
app.py              routes, auth, JSON API
parking.py          engine (Slot, PlateRecognizer, DemandPredictor, Ticket, ParkingLot)
db.py               SQLite: users, history, notifications
test_parking.py     unit tests
templates/, static/ pages, style.css, lot.js
```

## Good to know

- Lot state (slots, tickets, reservations) lives in memory and resets when the server restarts.
  Users, history and alerts are kept in `smartpark.db`.
- The price rate is locked into a ticket at entry. Use "Minutes stayed" on the exit tab to demo longer stays.
- For the report: take screenshots of the lot map (with a route shown), the entry ticket,
  the exit receipt, a rejected plate, the admin page and the unit test output.
- This is a classroom prototype: no CSRF protection, development server, simulated ANPR and payments.
