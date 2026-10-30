"""Слой работы с SQLite: услуги, клиенты, записи, выходные дни.

Каждая операция открывает короткое соединение — для небольшого бота это просто и надёжно.
Время хранится как локальное «настенное» время салона в формате 'YYYY-MM-DD HH:MM':
такие строки сравниваются и сортируются как обычный текст.

Главное место, где важна аккуратность, — create_appointment(): проверка занятости
и вставка записи выполняются в одной транзакции BEGIN IMMEDIATE, поэтому два клиента
не смогут занять одно и то же время, даже если нажмут «Подтвердить» одновременно.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from utils import from_db, to_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    duration_min INTEGER NOT NULL CHECK (duration_min > 0),
    price        INTEGER NOT NULL CHECK (price >= 0),
    is_active    INTEGER NOT NULL DEFAULT 1
);

-- Запоминаем последние имя и телефон клиента, чтобы не спрашивать их каждый раз.
CREATE TABLE IF NOT EXISTS clients (
    tg_id      INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL,
    username   TEXT,
    created_at TEXT NOT NULL
);

-- Имя и телефон дублируются в записи: клиент может записать, например, подругу.
CREATE TABLE IF NOT EXISTS appointments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    client_name     TEXT    NOT NULL,
    client_phone    TEXT    NOT NULL,
    client_username TEXT,
    service_id      INTEGER NOT NULL REFERENCES services (id),
    start_at        TEXT    NOT NULL,
    end_at          TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled')),
    reminder_sent   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,
    cancelled_at    TEXT,
    cancelled_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_appointments_start  ON appointments (start_at);
CREATE INDEX IF NOT EXISTS idx_appointments_client ON appointments (client_id, status);

-- Дни, закрытые мастером для записи (отпуск, болезнь, праздник).
CREATE TABLE IF NOT EXISTS days_off (
    day TEXT PRIMARY KEY
);
"""

_APPOINTMENT_SELECT = """
    SELECT a.id, a.client_id, a.client_name, a.client_phone, a.client_username,
           a.service_id, s.name AS service_name, s.price AS price,
           a.start_at, a.end_at, a.status, a.reminder_sent
    FROM appointments a
    JOIN services s ON s.id = a.service_id
"""


@dataclass(frozen=True)
class Service:
    id: int
    name: str
    duration_min: int
    price: int
    is_active: bool = True


@dataclass(frozen=True)
class Client:
    tg_id: int
    name: str
    phone: str
    username: str | None


@dataclass(frozen=True)
class Appointment:
    id: int
    client_id: int
    client_name: str
    client_phone: str
    client_username: str | None
    service_id: int
    service_name: str
    price: int
    start: datetime
    end: datetime
    status: str
    reminder_sent: bool


def _service(row: sqlite3.Row) -> Service:
    return Service(row["id"], row["name"], row["duration_min"], row["price"], bool(row["is_active"]))


