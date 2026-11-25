"""SmartPark - web front end for the AI Car Parking System (Flask)."""
import os
from datetime import datetime
from functools import wraps

from flask import (Flask, g, jsonify, redirect, render_template, request, session,
                   url_for, flash, abort)
from werkzeug.security import check_password_hash, generate_password_hash

import db
from parking import ParkingLot, ParkingError, ReviewRequired, KINDS

app = Flask(__name__)
app.secret_key = os.environ.get("SMARTPARK_SECRET", "dev-secret-change-me")
app.teardown_appcontext(db.close_db)

ADMIN_CODE = os.environ.get("SMARTPARK_ADMIN_CODE", "ADMIN2026")
PAYMENT_METHODS = ["UPI", "UPI Lite", "Net Banking", "Wallet", "EMI"]

LAYOUT_A = """
G . . . . . . . .
. C C . C C . B B
. C C . C C . B B
. . . . . . . . .
. E E . C C . B B
. E E . C C . . .
"""
LAYOUT_B = """
G . . . . . .
. C C . B B .
. C C . B B .
. . . . . . .
. E E . C C .
"""
LAYOUT_C = """
G . . . . . . .
. C C . C C . C
. C C . C C . C
. . . . . . . .
. C C . C C . B
. C C . C C . B
"""
LOTS = {
    1: ParkingLot(1, "City Centre Parking", "Downtown", LAYOUT_A),
    2: ParkingLot(2, "Tech Park Parking", "Tech Park", LAYOUT_B, rates={"CAR": 40, "BIKE": 20, "EV": 50}),
    3: ParkingLot(3, "Railway Station Parking", "Railway Station", LAYOUT_C, rates={"CAR": 25, "BIKE": 10, "EV": 35}),
}
db.init_db()


# ---------------------------------------------------------------- helpers
def get_lot(lid):
    if lid not in LOTS:
        abort(404)
    return LOTS[lid]


def housekeeping():
    """Release expired reservations and raise 'time expiring' alerts (FR8)."""
    for lot in LOTS.values():
        for r in lot.release_expired():
            if r["user_id"]:
                db.notify(r["user_id"], f"Your reservation of slot {r['slot_id']} at {lot.name} expired and was released.")
        for r in lot.expiring_soon(5):
            if r["user_id"]:
                db.notify(r["user_id"], f"Your reservation of slot {r['slot_id']} at {lot.name} expires in "
                                        f"about {r['minutes_left']} min. Arrive soon.")


@app.before_request
def load_user():
    g.user = None
    if request.endpoint == "static":
        return
    if session.get("uid"):
        g.user = db.q("SELECT * FROM users WHERE id=?", (session["uid"],), one=True)
        if g.user is None:
            session.clear()
    housekeeping()


