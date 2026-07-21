import requests
import json
from constants import WEATHER_API_KEY

lat = 55.755864
lon = 37.617698
key = WEATHER_API_KEY
headers = {"Accept": "application/json",
  "Content-Type": "application/json"}
lang = "ru"



icons = {"01d": "☀️","02d":"⛅️" ,"03d":"☁️" ,"04d": "☁️","09d": "🌦","10d": "🌧","11d": "⛈","13d": "🌨","50d": "🌫",}
weather_conditions = {
    "thunderstorm with light rain": "11d",
    "thunderstorm with rain": "11d",
    "thunderstorm with heavy rain": "11d",
    "light thunderstorm": "11d",
    "thunderstorm": "11d",
    "heavy thunderstorm": "11d",
    "ragged thunderstorm": "11d",
    "thunderstorm with light drizzle": "11d",
    "thunderstorm with drizzle": "11d",
    "thunderstorm with heavy drizzle": "11d",
    "light intensity drizzle": "09d",
    "drizzle": "09d",
    "heavy intensity drizzle": "09d",
    "light intensity drizzle rain": "09d",
    "drizzle rain": "09d",
    "heavy intensity drizzle rain": "09d",
    "shower rain and drizzle": "09d",
    "heavy shower rain and drizzle": "09d",
    "shower drizzle": "09d",
    "light rain": "10d",
    "moderate rain": "10d",
    "heavy intensity rain": "10d",
    "very heavy rain": "10d",
    "extreme rain": "10d",
    "freezing rain": "13d",
    "light intensity shower rain": "09d",
    "shower rain": "09d",
    "heavy intensity shower rain": "09d",
    "ragged shower rain": "09d",
    "light snow": "13d",
    "snow": "13d",
    "heavy snow": "13d",
    "sleet": "13d",
    "light shower sleet": "13d",
    "shower sleet": "13d",
    "light rain and snow": "13d",
    "rain and snow": "13d",
    "light shower snow": "13d",
    "shower snow": "13d",
    "heavy shower snow": "13d",
    "mist": "50d",
    "smoke": "50d",
    "haze": "50d",
    "sand/dust whirls": "50d",
    "fog": "50d",
    "sand": "50d",
    "dust": "50d",
    "volcanic ash": "50d",
    "squalls": "50d",
    "tornado": "50d",
    "clear sky": "01d",
    "few clouds": "02d",
    "scattered clouds": "03d",
    "broken clouds": "04d",
    "overcast clouds": "04d"
}

def get_weather():
  try:
    response = requests.request("GET", f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={key}&units=metric", headers=headers, timeout=10)
    response.raise_for_status()
    response_dict = response.json()

    response2 = requests.request("GET", f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={key}&units=metric&lang={lang}", headers=headers, timeout=10)
    response2.raise_for_status()
    response_dict2 = response2.json()

    temp = round(response_dict["main"]["temp"])
    feels_like = round(response_dict["main"]["feels_like"])
    weather_desk=response_dict["weather"][0]["description"]
    weather_desk_ru=response_dict2["weather"][0]["description"]
    weather_icon = icons.get(weather_conditions.get(weather_desk, ""), "")

    return (f"Температура {temp}°C, ощущается как: {feels_like}°C\n{weather_desk_ru.title()} {weather_icon}")
  except (requests.RequestException, KeyError, TypeError, ValueError) as e:
    print(f"Ошибка получения погоды: {e}")
    return "Погода временно недоступна"
