"""AI Car Parking System - core engine (pure Python standard library).

Classes: Slot (state machine), PlateRecognizer (simulated ANPR), DemandPredictor
(exponential moving average), Ticket and ParkingLot (BFS slot allocation, fees,
reservations, faults, navigation).
"""
import math
import re
import threading
from collections import deque
from datetime import datetime, timedelta

FREE, RESERVED, OCCUPIED, OUT_OF_SERVICE = "FREE", "RESERVED", "OCCUPIED", "OUT_OF_SERVICE"
ALLOWED = {  # fixed state machine: nothing else is legal
    FREE: {RESERVED, OCCUPIED, OUT_OF_SERVICE},
    RESERVED: {FREE, OCCUPIED, OUT_OF_SERVICE},
    OCCUPIED: {FREE},
    OUT_OF_SERVICE: {FREE},
}
KINDS = ("CAR", "BIKE", "EV")
DEFAULT_RATES = {"CAR": 30.0, "BIKE": 15.0, "EV": 40.0}   # Rs per hour
GRACE_MINUTES = 10
PLATE_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$")   # e.g. KA01AB1234
CONF_THRESHOLD = 0.60
DIRS = {"U": (-1, 0), "R": (0, 1), "D": (1, 0), "L": (0, -1)}
DIR_OF = {v: k for k, v in DIRS.items()}


class ParkingError(Exception):
    """Any rule violation: lot full, bad plate, bad ticket, short payment ..."""


class ReviewRequired(ParkingError):
    """Plate is valid but the ANPR confidence is too low; an attendant must check."""

    def __init__(self, plate, confidence, threshold):
        super().__init__(f"Plate {plate} was read with confidence {confidence:.2f} "
                         f"(minimum {threshold:.2f}); manual review needed")
        self.plate, self.confidence = plate, confidence


def fmt_time(dt):
    return dt.strftime("%d %b %Y, %H:%M")


class Slot:
    def __init__(self, sid, kind, row, col):
        self.sid, self.kind, self.row, self.col = sid, kind, row, col
        self.state, self.plate, self.flagged = FREE, None, False

    def transition(self, new):
        if new not in ALLOWED[self.state]:
            raise ParkingError(f"Illegal slot transition {self.state} -> {new} for {self.sid}")
        self.state = new
        if new == FREE:
            self.plate = None


class PlateRecognizer:
    """Simulated ANPR: sanitises text, validates the format, returns a confidence."""

    def __init__(self, threshold=CONF_THRESHOLD, manual_review=True):
        self.threshold, self.manual_review = threshold, manual_review

    @staticmethod
    def sanitise(text):
        return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()

    def read(self, raw_text, noise=0.0):
        plate = self.sanitise(raw_text)
        if not PLATE_RE.match(plate):
            raise ParkingError(f"Invalid number plate '{plate or raw_text}': expected a format "
                               f"such as KA01AB1234. Manual review needed.")
        noise = min(max(float(noise or 0), 0.0), 1.0)
        return plate, round(1.0 - noise, 2)


class DemandPredictor:
    """Exponential moving average of occupancy -> price multiplier."""

    def __init__(self, alpha=0.5):
        self.alpha, self.ema, self.n = alpha, 0.0, 0

    def update(self, occupancy):
        self.ema = occupancy if self.n == 0 else self.alpha * occupancy + (1 - self.alpha) * self.ema
        self.n += 1
        return self.ema

    def multiplier(self):
        if self.ema < 0.5:
            return 1.0
        return 1.25 if self.ema < 0.8 else 1.5


class Ticket:
    def __init__(self, tid, plate, kind, slot_id, entry_time, multiplier, user_id, confidence):
        self.tid, self.plate, self.kind, self.slot_id = tid, plate, kind, slot_id
        self.entry_time, self.multiplier, self.user_id = entry_time, multiplier, user_id
        self.confidence, self.status = confidence, "ACTIVE"

    def to_dict(self):
        return {"ticket_id": self.tid, "plate": self.plate, "kind": self.kind,
                "slot": self.slot_id, "entry_time": fmt_time(self.entry_time),
                "multiplier": self.multiplier, "confidence": self.confidence}


