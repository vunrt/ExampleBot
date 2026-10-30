import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from booking import available_days, slots_for_day
from config import WORK_HOURS, Settings
from db import Database
from utils import (
    clean_name,
    esc,
    fmt_date,
    fmt_date_full,
    fmt_duration,
    fmt_price,
    fmt_when,
    format_schedule,
    normalize_phone,
)

MONDAY = date(2026, 10, 26)


def make_settings(**overrides) -> Settings:
    base = dict(
        token="test",
        admin_ids=frozenset(),
        tz=ZoneInfo("UTC"),
        db_path=Path("unused.db"),
        work_hours={
            0: (time(10, 0), time(13, 0)),
            1: (time(10, 0), time(13, 0)),
            2: (time(10, 0), time(13, 0)),
            3: (time(10, 0), time(13, 0)),
            4: (time(10, 0), time(13, 0)),
            5: None,
            6: None,
        },
        breaks=(),
        horizon_days=7,
    )
    base.update(overrides)
    return Settings(**base)


class BookingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Database(Path(tmp.name) / "test.db")
        self.db.init_schema()
        self.db.seed_services([("Маникюр", 60, 1500)])
        self.service = self.db.list_services()[0]
        self.long_service_id = self.db.add_service("Долгая", 180, 5000)
        self.settings = make_settings()
        self.now = datetime(2026, 10, 26, 9, 0)  # понедельник, 09:00

    def book(self, start, service):
        return self.db.create_appointment(
            client_id=1, name="Анна", phone="+79123456789", username=None,
            service=service, start=start, now=self.now, reminder_hours=24,
        )

    def test_weekends_are_skipped(self):
        days = available_days(self.db, self.settings, 60, self.now)
        self.assertEqual(
            sorted(days), [MONDAY + timedelta(days=i) for i in range(5)]  # пн–пт, без сб и вс
        )

    def test_closed_day_is_skipped(self):
        self.db.toggle_day_off(MONDAY + timedelta(days=2))  # среда
        days = available_days(self.db, self.settings, 60, self.now)
        self.assertNotIn(MONDAY + timedelta(days=2), days)
        self.assertEqual(len(days), 4)

    def test_fully_booked_day_is_skipped(self):
        long_service = self.db.get_service(self.long_service_id)
        tuesday = datetime.combine(MONDAY + timedelta(days=1), time(10, 0))
        self.assertIsNotNone(self.book(tuesday, long_service))  # 10:00–13:00 — весь день
        days = available_days(self.db, self.settings, 60, self.now)
        self.assertNotIn(tuesday.date(), days)

    def test_today_respects_minimum_lead_time(self):
        now = datetime(2026, 10, 26, 10, 10)  # раньше 11:10 записаться нельзя
        slots = slots_for_day(self.db, self.settings, 60, MONDAY, now)
        self.assertEqual([f"{s:%H:%M}" for s in slots], ["11:30", "12:00"])

    def test_booked_slot_disappears_from_the_list(self):
        monday_11 = datetime.combine(MONDAY, time(11, 0))
        self.assertIsNotNone(self.book(monday_11, self.service))
        slots = slots_for_day(self.db, self.settings, 60, MONDAY, self.now)
        # 10:00 заканчивается в 11:00, а 12:00 начинается ровно в конце занятой записи — оба свободны.
        self.assertEqual([f"{s:%H:%M}" for s in slots], ["10:00", "12:00"])

    def test_past_and_closed_days_have_no_slots(self):
        self.assertEqual(slots_for_day(self.db, self.settings, 60, MONDAY - timedelta(days=1), self.now), [])
        self.db.toggle_day_off(MONDAY)
        self.assertEqual(slots_for_day(self.db, self.settings, 60, MONDAY, self.now), [])

    def test_default_config_is_sane(self):
        for weekday, hours in WORK_HOURS.items():
            if hours is not None:
                self.assertLess(hours[0], hours[1], f"день {weekday}")


class UtilsTests(unittest.TestCase):
    def test_normalize_phone_accepts_common_formats(self):
        for raw in ["+7 (912) 345-67-89", "89123456789", "9123456789", "+79123456789", "7 912 345 67 89"]:
            self.assertEqual(normalize_phone(raw), "+79123456789", raw)
        self.assertEqual(normalize_phone("+49 151 2345 6789"), "+4915123456789")

    def test_normalize_phone_rejects_garbage(self):
        for raw in ["", "abc", "123", "позвоните мне 89123456789", "+7 912 345", "1" * 20]:
            self.assertIsNone(normalize_phone(raw), raw)

    def test_clean_name(self):
        self.assertEqual(clean_name("  Анна   Иванова "), "Анна Иванова")
        self.assertIsNone(clean_name("А"))
        self.assertIsNone(clean_name("   "))
        self.assertIsNone(clean_name("х" * 51))

    def test_formatting(self):
        self.assertEqual(fmt_duration(90), "1 ч 30 мин")
        self.assertEqual(fmt_duration(45), "45 мин")
        self.assertEqual(fmt_duration(120), "2 ч")
        self.assertEqual(fmt_price(1500), "1\u00a0500\u00a0₽")
        self.assertEqual(fmt_date(MONDAY), "Пн, 26 октября")
        self.assertEqual(fmt_date_full(MONDAY), "Понедельник, 26 октября")

    def test_fmt_when(self):
        now = datetime(2026, 10, 26, 9, 0)
        self.assertEqual(fmt_when(datetime(2026, 10, 26, 15, 0), now), "сегодня")
        self.assertEqual(fmt_when(datetime(2026, 10, 27, 15, 0), now), "завтра")
        self.assertEqual(fmt_when(datetime(2026, 10, 30, 15, 0), now), "Пт, 30 октября")

    def test_format_schedule_merges_equal_days(self):
        hours = {i: (time(10, 0), time(19, 0)) for i in range(5)}
        hours[5] = (time(10, 0), time(16, 0))
        hours[6] = None
        self.assertEqual(
            format_schedule(hours), "Пн–Пт: 10:00–19:00\nСб: 10:00–16:00\nВс: выходной"
        )

    def test_esc_protects_html(self):
        self.assertEqual(esc("<b>Анна & Ко</b>"), "&lt;b&gt;Анна &amp; Ко&lt;/b&gt;")


if __name__ == "__main__":
    unittest.main()
