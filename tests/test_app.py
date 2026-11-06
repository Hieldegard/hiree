import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hiree import app

class TestVacancies(unittest.TestCase):
    def test_safe_urls(self):
        self.assertEqual(app.vacancy_id('https://hh.ru/vacancy/123?x=1'), '123')
        for url in ['https://hh.ru.evil.test/vacancy/123','https://user@hh.ru/vacancy/123','http://hh.ru/vacancy/123','https://hh.ru/vacancy/abc']:
            self.assertIsNone(app.vacancy_id(url))
    def test_dedup_and_persist(self):
        with tempfile.TemporaryDirectory() as d, patch.object(app, 'DATA', Path(d)):
            with app.db() as con:
                self.assertTrue(app.save(con,'https://hh.ru/vacancy/123','Роль','Описание'))
                self.assertFalse(app.save(con,'https://hh.ru/vacancy/123?from=search','Роль','Описание'))
            with app.db() as con:
                self.assertEqual(con.execute('select count(*) from vacancies').fetchone()[0], 1)

if __name__ == '__main__': unittest.main()