def _appointment(row: sqlite3.Row) -> Appointment:
    return Appointment(
        id=row["id"],
        client_id=row["client_id"],
        client_name=row["client_name"],
        client_phone=row["client_phone"],
        client_username=row["client_username"],
        service_id=row["service_id"],
        service_name=row["service_name"],
        price=row["price"],
        start=from_db(row["start_at"]),
        end=from_db(row["end_at"]),
        status=row["status"],
        reminder_sent=bool(row["reminder_sent"]),
    )


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    # ------------------------------------------------------------ соединения

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None — «автокоммит»: транзакциями управляем вручную (см. _transaction).
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE сразу берёт блокировку на запись: параллельные записи ждут своей очереди."""
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # ------------------------------------------------------------ инициализация

    def init_schema(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    def seed_services(self, items: Iterable[tuple[str, int, int]]) -> None:
        """Добавляет стартовые услуги, только если таблица услуг пока пустая."""
        with self._transaction() as conn:
            if conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO services (name, duration_min, price) VALUES (?, ?, ?)", list(items)
                )

    # ------------------------------------------------------------ услуги

    def list_services(self, active_only: bool = True) -> list[Service]:
        sql = "SELECT * FROM services"
        if active_only:
            sql += " WHERE is_active = 1"
        with self._connection() as conn:
            return [_service(r) for r in conn.execute(sql + " ORDER BY id")]

    def get_service(self, service_id: int) -> Service | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
        return _service(row) if row else None

    def add_service(self, name: str, duration_min: int, price: int) -> int:
        with self._transaction() as conn:
            cur = conn.execute(
                "INSERT INTO services (name, duration_min, price) VALUES (?, ?, ?)",
                (name, duration_min, price),
            )
            return int(cur.lastrowid)

    def deactivate_service(self, service_id: int) -> bool:
        """Услуги не удаляем, а скрываем — иначе сломается история старых записей."""
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE services SET is_active = 0 WHERE id = ? AND is_active = 1", (service_id,)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------ клиенты

    def get_client(self, tg_id: int) -> Client | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM clients WHERE tg_id = ?", (tg_id,)).fetchone()
        return Client(row["tg_id"], row["name"], row["phone"], row["username"]) if row else None

    # ------------------------------------------------------------ выходные дни

    def is_day_off(self, day: date) -> bool:
        with self._connection() as conn:
            return conn.execute("SELECT 1 FROM days_off WHERE day = ?", (day.isoformat(),)).fetchone() is not None

    def get_days_off(self, first: date, last: date) -> set[date]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT day FROM days_off WHERE day BETWEEN ? AND ?", (first.isoformat(), last.isoformat())
            )
            return {date.fromisoformat(r["day"]) for r in rows}

    def toggle_day_off(self, day: date) -> bool:
        """Закрывает день, если он открыт, и наоборот. Возвращает True, если день теперь закрыт."""
        key = day.isoformat()
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM days_off WHERE day = ?", (key,)).fetchone():
                conn.execute("DELETE FROM days_off WHERE day = ?", (key,))
                return False
            conn.execute("INSERT INTO days_off (day) VALUES (?)", (key,))
            return True

    # ------------------------------------------------------------ записи

    def create_appointment(
        self,
        *,
        client_id: int,
        name: str,
        phone: str,
        username: str | None,
        service: Service,
        start: datetime,
        now: datetime,
        reminder_hours: int,
    ) -> int | None:
        """Создаёт запись. Возвращает её номер или None, если время уже занято / день закрыт."""
        end = start + timedelta(minutes=service.duration_min)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM days_off WHERE day = ?", (start.date().isoformat(),)).fetchone():
                return None

            conflict = conn.execute(
                "SELECT 1 FROM appointments WHERE status = 'booked' AND start_at < ? AND end_at > ? LIMIT 1",
                (to_db(end), to_db(start)),
            ).fetchone()
            if conflict:
                return None

            conn.execute(
                """
                INSERT INTO clients (tg_id, name, phone, username, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (tg_id) DO UPDATE
                SET name = excluded.name, phone = excluded.phone, username = excluded.username
                """,
                (client_id, name, phone, username, to_db(now)),
            )

            # Если до визита осталось меньше, чем «окно напоминания», напоминать уже поздно —
            # иначе клиент получил бы «напоминание» сразу после записи.
            reminder_sent = 1 if start - now <= timedelta(hours=reminder_hours) else 0
            cur = conn.execute(
                """
                INSERT INTO appointments
                    (client_id, client_name, client_phone, client_username, service_id,
                     start_at, end_at, reminder_sent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (client_id, name, phone, username, service.id,
                 to_db(start), to_db(end), reminder_sent, to_db(now)),
            )
            return int(cur.lastrowid)

    def get_appointment(self, appointment_id: int) -> Appointment | None:
        with self._connection() as conn:
            row = conn.execute(_APPOINTMENT_SELECT + " WHERE a.id = ?", (appointment_id,)).fetchone()
        return _appointment(row) if row else None

    def get_client_upcoming(self, client_id: int, now: datetime) -> list[Appointment]:
        with self._connection() as conn:
            rows = conn.execute(
                _APPOINTMENT_SELECT
                + " WHERE a.client_id = ? AND a.status = 'booked' AND a.end_at > ? ORDER BY a.start_at",
                (client_id, to_db(now)),
            )
            return [_appointment(r) for r in rows]

    def count_client_upcoming(self, client_id: int, now: datetime) -> int:
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE client_id = ? AND status = 'booked' AND end_at > ?",
                (client_id, to_db(now)),
            ).fetchone()[0]

    def get_day_appointments(self, day: date) -> list[Appointment]:
        first = datetime.combine(day, datetime.min.time())
        with self._connection() as conn:
            rows = conn.execute(
                _APPOINTMENT_SELECT
                + " WHERE a.status = 'booked' AND a.start_at >= ? AND a.start_at < ? ORDER BY a.start_at",
                (to_db(first), to_db(first + timedelta(days=1))),
            )
            return [_appointment(r) for r in rows]

    def get_busy_intervals(self, first: date, last: date) -> list[tuple[datetime, datetime]]:
        """Занятые промежутки всех действующих записей за период [first, last] включительно."""
        range_start = datetime.combine(first, datetime.min.time())
        range_end = datetime.combine(last, datetime.min.time()) + timedelta(days=1)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT start_at, end_at FROM appointments WHERE status = 'booked' AND start_at < ? AND end_at > ?",
                (to_db(range_end), to_db(range_start)),
            )
            return [(from_db(r["start_at"]), from_db(r["end_at"])) for r in rows]

    def cancel_appointment(self, appointment_id: int, by: str, now: datetime) -> bool:
        """Отменяет действующую запись. by — 'client' или 'master'. False, если отменять нечего."""
        with self._transaction() as conn:
            cur = conn.execute(
                """
                UPDATE appointments
                SET status = 'cancelled', cancelled_at = ?, cancelled_by = ?
                WHERE id = ? AND status = 'booked'
                """,
                (to_db(now), by, appointment_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------ напоминания

    def get_due_reminders(self, now: datetime, hours: int) -> list[Appointment]:
        """Записи, о которых пора напомнить: до начала меньше `hours` часов, но визит ещё не начался."""
        limit = now + timedelta(hours=hours)
        with self._connection() as conn:
            rows = conn.execute(
                _APPOINTMENT_SELECT
                + " WHERE a.status = 'booked' AND a.reminder_sent = 0 AND a.start_at > ? AND a.start_at <= ?"
                + " ORDER BY a.start_at",
                (to_db(now), to_db(limit)),
            )
            return [_appointment(r) for r in rows]

    def mark_reminded(self, appointment_id: int) -> None:
        with self._transaction() as conn:
            conn.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appointment_id,))
