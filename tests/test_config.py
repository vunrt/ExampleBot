import os
import unittest
from unittest import mock

from config import load_settings

BASE_ENV = {"BOT_TOKEN": "123:abc", "ADMIN_IDS": "111, 222", "TIMEZONE": "Europe/Berlin"}


def load_with(env: dict):
    """Загружает настройки с подменённым окружением (реальный .env не перезаписывает уже заданные переменные)."""
    with mock.patch.dict(os.environ, env, clear=False):
        return load_settings()


class LoadSettingsTests(unittest.TestCase):
    def test_reads_values_from_environment(self):
        settings = load_with({**BASE_ENV, "PROXY_URL": "socks5://127.0.0.1:10808", "SALON_NAME": "Студия"})
        self.assertEqual(settings.token, "123:abc")
        self.assertEqual(settings.admin_ids, frozenset({111, 222}))
        self.assertEqual(str(settings.tz), "Europe/Berlin")
        self.assertEqual(settings.proxy, "socks5://127.0.0.1:10808")
        self.assertEqual(settings.salon_name, "Студия")
        self.assertTrue(settings.db_path.is_absolute())

    def test_proxy_is_optional(self):
        settings = load_with({**BASE_ENV, "PROXY_URL": ""})
        self.assertIsNone(settings.proxy)

    def test_missing_token_gives_clear_error(self):
        with self.assertRaises(SystemExit) as ctx:
            load_with({**BASE_ENV, "BOT_TOKEN": ""})
        self.assertIn("BOT_TOKEN", str(ctx.exception))

    def test_bad_admin_ids_give_clear_error(self):
        with self.assertRaises(SystemExit) as ctx:
            load_with({**BASE_ENV, "ADMIN_IDS": "мастер"})
        self.assertIn("ADMIN_IDS", str(ctx.exception))

    def test_unknown_timezone_gives_clear_error(self):
        with self.assertRaises(SystemExit) as ctx:
            load_with({**BASE_ENV, "TIMEZONE": "Mars/Olympus"})
        self.assertIn("часовой пояс", str(ctx.exception))

    def test_empty_admin_list_is_allowed(self):
        settings = load_with({**BASE_ENV, "ADMIN_IDS": ""})
        self.assertEqual(settings.admin_ids, frozenset())


if __name__ == "__main__":
    unittest.main()