class ParkingLot:
    def __init__(self, lot_id, name, area, layout, rates=None, hold_minutes=15):
        self.lot_id, self.name, self.area = lot_id, name, area
        self.grid = [line.split() for line in layout.strip().splitlines()]
        self.rows, self.cols = len(self.grid), max(len(r) for r in self.grid)
        self.rates = dict(DEFAULT_RATES)
        self.rates.update(rates or {})
        self.grace, self.hold_minutes, self.override = GRACE_MINUTES, hold_minutes, None
        self.recognizer, self.predictor = PlateRecognizer(), DemandPredictor()
        self.slots, self.tickets, self.inside = {}, {}, {}
        self.reservations, self.review_queue = {}, []
        self._ticket_no = self._review_no = 0
        self._lock = threading.RLock()
        letters, self.gate, n = {"C": "CAR", "B": "BIKE", "E": "EV"}, None, 0
        for r, row in enumerate(self.grid):
            for c, tok in enumerate(row):
                if tok == "G":
                    self.gate = (r, c)
                elif tok in letters:
                    n += 1
                    sid = f"S{n:02d}"
                    self.slots[sid] = Slot(sid, letters[tok], r, c)
        if self.gate is None:
            raise ValueError("Layout needs a gate 'G'")
        self._bfs()

    # ---------- layout / breadth-first search ----------
    def _walkable(self, r, c):
        return 0 <= r < self.rows and 0 <= c < len(self.grid[r]) and self.grid[r][c] in (".", "G")

    def _bfs(self):
        dist, parent, q = {self.gate: 0}, {self.gate: None}, deque([self.gate])
        while q:
            r, c = q.popleft()
            for dr, dc in DIRS.values():
                nxt = (r + dr, c + dc)
                if nxt not in dist and self._walkable(*nxt):
                    dist[nxt], parent[nxt] = dist[(r, c)] + 1, (r, c)
                    q.append(nxt)
        self._parent, self.slot_dist, self.slot_access = parent, {}, {}
        for s in self.slots.values():
            near = [(s.row + dr, s.col + dc) for dr, dc in DIRS.values()]
            near = [n for n in near if n in dist]
            if near:
                best = min(near, key=lambda n: dist[n])
                self.slot_access[s.sid], self.slot_dist[s.sid] = best, dist[best] + 1

    def nearest_free(self, kind):
        cands = [s for s in self.slots.values()
                 if s.kind == kind and s.state == FREE and not s.flagged and s.sid in self.slot_access]
        if not cands:
            raise ParkingError(f"Lot is full for {kind} bays")
        return min(cands, key=lambda s: (self.slot_dist[s.sid], s.sid)).sid

    def path_to(self, sid):
        slot = self._slot(sid)
        cur = self.slot_access.get(sid)
        if cur is None:
            raise ParkingError(f"No route to slot {sid}")
        cells = []
        while cur is not None:
            cells.append(cur)
            cur = self._parent[cur]
        cells.reverse()
        cells.append((slot.row, slot.col))
        return {"slot": sid, "cells": [list(c) for c in cells], "distance": len(cells) - 1,
                "directions": self._directions(cells, sid)}

    @staticmethod
    def _directions(cells, sid):
        runs = []
        for a, b in zip(cells, cells[1:]):
            d = DIR_OF[(b[0] - a[0], b[1] - a[1])]
            if runs and runs[-1][0] == d:
                runs[-1][1] += 1
            else:
                runs.append([d, 1])
        order, heading, steps = "URDL", "R", []
        for i, (d, n) in enumerate(runs):
            turn = (order.index(d) - order.index(heading)) % 4
            verb = ["Go straight", "Turn right", "Make a U-turn", "Turn left"][turn]
            if i == 0:
                verb = "From the gate, " + verb[0].lower() + verb[1:]
            steps.append(f"{verb} for {n} block{'s' if n > 1 else ''}")
            heading = d
        steps.append(f"Park in slot {sid}")
        return steps

    # ---------- helpers ----------
    def _slot(self, sid):
        if sid not in self.slots:
            raise ParkingError(f"Unknown slot '{sid}'")
        return self.slots[sid]

    @staticmethod
    def _check_kind(kind):
        kind = (kind or "").strip().upper()
        if kind not in KINDS:
            raise ParkingError(f"Unknown vehicle type '{kind}': choose CAR, BIKE or EV")
        return kind

    def _valid_plate(self, raw):
        plate = self.recognizer.sanitise(raw)
        if not PLATE_RE.match(plate):
            raise ParkingError(f"Invalid number plate '{plate or raw}': expected a format such as KA01AB1234")
        return plate

    def occupancy(self):
        usable = [s for s in self.slots.values() if s.state != OUT_OF_SERVICE]
        if not usable:
            return 1.0
        return sum(1 for s in usable if s.state == OCCUPIED or s.flagged) / len(usable)

    def current_multiplier(self):
        return self.override if self.override else self.predictor.multiplier()

    def _sample(self):
        self.predictor.update(self.occupancy())

    def _owner(self, slot):
        if slot.state == OCCUPIED and slot.plate in self.inside:
            return self.tickets[self.inside[slot.plate]].user_id
        if slot.state == RESERVED and slot.plate in self.reservations:
            return self.reservations[slot.plate]["user_id"]
        return None

    # ---------- fees ----------
    def compute_fee(self, kind, minutes, multiplier=1.0):
        if minutes <= self.grace:
            return 0.0
        return round(math.ceil(minutes / 60) * self.rates[kind] * multiplier, 2)

    def quote(self, ticket_id, now=None, minutes=None):
        t = self.tickets.get((ticket_id or "").strip().upper())
        if t is None:
            raise ParkingError(f"Invalid ticket '{ticket_id}'")
        if t.status != "ACTIVE":
            raise ParkingError(f"Ticket {t.tid} has already been used")
        if minutes is None:
            minutes = ((now or datetime.now()) - t.entry_time).total_seconds() / 60
        return t, round(minutes, 1), self.compute_fee(t.kind, minutes, t.multiplier)

    # ---------- entry / exit ----------
    def enter(self, raw_plate, kind, noise=0.0, manual_ok=False, user_id=None, now=None):
        now = now or datetime.now()
        kind = self._check_kind(kind)
        plate, conf = self.recognizer.read(raw_plate, noise)
        if conf < self.recognizer.threshold:
            if not self.recognizer.manual_review:
                raise ParkingError(f"Plate unreadable (confidence {conf:.2f}) and manual review is disabled")
            if not manual_ok:
                raise ReviewRequired(plate, conf, self.recognizer.threshold)
        with self._lock:
            self.release_expired(now)
            if plate in self.inside:
                raise ParkingError(f"{plate} is already inside the lot")
            res = self.reservations.get(plate)
            if res:
                if res["kind"] != kind:
                    raise ParkingError(f"The reservation for {plate} is for a {res['kind']} bay, not {kind}")
                slot = self.slots[res["slot_id"]]
                del self.reservations[plate]
            else:
                slot = self.slots[self.nearest_free(kind)]
            mult = self.current_multiplier()
            slot.transition(OCCUPIED)
            slot.plate = plate
            self._ticket_no += 1
            t = Ticket(f"T{self._ticket_no:04d}", plate, kind, slot.sid, now, mult, user_id, conf)
            self.tickets[t.tid], self.inside[plate] = t, t.tid
            self._sample()
            return t

    def exit_vehicle(self, ticket_id, paid, now=None, minutes=None):
        now = now or datetime.now()
        with self._lock:
            t, mins, fee = self.quote(ticket_id, now, minutes)
            try:
                paid = round(float(paid), 2)
            except (TypeError, ValueError):
                raise ParkingError("Enter the amount paid as a number")
            if paid < fee:
                raise ParkingError(f"Insufficient payment: due Rs {fee:.2f}, paid Rs {paid:.2f}")
            self.slots[t.slot_id].transition(FREE)
            t.status = "CLOSED"
            self.inside.pop(t.plate, None)
            self._sample()
            return {"ticket_id": t.tid, "plate": t.plate, "kind": t.kind, "slot": t.slot_id,
                    "entry_time": fmt_time(t.entry_time), "exit_time": fmt_time(now),
                    "minutes": mins, "multiplier": t.multiplier, "fee": fee,
                    "paid": paid, "change": round(paid - fee, 2),
                    "entry_dt": t.entry_time, "exit_dt": now, "user_id": t.user_id}

    # ---------- reservations ----------
    def reserve(self, raw_plate, kind, user_id=None, now=None):
        now = now or datetime.now()
        kind, plate = self._check_kind(kind), self._valid_plate(raw_plate)
        with self._lock:
            self.release_expired(now)
            if plate in self.inside:
                raise ParkingError(f"{plate} is already inside the lot")
            if plate in self.reservations:
                raise ParkingError(f"{plate} already has a reservation (slot {self.reservations[plate]['slot_id']})")
            slot = self.slots[self.nearest_free(kind)]
            slot.transition(RESERVED)
            slot.plate = plate
            res = {"plate": plate, "slot_id": slot.sid, "kind": kind, "user_id": user_id,
                   "expires": now + timedelta(minutes=self.hold_minutes), "alerted": False}
            self.reservations[plate] = res
            return res

    def cancel_reservation(self, raw_plate):
        plate = self.recognizer.sanitise(raw_plate)
        with self._lock:
            res = self.reservations.pop(plate, None)
            if res is None:
                raise ParkingError(f"No reservation found for {plate}")
            self.slots[res["slot_id"]].transition(FREE)
            return res

    def release_expired(self, now=None):
        now, out = now or datetime.now(), []
        with self._lock:
            for plate, r in list(self.reservations.items()):
                if r["expires"] <= now:
                    slot = self.slots[r["slot_id"]]
                    if slot.state == RESERVED:
                        slot.transition(FREE)
                    del self.reservations[plate]
                    out.append(r)
        return out

    def expiring_soon(self, within=5, now=None):
        now, out = now or datetime.now(), []
        with self._lock:
            for r in self.reservations.values():
                left = (r["expires"] - now).total_seconds() / 60
                if 0 < left <= within and not r["alerted"]:
                    r["alerted"] = True
                    out.append({**r, "minutes_left": max(1, round(left))})
        return out

    # ---------- attendant / admin actions ----------
    def set_out_of_service(self, sid):
        with self._lock:
            slot = self._slot(sid)
            if slot.state == OCCUPIED:
                raise ParkingError(f"Slot {sid} is occupied; it cannot be taken out of service yet")
            if slot.state == RESERVED:
                self.reservations.pop(slot.plate, None)
            slot.transition(OUT_OF_SERVICE)

    def restore(self, sid):
        with self._lock:
            self._slot(sid).transition(FREE)

    def flag_unticketed(self, sid):
        """Sensor reports a vehicle in a slot that has no ticket."""
        with self._lock:
            slot = self._slot(sid)
            if slot.state != FREE:
                raise ParkingError(f"Slot {sid} is not free, so the sensor cannot report a stray vehicle")
            slot.flagged = True

    def clear_flag(self, sid):
        with self._lock:
            self._slot(sid).flagged = False

    def set_pricing(self, rates=None, grace=None, override=None):
        with self._lock:
            for k, v in (rates or {}).items():
                v = float(v)
                if k not in KINDS or v <= 0:
                    raise ParkingError("Rates must be positive numbers for CAR, BIKE and EV")
                self.rates[k] = v
            if grace is not None:
                grace = float(grace)
                if grace < 0:
                    raise ParkingError("Grace period cannot be negative")
                self.grace = grace
            if override is not None:
                self.override = None if override in ("", "auto", 0) else float(override)

    # ---------- manual review queue ----------
    def queue_review(self, plate, kind, confidence, user_id):
        with self._lock:
            for item in self.review_queue:
                if item["plate"] == plate:
                    return item
            self._review_no += 1
            item = {"id": self._review_no, "plate": plate, "kind": kind,
                    "confidence": confidence, "user_id": user_id}
            self.review_queue.append(item)
            return item

    def get_review(self, rid):
        for item in self.review_queue:
            if item["id"] == rid:
                return item
        raise ParkingError("That review request no longer exists")

    def drop_review(self, rid):
        self.review_queue = [i for i in self.review_queue if i["id"] != rid]

    # ---------- reporting ----------
    def summary(self):
        with self._lock:
            kinds = {k: {"free": 0, "total": 0} for k in KINDS}
            c = {"free": 0, "occupied": 0, "reserved": 0, "unavailable": 0}
            for s in self.slots.values():
                kinds[s.kind]["total"] += 1
                if s.state == FREE and not s.flagged:
                    c["free"] += 1
                    kinds[s.kind]["free"] += 1
                elif s.state == RESERVED:
                    c["reserved"] += 1
                elif s.state == OCCUPIED or s.flagged:
                    c["occupied"] += 1
                else:
                    c["unavailable"] += 1
            m = self.current_multiplier()
            return {**c, "total": len(self.slots), "kinds": kinds, "multiplier": m,
                    "level": {1.0: "Normal", 1.25: "Busy", 1.5: "Peak"}.get(m, "Custom"),
                    "occupancy": round(self.occupancy(), 3), "ema": round(self.predictor.ema, 3),
                    "auto": self.override is None}

    def status(self, viewer_id=None, is_admin=False, now=None):
        now = now or datetime.now()
        with self._lock:
            slots = []
            for s in self.slots.values():
                owner = self._owner(s)
                mine = viewer_id is not None and owner == viewer_id
                slots.append({"id": s.sid, "kind": s.kind, "row": s.row, "col": s.col,
                              "state": s.state, "flagged": s.flagged, "mine": mine,
                              "plate": s.plate if (is_admin or mine) and not s.flagged else None})
            mine_items = []
            if viewer_id is not None:
                for r in self.reservations.values():
                    if r["user_id"] == viewer_id:
                        left = max(0, (r["expires"] - now).total_seconds() / 60)
                        mine_items.append({"type": "reservation", "plate": r["plate"], "slot": r["slot_id"],
                                           "kind": r["kind"], "minutes_left": round(left, 1)})
                for t in self.tickets.values():
                    if t.user_id == viewer_id and t.status == "ACTIVE":
                        mine_items.append({"type": "ticket", "plate": t.plate, "slot": t.slot_id,
                                           "kind": t.kind, "ticket_id": t.tid})
            return {"id": self.lot_id, "name": self.name, "area": self.area,
                    "rows": self.rows, "cols": self.cols, "grid": self.grid, "slots": slots,
                    "summary": self.summary(), "rates": self.rates, "grace": self.grace,
                    "hold_minutes": self.hold_minutes, "mine": mine_items}
