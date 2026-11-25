"""18 automated test cases (TC01-TC18). Run:  python -m unittest -v"""
import unittest
from datetime import datetime, timedelta
from parking import (ParkingLot, ParkingError, ReviewRequired, Slot,
                     FREE, RESERVED, OCCUPIED, OUT_OF_SERVICE)

LAYOUT = """
G . . . .
. C C B .
. C C B .
. . . . .
. E E . .
"""
T0 = datetime(2026, 11, 25, 10, 0)


def fresh():
    return ParkingLot(1, "Test Lot", "Test", LAYOUT)


class ParkingTests(unittest.TestCase):
    def test_tc01_valid_car_entry_nearest_slot(self):
        lot = fresh(); t = lot.enter("KA01AB1234", "CAR", now=T0)
        self.assertEqual(t.slot_id, "S01"); self.assertEqual(lot.slots["S01"].state, OCCUPIED)

    def test_tc02_second_car_gets_next_nearest(self):
        lot = fresh(); lot.enter("KA01AB1234", "CAR", now=T0)
        self.assertEqual(lot.enter("KA02CD5678", "CAR", now=T0).slot_id, "S02")

    def test_tc03_ev_gets_ev_bay(self):
        lot = fresh(); t = lot.enter("KA03EF1111", "EV", now=T0)
        self.assertEqual(lot.slots[t.slot_id].kind, "EV")

    def test_tc04_lot_full(self):
        lot = fresh()
        for p in ["KA01AA0001", "KA01AA0002", "KA01AA0003", "KA01AA0004"]:
            lot.enter(p, "CAR", now=T0)
        with self.assertRaises(ParkingError):
            lot.enter("KA01AA0005", "CAR", now=T0)

    def test_tc05_invalid_plate(self):
        with self.assertRaisesRegex(ParkingError, "Manual review"):
            fresh().enter("1234", "CAR", now=T0)

    def test_tc06_low_confidence_plate(self):
        lot = fresh()
        with self.assertRaises(ReviewRequired):
            lot.enter("KA01AB1234", "CAR", noise=0.7, now=T0)
        self.assertEqual(lot.enter("KA01AB1234", "CAR", noise=0.7, manual_ok=True, now=T0).slot_id, "S01")
        lot2 = fresh(); lot2.recognizer.manual_review = False
        with self.assertRaises(ParkingError):
            lot2.enter("KA01AB1234", "CAR", noise=0.7, manual_ok=True, now=T0)

    def test_tc07_duplicate_entry(self):
        lot = fresh(); lot.enter("KA01AB1234", "CAR", now=T0)
        with self.assertRaisesRegex(ParkingError, "already inside"):
            lot.enter("KA01AB1234", "CAR", now=T0)

    def test_tc08_fee_inside_grace(self):
        self.assertEqual(fresh().compute_fee("CAR", 8), 0.0)

    def test_tc09_fee_100_minutes(self):
        self.assertEqual(fresh().compute_fee("CAR", 100, 1.0), 60.0)

    def test_tc10_exit_with_change(self):
        lot = fresh(); t = lot.enter("KA01AB1234", "CAR", now=T0)
        r = lot.exit_vehicle(t.tid, 100, now=T0 + timedelta(minutes=100))
        self.assertEqual((r["fee"], r["change"]), (60.0, 40.0)); self.assertEqual(lot.slots["S01"].state, FREE)

    def test_tc11_insufficient_payment(self):
        lot = fresh(); t = lot.enter("KA01AB1234", "CAR", now=T0)
        with self.assertRaisesRegex(ParkingError, "Insufficient"):
            lot.exit_vehicle(t.tid, 10, now=T0 + timedelta(minutes=100))

    def test_tc12_invalid_ticket(self):
        with self.assertRaisesRegex(ParkingError, "Invalid ticket"):
            fresh().exit_vehicle("T9999", 100, now=T0)

    def test_tc13_ticket_reuse(self):
        lot = fresh(); t = lot.enter("KA01AB1234", "CAR", now=T0)
        lot.exit_vehicle(t.tid, 100, now=T0 + timedelta(minutes=100))
        with self.assertRaisesRegex(ParkingError, "already been used"):
            lot.exit_vehicle(t.tid, 100, now=T0 + timedelta(minutes=101))

    def test_tc14_dynamic_price_high_demand(self):
        lot = fresh()
        for p, k in [("KA01AA0001", "CAR"), ("KA01AA0002", "CAR"), ("KA01AA0003", "CAR"),
                     ("KA01AA0004", "CAR"), ("KA01AA0005", "BIKE")]:
            lot.enter(p, k, now=T0)
        self.assertGreaterEqual(lot.predictor.ema, 0.5)
        self.assertEqual(lot.current_multiplier(), 1.25)
        self.assertEqual(lot.compute_fee("CAR", 100, lot.current_multiplier()), 75.0)

    def test_tc15_reservation_blocks_slot(self):
        lot = fresh(); lot.reserve("KA09ZZ9999", "CAR", now=T0)
        self.assertEqual(lot.enter("KA01AB1234", "CAR", now=T0).slot_id, "S02")
        self.assertEqual(lot.slots["S01"].state, RESERVED)

    def test_tc16_out_of_service_slot_skipped(self):
        lot = fresh(); lot.set_out_of_service("S01")
        self.assertEqual(lot.enter("KA01AB1234", "CAR", now=T0).slot_id, "S02")

    def test_tc17_illegal_state_transition(self):
        s = Slot("S01", "CAR", 1, 1); s.transition(OCCUPIED)
        with self.assertRaises(ParkingError):
            s.transition(RESERVED)

    def test_tc18_unknown_vehicle_type(self):
        with self.assertRaisesRegex(ParkingError, "Unknown vehicle type"):
            fresh().enter("KA01AB1234", "TRUCK", now=T0)

    # extra checks used by the website
    def test_navigation_path(self):
        p = fresh().path_to("S05")
        self.assertEqual(p["cells"][0], [0, 0]); self.assertEqual(p["cells"][-1], [2, 2])
        self.assertTrue(p["directions"][-1].startswith("Park in slot"))

    def test_reservation_expiry_frees_slot(self):
        lot = fresh(); lot.reserve("KA09ZZ9999", "CAR", now=T0)
        self.assertEqual(len(lot.release_expired(T0 + timedelta(minutes=16))), 1)
        self.assertEqual(lot.slots["S01"].state, FREE)


if __name__ == "__main__":
    unittest.main()
