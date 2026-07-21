import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch

import requests


def load_weather_module():
    constants = types.ModuleType("constants")
    constants.WEATHER_API_KEY = "test-key"
    with patch.dict(sys.modules, {"constants": constants}):
        spec = importlib.util.spec_from_file_location("weather_under_test", "weather.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class WeatherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.weather = load_weather_module()

    def test_timeout_returns_fallback(self):
        with patch.object(self.weather.requests, "request", side_effect=requests.Timeout("timeout")) as request:
            result = self.weather.get_weather()

        self.assertEqual(result, "Погода временно недоступна")
        self.assertEqual(request.call_args.kwargs["timeout"], 10)

    def test_successful_response_is_formatted(self):
        response_en = Mock()
        response_en.json.return_value = {
            "main": {"temp": 20.4, "feels_like": 19.6},
            "weather": [{"description": "clear sky"}],
        }
        response_ru = Mock()
        response_ru.json.return_value = {"weather": [{"description": "ясно"}]}

        with patch.object(self.weather.requests, "request", side_effect=[response_en, response_ru]) as request:
            result = self.weather.get_weather()

        self.assertEqual(result, "Температура 20°C, ощущается как: 20°C\nЯсно ☀️")
        self.assertTrue(all(call.kwargs["timeout"] == 10 for call in request.call_args_list))


if __name__ == "__main__":
    unittest.main()
