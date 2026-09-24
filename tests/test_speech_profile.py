"""Speech-rate preference persists without changing older profiles."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from llm import AppError
from service import Service


class SpeechProfileTests(unittest.TestCase):
    def test_rate_defaults_persists_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Service(folder)
            try:
                profile = app.get('profile')
                self.assertEqual(profile['speech_rate'], 1.0)
                profile.pop('speech_rate')
                with app.db:
                    app.set('profile', profile)
                values = dict(name='学习者', minutes=20, goal='日常交流')
                self.assertEqual(app.profile(values)['speech_rate'], 1.0)
                self.assertEqual(app.profile(dict(values, speech_rate=1.5))['speech_rate'], 1.5)
                for invalid in (0.49, 2.01, True, '1.5', float('nan')):
                    with self.subTest(invalid=invalid), self.assertRaises(AppError):
                        app.profile(dict(values, speech_rate=invalid))
                    self.assertEqual(app.get('profile')['speech_rate'], 1.5)
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
