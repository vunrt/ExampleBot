import unittest
from datetime import date, datetime, time

from slots import free_slots, overlaps

DAY = date(2026, 11, 2)          # понедельник
HOURS = (time(10, 0), time(13, 0))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 2, hour, minute)


def hhmm(slots: list[datetime]) -> list[str]:
    return [f"{s:%H:%M}" for s in slots]


class OverlapsTests(unittest.TestCase):
    def test_touching_intervals_do_not_overlap(self):
        self.assertFalse(overlaps(at(10), at(11), at(11), at(12)))
        self.assertFalse(overlaps(at(11), at(12), at(10), at(11)))

    def test_partial_overlap(self):
        self.assertTrue(overlaps(at(10), at(11), at(10, 30), at(12)))

    def test_one_inside_another(self):
        self.assertTrue(overlaps(at(10), at(13), at(11), at(12)))
        self.assertTrue(overlaps(at(11), at(12), at(10), at(13)))


class FreeSlotsTests(unittest.TestCase):
    def test_full_grid_for_one_hour_service(self):
        self.assertEqual(
            hhmm(free_slots(DAY, 60, HOURS)),
            ["10:00", "10:30", "11:00", "11:30", "12:00"],
        )

    def test_service_must_finish_before_closing(self):
        slots = hhmm(free_slots(DAY, 90, HOURS))
        self.assertEqual(slots[-1], "11:30")  # 11:30 + 1,5 ч = 13:00 — ровно закрытие
        self.assertNotIn("12:00", slots)

    def test_odd_duration_still_uses_half_hour_grid(self):
        slots = hhmm(free_slots(DAY, 45, HOURS))
        self.assertEqual(slots, ["10:00", "10:30", "11:00", "11:30", "12:00"])

    def test_day_off_has_no_slots(self):
        self.assertEqual(free_slots(DAY, 60, None), [])

    def test_service_longer_than_work_day(self):
        self.assertEqual(free_slots(DAY, 240, HOURS), [])

    def test_zero_duration_is_rejected(self):
        self.assertEqual(free_slots(DAY, 0, HOURS), [])

    def test_busy_interval_blocks_overlapping_starts(self):
        busy = [(at(11), at(12))]
        # 10:00 заканчивается ровно в 11:00 — можно; 12:00 начинается, когда занятость кончилась — можно.
        self.assertEqual(hhmm(free_slots(DAY, 60, HOURS, busy=busy)), ["10:00", "12:00"])

    def test_several_busy_intervals(self):
        busy = [(at(10), at(11)), (at(12), at(13))]
        self.assertEqual(hhmm(free_slots(DAY, 60, HOURS, busy=busy)), ["11:00"])

    def test_breaks_work_like_busy_time(self):
        breaks = [(time(11, 0), time(12, 0))]
        self.assertEqual(hhmm(free_slots(DAY, 60, HOURS, breaks=breaks)), ["10:00", "12:00"])

    def test_minimum_lead_time(self):
        now = at(10, 10)  # раньше 11:10 записаться нельзя
        slots = free_slots(DAY, 60, HOURS, now=now, min_lead_min=60)
        self.assertEqual(hhmm(slots), ["11:30", "12:00"])

    def test_slot_exactly_at_lead_boundary_is_allowed(self):
        now = at(10, 0)
        slots = free_slots(DAY, 60, HOURS, now=now, min_lead_min=60)
        self.assertEqual(hhmm(slots)[0], "11:00")

    def test_past_day_slots_are_removed_when_now_is_later(self):
        now = datetime(2026, 11, 3, 9, 0)  # «сейчас» — уже следующий день
        self.assertEqual(free_slots(DAY, 60, HOURS, now=now), [])

    def test_custom_step(self):
        slots = hhmm(free_slots(DAY, 60, (time(10, 0), time(12, 0)), step_min=15))
        self.assertEqual(slots, ["10:00", "10:15", "10:30", "10:45", "11:00"])

    def test_busy_from_other_days_is_ignored(self):
        other_day = [(datetime(2026, 11, 3, 10, 0), datetime(2026, 11, 3, 13, 0))]
        self.assertEqual(len(free_slots(DAY, 60, HOURS, busy=other_day)), 5)


if __name__ == "__main__":
    unittest.main()