@app.context_processor
def inject():
    unread = 0
    if g.get("user"):
        unread = db.q("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
                      (g.user["id"],), one=True)["c"]
    return {"user": g.get("user"), "unread": unread, "PAYMENT_METHODS": PAYMENT_METHODS}


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            flash("Log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            abort(403)
        return f(*a, **k)
    return w


def jerr(msg, code=400, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def api_login(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            return jerr("Log in first to do this.", 401)
        return f(*a, **k)
    return w


def api_admin(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user or g.user["role"] != "admin":
            return jerr("Only administrators can do this.", 403)
        return f(*a, **k)
    return w


def body():
    return request.get_json(silent=True) or {}


def is_admin():
    return bool(g.user and g.user["role"] == "admin")


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html", lots=[(l, l.summary()) for l in LOTS.values()])


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        username, email, pw = f.get("username", "").strip(), f.get("email", "").strip(), f.get("password", "")
        code, role, err = f.get("admin_code", "").strip(), "user", None
        if len(username) < 3:
            err = "Choose a username with at least 3 characters."
        elif "@" not in email:
            err = "Enter a valid email address."
        elif len(pw) < 6:
            err = "Use a password with at least 6 characters."
        elif pw != f.get("confirm", ""):
            err = "The two passwords do not match."
        elif code and code != ADMIN_CODE:
            err = "That admin code is not valid. Leave it empty to register as a normal user."
        elif db.q("SELECT 1 FROM users WHERE username=?", (username,), one=True):
            err = "That username is taken. Try another."
        if err:
            flash(err, "error")
            return render_template("register.html", form=f), 400
        if code:
            role = "admin"
        uid = db.ex("INSERT INTO users(username,email,password_hash,role,created) VALUES(?,?,?,?,?)",
                    (username, email, generate_password_hash(pw), role, db.now_str()))
        session["uid"] = uid
        db.notify(uid, f"Welcome to SmartPark, {username}. Find a lot and reserve your first slot.")
        return redirect(url_for("lots"))
    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db.q("SELECT * FROM users WHERE username=?", (request.form.get("username", "").strip(),), one=True)
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = u["id"]
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else
                            url_for("admin" if u["role"] == "admin" else "lots"))
        flash("Username or password is wrong. Check them and try again.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/lots")
def lots():
    qtext, kind = request.args.get("q", "").strip().lower(), request.args.get("kind", "").upper()
    rows = []
    for lot in LOTS.values():
        s = lot.summary()
        if qtext and qtext not in lot.area.lower() and qtext not in lot.name.lower():
            continue
        if kind in KINDS and s["kinds"][kind]["free"] == 0:
            continue
        rows.append((lot, s))
    rows.sort(key=lambda x: -x[1]["free"])
    areas = sorted({l.area for l in LOTS.values()})
    return render_template("lots.html", rows=rows, areas=areas, q=request.args.get("q", ""), kind=kind)


@app.route("/lot/<int:lid>")
def lot_page(lid):
    return render_template("lot.html", lot=get_lot(lid))


@app.route("/history")
@login_required
def history():
    rows = db.q("SELECT * FROM history WHERE user_id=? ORDER BY id DESC", (g.user["id"],))
    tot = db.q("SELECT COUNT(*) n, COALESCE(SUM(fee),0) fee, COALESCE(SUM(minutes),0) mins "
               "FROM history WHERE user_id=?", (g.user["id"],), one=True)
    return render_template("history.html", rows=rows, tot=tot)


@app.route("/notifications")
@login_required
def notifications():
    rows = db.q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (g.user["id"],))
    db.ex("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    return render_template("notifications.html", rows=rows)


# ---------------------------------------------------------------- lot API
@app.get("/api/lot/<int:lid>/status")
def api_status(lid):
    lot = get_lot(lid)
    return jsonify(lot.status(viewer_id=g.user["id"] if g.user else None, is_admin=is_admin()))


@app.get("/api/lot/<int:lid>/path/<sid>")
def api_path(lid, sid):
    try:
        return jsonify(ok=True, **get_lot(lid).path_to(sid))
    except ParkingError as e:
        return jerr(str(e))


@app.post("/api/lot/<int:lid>/reserve")
@api_login
def api_reserve(lid):
    lot, d = get_lot(lid), body()
    try:
        r = lot.reserve(d.get("plate"), d.get("kind"), user_id=g.user["id"])
    except ParkingError as e:
        return jerr(str(e))
    db.notify(g.user["id"], f"Slot {r['slot_id']} at {lot.name} is reserved for {r['plate']} for the next "
                            f"{lot.hold_minutes} minutes.")
    return jsonify(ok=True, plate=r["plate"], slot=r["slot_id"], kind=r["kind"], hold_minutes=lot.hold_minutes)


@app.post("/api/lot/<int:lid>/cancel")
@api_login
def api_cancel(lid):
    lot, d = get_lot(lid), body()
    plate = lot.recognizer.sanitise(d.get("plate"))
    res = lot.reservations.get(plate)
    if res and res["user_id"] != g.user["id"] and not is_admin():
        return jerr("That reservation belongs to another user.", 403)
    try:
        r = lot.cancel_reservation(plate)
    except ParkingError as e:
        return jerr(str(e))
    db.notify(g.user["id"], f"Reservation of slot {r['slot_id']} at {lot.name} was cancelled.")
    return jsonify(ok=True, slot=r["slot_id"])


@app.post("/api/lot/<int:lid>/entry")
@api_login
def api_entry(lid):
    lot, d = get_lot(lid), body()
    try:
        noise = float(d.get("noise") or 0)
    except ValueError:
        return jerr("Camera noise must be a number between 0 and 1.")
    try:
        t = lot.enter(d.get("plate"), d.get("kind"), noise=noise, user_id=g.user["id"])
    except ReviewRequired as e:
        lot.queue_review(e.plate, (d.get("kind") or "").upper(), e.confidence, g.user["id"])
        return jerr(f"{e}. The request was sent to the attendant.", 409, review=True)
    except ParkingError as e:
        return jerr(str(e))
    db.notify(g.user["id"], f"Entry recorded at {lot.name}: ticket {t.tid}, slot {t.slot_id} for {t.plate}.")
    return jsonify(ok=True, **t.to_dict())


def _own_ticket(lot, ticket_id):
    t = lot.tickets.get((ticket_id or "").strip().upper())
    if t and t.user_id not in (None, g.user["id"]) and not is_admin():
        return False
    return True


@app.post("/api/lot/<int:lid>/quote")
@api_login
def api_quote(lid):
    lot, d = get_lot(lid), body()
    if not _own_ticket(lot, d.get("ticket")):
        return jerr("That ticket belongs to another user.", 403)
    try:
        minutes = float(d["minutes"]) if d.get("minutes") not in (None, "") else None
        t, mins, fee = lot.quote(d.get("ticket"), minutes=minutes)
    except ValueError:
        return jerr("Minutes stayed must be a number.")
    except ParkingError as e:
        return jerr(str(e))
    return jsonify(ok=True, ticket_id=t.tid, plate=t.plate, slot=t.slot_id, minutes=mins,
                   multiplier=t.multiplier, fee=fee)


@app.post("/api/lot/<int:lid>/exit")
@api_login
def api_exit(lid):
    lot, d = get_lot(lid), body()
    method = d.get("method")
    if method not in PAYMENT_METHODS:
        return jerr("Choose a payment method: " + ", ".join(PAYMENT_METHODS) + ".")
    if not _own_ticket(lot, d.get("ticket")):
        return jerr("That ticket belongs to another user.", 403)
    try:
        minutes = float(d["minutes"]) if d.get("minutes") not in (None, "") else None
        r = lot.exit_vehicle(d.get("ticket"), d.get("paid"), minutes=minutes)
    except ValueError:
        return jerr("Minutes stayed must be a number.")
    except ParkingError as e:
        return jerr(str(e))
    db.ex("INSERT INTO history(user_id,lot_id,lot_name,ticket_id,plate,kind,slot_id,entry_time,exit_time,"
          "minutes,multiplier,fee,method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (r["user_id"] or g.user["id"], lot.lot_id, lot.name, r["ticket_id"], r["plate"], r["kind"], r["slot"],
           r["entry_dt"].strftime("%Y-%m-%d %H:%M:%S"), r["exit_dt"].strftime("%Y-%m-%d %H:%M:%S"),
           r["minutes"], r["multiplier"], r["fee"], method))
    if r["user_id"]:
        db.notify(r["user_id"], f"Payment of Rs {r['fee']:.2f} by {method} received for ticket {r['ticket_id']} "
                                f"at {lot.name}. Safe travels.")
    for k in ("entry_dt", "exit_dt", "user_id"):
        r.pop(k)
    return jsonify(ok=True, method=method, **r)


# ---------------------------------------------------------------- admin
def _reports():
    one = lambda sql: db.q(sql, one=True)
    return {
        "totals": one("SELECT COUNT(*) n, COALESCE(SUM(fee),0) fee, COALESCE(AVG(minutes),0) mins FROM history"),
        "by_day": db.q("SELECT substr(exit_time,1,10) k, SUM(fee) v FROM history GROUP BY k ORDER BY k DESC LIMIT 7"),
        "by_lot": db.q("SELECT lot_name k, SUM(fee) v FROM history GROUP BY k ORDER BY v DESC"),
        "by_method": db.q("SELECT method k, COUNT(*) v FROM history GROUP BY k ORDER BY v DESC"),
        "by_kind": db.q("SELECT kind k, COUNT(*) v FROM history GROUP BY k ORDER BY v DESC"),
    }


@app.route("/admin")
@admin_required
def admin():
    lid = request.args.get("lot", 1, type=int)
    lot = get_lot(lid)
    st = lot.status(is_admin=True)
    users = db.q("SELECT COUNT(*) c FROM users", one=True)["c"]
    return render_template("admin.html", lots=[(l, l.summary()) for l in LOTS.values()], lot=lot,
                           st=st, reviews=lot.review_queue, rep=_reports(), users=users)


@app.post("/api/admin/lot/<int:lid>/slot/<sid>")
@api_admin
def api_admin_slot(lid, sid):
    lot, action = get_lot(lid), body().get("action")
    fn = {"fault": lot.set_out_of_service, "restore": lot.restore,
          "flag": lot.flag_unticketed, "clear": lot.clear_flag}.get(action)
    if fn is None:
        return jerr("Unknown action.")
    try:
        fn(sid)
    except ParkingError as e:
        return jerr(str(e))
    return jsonify(ok=True)


@app.post("/api/admin/lot/<int:lid>/pricing")
@api_admin
def api_admin_pricing(lid):
    lot, d = get_lot(lid), body()
    try:
        lot.set_pricing(rates=d.get("rates"), grace=d.get("grace"), override=d.get("override"))
    except (ParkingError, ValueError) as e:
        return jerr(str(e) if isinstance(e, ParkingError) else "Enter numbers only.")
    return jsonify(ok=True)


@app.post("/api/admin/lot/<int:lid>/review/<int:rid>")
@api_admin
def api_admin_review(lid, rid):
    lot, action = get_lot(lid), body().get("action")
    try:
        item = lot.get_review(rid)
        if action == "approve":
            t = lot.enter(item["plate"], item["kind"], noise=1 - item["confidence"],
                          manual_ok=True, user_id=item["user_id"])
            if item["user_id"]:
                db.notify(item["user_id"], f"The attendant verified {t.plate}. Ticket {t.tid}, slot {t.slot_id} at {lot.name}.")
        elif action == "reject":
            if item["user_id"]:
                db.notify(item["user_id"], f"The attendant could not verify plate {item['plate']} at {lot.name}. "
                                           f"Check the plate and try again.")
        else:
            return jerr("Unknown action.")
        lot.drop_review(rid)
    except ParkingError as e:
        return jerr(str(e))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
