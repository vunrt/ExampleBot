import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from db import Database

NOW = datetime(2026, 10, 22, 12, 0)          # четверг
START = datetime(2026, 10, 26, 10, 0)        # понедельник, 10:00


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Database(Path(tmp.name) / "test.db")
        self.db.init_schema()
        self.db.seed_services([("Маникюр", 60, 1500), ("Гель-лак", 90, 2500)])
        self.manicure, self.gel = self.db.list_services()

    def book(self, start=START, service=None, client_id=1, name="Анна", now=NOW, reminder_hours=24):
        return self.db.create_appointment(
            client_id=client_id,
            name=name,
            phone="+79123456789",
            username="anna",
            service=service or self.manicure,
            start=start,
            now=now,
            reminder_hours=reminder_hours,
        )


class ServicesTests(DatabaseTestCase):
    def test_seed_runs_only_once(self):
        self.db.seed_services([("Другая", 30, 100)])
        self.assertEqual(len(self.db.list_services()), 2)

    def test_deactivated_service_is_hidden_but_still_readable(self):
        self.assertTrue(self.db.deactivate_service(self.manicure.id))
        self.assertFalse(self.db.deactivate_service(self.manicure.id))  # повторно — уже скрыта
        self.assertEqual([s.name for s in self.db.list_services()], ["Гель-лак"])
        self.assertFalse(self.db.get_service(self.manicure.id).is_active)  # история не ломается

    def test_add_service(self):
        service_id = self.db.add_service("Педикюр", 90, 3000)
        service = self.db.get_service(service_id)
        self.assertEqual((service.name, service.duration_min, service.price), ("Педикюр", 90, 3000))


class AppointmentTests(DatabaseTestCase):
    def test_create_and_read_back(self):
        appointment_id = self.book()
        a = self.db.get_appointment(appointment_id)
        self.assertEqual(a.start, START)
        self.assertEqual(a.end, START + timedelta(hours=1))
        self.assertEqual((a.client_name, a.service_name, a.price, a.status), ("Анна", "Маникюр", 1500, "booked"))

    def test_overlapping_booking_is_rejected(self):
        self.assertIsNotNone(self.book())
        self.assertIsNone(self.book(start=START + timedelta(minutes=30), client_id=2))
        self.assertIsNone(self.book(start=START - timedelta(minutes=30), client_id=3))
        self.assertIsNone(self.book(start=START, service=self.gel, client_id=4))

    def test_adjacent_bookings_are_allowed(self):
        self.assertIsNotNone(self.book())
        self.assertIsNotNone(self.book(start=START + timedelta(hours=1), client_id=2))
        self.assertIsNotNone(self.book(start=START - timedelta(hours=1), client_id=3))

    def test_cancel_frees_the_slot(self):
        appointment_id = self.book()
        self.assertTrue(self.db.cancel_appointment(appointment_id, "client", NOW))
        self.assertFalse(self.db.cancel_appointment(appointment_id, "client", NOW))  # уже отменена
        self.assertEqual(self.db.get_appointment(appointment_id).status, "cancelled")
        self.assertIsNotNone(self.book(client_id=2))

    def test_booking_on_closed_day_is_rejected(self):
        self.assertTrue(self.db.toggle_day_off(START.date()))
        self.assertIsNone(self.book())
        self.assertFalse(self.db.toggle_day_off(START.date()))  # снова открыли
        self.assertIsNotNone(self.book())

    def test_booking_remembers_client_contacts(self):
        self.book(client_id=7, name="Анна")
        self.book(start=START + timedelta(days=1), client_id=7, name="Мария")
        client = self.db.get_client(7)
        self.assertEqual(client.name, "Мария")  # последние введённые данные
        self.assertEqual(client.phone, "+79123456789")
        self.assertIsNone(self.db.get_client(999))

    def test_upcoming_list_excludes_cancelled_and_past_and_is_sorted(self):
        later = self.book(start=START + timedelta(days=2))
        sooner = self.book(start=START)
        cancelled = self.book(start=START + timedelta(days=1))
        self.db.cancel_appointment(cancelled, "client", NOW)
        past = self.book(start=datetime(2026, 10, 20, 10, 0))  # уже прошла
        self.assertIsNotNone(past)

        upcoming = self.db.get_client_upcoming(1, NOW)
        self.assertEqual([a.id for a in upcoming], [sooner, later])
        self.assertEqual(self.db.count_client_upcoming(1, NOW), 2)

    def test_day_view_and_busy_intervals(self):
        self.book()
        self.book(start=START + timedelta(days=1), client_id=2)
        self.assertEqual(len(self.db.get_day_appointments(START.date())), 1)
        busy = self.db.get_busy_intervals(START.date(), START.date())
        self.assertEqual(busy, [(START, START + timedelta(hours=1))])
        both = self.db.get_busy_intervals(START.date(), START.date() + timedelta(days=1))
        self.assertEqual(len(both), 2)

    def test_days_off_range_query(self):
        self.db.toggle_day_off(date(2026, 10, 27))
        self.db.toggle_day_off(date(2026, 11, 20))
        self.assertEqual(
            self.db.get_days_off(date(2026, 10, 22), date(2026, 11, 5)), {date(2026, 10, 27)}
        )
        self.assertTrue(self.db.is_day_off(date(2026, 10, 27)))
        self.assertFalse(self.db.is_day_off(date(2026, 10, 28)))


class ReminderTests(DatabaseTestCase):
    def test_far_booking_gets_a_reminder_later(self):
        appointment_id = self.book(start=START)  # до визита ~4 дня
        self.assertFalse(self.db.get_appointment(appointment_id).reminder_sent)

        # Сейчас рано — напоминание не нужно.
        self.assertEqual(self.db.get_due_reminders(NOW, 24), [])

        # За 23 часа до визита — пора.
        almost = START - timedelta(hours=23)
        due = self.db.get_due_reminders(almost, 24)
        self.assertEqual([a.id for a in due], [appointment_id])

        self.db.mark_reminded(appointment_id)
        self.assertEqual(self.db.get_due_reminders(almost, 24), [])

    def test_last_minute_booking_gets_no_reminder(self):
        start = NOW + timedelta(hours=5)
        appointment_id = self.book(start=start)
        self.assertTrue(self.db.get_appointment(appointment_id).reminder_sent)
        self.assertEqual(self.db.get_due_reminders(NOW, 24), [])

    def test_cancelled_and_started_appointments_are_not_reminded(self):
        cancelled = self.book(start=START)
        self.db.cancel_appointment(cancelled, "client", NOW)
        started = self.book(start=START + timedelta(days=1), client_id=2)

        self.assertEqual(self.db.get_due_reminders(START - timedelta(hours=1), 24), [])
        # Визит уже начался — напоминать поздно.
        self.assertEqual(self.db.get_due_reminders(START + timedelta(days=1, minutes=5), 24), [])
        self.assertIsNotNone(started)


class ConcurrencyTests(DatabaseTestCase):
    def test_only_one_of_many_simultaneous_bookings_wins(self):
        """Восемь клиентов одновременно жмут «Подтвердить» на одно и то же время."""
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.book(client_id=100 + i), range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.db.get_day_appointments(START.date())), 1)


if __name__ == "__main__":
    unittest.main()
