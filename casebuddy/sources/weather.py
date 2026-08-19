"""Outdoor conditions and the daylight cycle, for the buddy scene's sky.

WHY OPEN-METEO IS THE DEFAULT
-----------------------------
No account, no API key, no signup -- which matters for something meant to come
up by itself at boot on an appliance display. OpenWeather is supported because
it is what most people already have a key for, but it is opt-in: pick the
provider and paste the key on the Weather tab.

THREE SKY SOURCES
-----------------
    weather   real conditions and real sunrise/sunset for your location
    clock     daylight only, from two times you set. No network at all.
    off       no sky data; the character's mood has the screen to itself

"clock" exists because the daylight cycle is most of the visual effect and it
costs nothing to compute, so wanting a sky that tracks the time of day should
not oblige anyone to make network requests.

WHAT LEAVES THE MACHINE
-----------------------
In "weather" mode with `location` set to "auto", one request goes to an
IP-geolocation service per launch -- which necessarily reveals your public IP
to it -- and then one request per refresh interval to the weather provider,
carrying a latitude and longitude rounded to three decimals. Put "lat,lon" in
`location` and the geolocation request never happens. Use "clock" or "off" and
neither does.

DAYLIGHT IS CONTINUOUS
----------------------
`daylight` and `twilight` are properties, not stored fields, so they keep
moving between the quarter-hourly refreshes; a reading fifteen minutes old
still gives the correct sky right now. They are also smooth: sunrise is a
half-hour ramp rather than a switch, because a sky that changed in one frame
would read as a fault.

THREADING
---------
Its own daemon thread, never the collector's. A sensor poll takes about 18 ms
and happens twice a second; a weather request can hang until it times out, and
one must never be able to stall the other.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

USER_AGENT = "CaseBuddy/1.0 (case-mounted telemetry panel)"
TIMEOUT_S = 8.0
DAY_S = 86400.0

# Our own small vocabulary. Both providers map onto it, so the sky code never
# has to know which one a reading came from.
CONDITIONS = ("clear", "partly", "cloudy", "overcast", "fog",
              "drizzle", "rain", "snow", "thunder")

SEPARATOR = "   ·   "
DEFAULT_LINE = "{place}{sep}{temp}{sep}{sky}"


class _Blanks(dict):
    """A template naming a field we do not have leaves a blank, not a crash."""

    def __missing__(self, key):
        return ""


# Sunrise and sunset are ramps, not switches.
DAWN_RAMP_S = 1800.0     # half an hour either side of the horizon crossing
TWILIGHT_SPAN_S = 2700.0  # how long the warm cast lingers


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


@dataclass(frozen=True)
class Weather:
    ok: bool = False
    place: str = ""
    temp_c: float | None = None
    feels_c: float | None = None
    humidity: float | None = None
    wind_kph: float | None = None
    condition: str = "clear"
    description: str = ""
    sunrise: float | None = None   # unix seconds, today
    sunset: float | None = None
    status: str = ""
    fetched: float = 0.0
    # True when the provider says so and no sunrise/sunset was available. Only
    # used as the fallback for `daylight`.
    day_flag: bool = True

    # --- the daylight cycle ----------------------------------------------

    @property
    def daylight(self) -> float:
        """0 at night, 1 in full day, ramped smoothly across the horizon."""
        if self.sunrise is None or self.sunset is None:
            return 1.0 if self.day_flag else 0.0
        now = time.time()
        risen = _smoothstep((now - self.sunrise) / DAWN_RAMP_S + 0.5)
        not_set = _smoothstep((self.sunset - now) / DAWN_RAMP_S + 0.5)
        return min(risen, not_set)

    @property
    def twilight(self) -> float:
        """Peaks at sunrise and sunset, zero at midday and in deep night."""
        if self.sunrise is None or self.sunset is None:
            return 0.0
        now = time.time()
        best = 0.0
        for moment in (self.sunrise, self.sunset):
            distance = abs(now - moment) / TWILIGHT_SPAN_S
            if distance < 1.0:
                best = max(best, 1.0 - distance * distance)
        return best

    @property
    def is_day(self) -> bool:
        return self.daylight >= 0.5

    @property
    def arc(self) -> float:
        """How far the sun (or the moon) is along its visible arc, 0 to 1."""
        if self.sunrise is None or self.sunset is None:
            return 0.5
        now = time.time()
        if self.sunrise <= now <= self.sunset:
            return (now - self.sunrise) / max(1.0, self.sunset - self.sunrise)
        night = max(1.0, DAY_S - (self.sunset - self.sunrise))
        return max(0.0, min(1.0, ((now - self.sunset) % DAY_S) / night))

    @property
    def line(self) -> str:
        """One string for a header slot: place, temperature, conditions."""
        return self.format(DEFAULT_LINE)

    def fields(self) -> dict:
        """Everything a line template may name. All strings, all safe to join."""
        def temp(value):
            return "" if value is None else f"{value:.0f} °C"

        def clock(stamp):
            return "" if not stamp else time.strftime("%H:%M", time.localtime(stamp))

        return {
            "place": self.place,
            "temp": temp(self.temp_c),
            "feels": temp(self.feels_c),
            "sky": self.description,
            "condition": self.condition,
            "humidity": "" if self.humidity is None else f"{self.humidity:.0f}%",
            "wind": "" if self.wind_kph is None else f"{self.wind_kph:.0f} km/h",
            "sunrise": clock(self.sunrise),
            "sunset": clock(self.sunset),
            "daylight": f"{self.daylight * 100:.0f}%",
            "sep": SEPARATOR,
        }

    def format(self, template: str) -> str:
        """Render a user template, dropping separators around empty fields.

        A reading with no wind should not leave "28 °C  ·    ·  Mumbai" on
        screen, so empty values take their neighbouring separator with them.
        """
        if not self.ok:
            return ""
        try:
            text = str(template).format_map(_Blanks(self.fields()))
        except Exception:
            text = DEFAULT_LINE.format_map(_Blanks(self.fields()))
        parts = [chunk.strip() for chunk in text.split(SEPARATOR)]
        return SEPARATOR.join(chunk for chunk in parts if chunk)


# WMO code -> (our condition, words). Open-Meteo publishes these directly.
WMO = {
    0: ("clear", "Clear"),
    1: ("clear", "Mainly clear"),
    2: ("partly", "Partly cloudy"),
    3: ("overcast", "Overcast"),
    45: ("fog", "Fog"),
    48: ("fog", "Rime fog"),
    51: ("drizzle", "Light drizzle"),
    53: ("drizzle", "Drizzle"),
    55: ("drizzle", "Heavy drizzle"),
    56: ("drizzle", "Freezing drizzle"),
    57: ("drizzle", "Freezing drizzle"),
    61: ("rain", "Light rain"),
    63: ("rain", "Rain"),
    65: ("rain", "Heavy rain"),
    66: ("rain", "Freezing rain"),
    67: ("rain", "Freezing rain"),
    71: ("snow", "Light snow"),
    73: ("snow", "Snow"),
    75: ("snow", "Heavy snow"),
    77: ("snow", "Snow grains"),
    80: ("rain", "Light showers"),
    81: ("rain", "Showers"),
    82: ("rain", "Violent showers"),
    85: ("snow", "Snow showers"),
    86: ("snow", "Heavy snow showers"),
    95: ("thunder", "Thunderstorm"),
    96: ("thunder", "Thunderstorm, hail"),
    99: ("thunder", "Thunderstorm, hail"),
}


def _from_owm_id(code: int) -> str:
    """OpenWeather condition id -> our vocabulary."""
    if 200 <= code < 300:
        return "thunder"
    if 300 <= code < 400:
        return "drizzle"
    if 500 <= code < 600:
        return "rain"
    if 600 <= code < 700:
        return "snow"
    if 700 <= code < 800:
        return "fog"
    if code in (800, 801):
        return "clear"
    if code == 802:
        return "partly"
    if code in (803, 804):
        return "overcast"
    return "cloudy"


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _maybe_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# --- location -------------------------------------------------------------

def parse_location(text: str) -> tuple[float, float] | None:
    """A "12.34,56.78" string to a coordinate pair. Anything else is None."""
    try:
        lat, lon = (float(part) for part in str(text).split(",", 1))
    except (TypeError, ValueError):
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    return None


def geocode(name: str) -> tuple[float, float, str] | None:
    """Look a place up by name. Open-Meteo geocoding: free, no key.

    Tried before the IP lookup, because someone who typed a city clearly meant
    that city. "Mumbai, Gujarat" is asked for whole first and then as just
    the part before the comma: the search is good at plain names and less good
    at name-plus-region, and dropping the qualifier is what a person would do.
    """
    attempts = [name.strip()]
    if "," in name:
        attempts.append(name.split(",", 1)[0].strip())
    for attempt in attempts:
        if not attempt:
            continue
        try:
            data = _get_json(
                "https://geocoding-api.open-meteo.com/v1/search"
                f"?name={urllib.parse.quote(attempt)}&count=1"
                "&language=en&format=json")
            results = data.get("results") or []
            if results:
                hit = results[0]
                return (float(hit["latitude"]), float(hit["longitude"]),
                        str(hit.get("name") or attempt))
        except Exception:
            continue
    return None


def locate_by_ip() -> tuple[float, float, str] | None:
    """Coarse location from the public IP. Two providers; first to answer wins."""
    try:
        data = _get_json("https://ipapi.co/json/")
        if data.get("latitude") is not None:
            return (float(data["latitude"]), float(data["longitude"]),
                    str(data.get("city") or ""))
    except Exception:
        pass
    try:
        data = _get_json("http://ip-api.com/json/?fields=status,lat,lon,city")
        if data.get("status") == "success":
            return float(data["lat"]), float(data["lon"]), str(data.get("city") or "")
    except Exception:
        pass
    return None


# --- providers ------------------------------------------------------------

def fetch_open_meteo(lat: float, lon: float, place: str) -> Weather:
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.3f}"
           f"&longitude={lon:.3f}&current=temperature_2m,apparent_temperature,"
           f"relative_humidity_2m,is_day,weather_code,wind_speed_10m"
           f"&daily=sunrise,sunset&forecast_days=1&timeformat=unixtime"
           f"&wind_speed_unit=kmh&timezone=auto")
    data = _get_json(url)
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    condition, words = WMO.get(int(current.get("weather_code", 0) or 0),
                               ("cloudy", "Cloudy"))
    # timeformat=unixtime returns true UTC epoch seconds even when timezone
    # is set -- utc_offset_seconds is for formatting local wall-clock strings,
    # not for these. Subtracting it moved sunrise six hours earlier.
    sunrise = _first(daily.get("sunrise"))
    sunset = _first(daily.get("sunset"))
    return Weather(
        ok=True, place=place,
        temp_c=_maybe_float(current.get("temperature_2m")),
        feels_c=_maybe_float(current.get("apparent_temperature")),
        humidity=_maybe_float(current.get("relative_humidity_2m")),
        wind_kph=_maybe_float(current.get("wind_speed_10m")),
        condition=condition, description=words,
        sunrise=sunrise,
        sunset=sunset,
        day_flag=bool(current.get("is_day", 1)),
        fetched=time.time(),
    )


def _first(values) -> float | None:
    if isinstance(values, list) and values:
        return _maybe_float(values[0])
    return None


def fetch_openweather(lat: float, lon: float, place: str, key: str) -> Weather:
    url = (f"https://api.openweathermap.org/data/2.5/weather?lat={lat:.3f}"
           f"&lon={lon:.3f}&units=metric&appid={key}")
    data = _get_json(url)
    entry = (data.get("weather") or [{}])[0]
    main = data.get("main") or {}
    wind = data.get("wind") or {}
    system = data.get("sys") or {}
    return Weather(
        ok=True, place=place or str(data.get("name") or ""),
        temp_c=_maybe_float(main.get("temp")),
        feels_c=_maybe_float(main.get("feels_like")),
        humidity=_maybe_float(main.get("humidity")),
        wind_kph=None if wind.get("speed") is None else float(wind["speed"]) * 3.6,
        condition=_from_owm_id(int(entry.get("id", 800) or 800)),
        description=str(entry.get("description") or "").capitalize(),
        sunrise=_maybe_float(system.get("sunrise")),
        sunset=_maybe_float(system.get("sunset")),
        fetched=time.time(),
    )


# --- the clock-only sky ---------------------------------------------------

def parse_clock(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in str(text).split(":", 1))
    except (TypeError, ValueError):
        return fallback
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour, minute
    return fallback


def clock_weather(day_starts: str, day_ends: str, place: str = "") -> Weather:
    """A synthetic reading: always clear, with the daylight you configured."""
    today = dt.date.today()
    rise_h, rise_m = parse_clock(day_starts, (7, 0))
    set_h, set_m = parse_clock(day_ends, (19, 0))
    rise = dt.datetime.combine(today, dt.time(rise_h, rise_m)).timestamp()
    fall = dt.datetime.combine(today, dt.time(set_h, set_m)).timestamp()
    if fall <= rise:                       # a day that ends before it starts
        fall = rise + 3600.0
    return Weather(ok=True, place=place, condition="clear", description="",
                   sunrise=rise, sunset=fall, fetched=time.time())


# --- watcher --------------------------------------------------------------

class WeatherWatcher:
    """Keeps one Weather current on its own thread. Never raises at callers."""

    # Failures back off rather than hammer: a provider that is down, or a
    # machine with no network, should cost one request a minute, not one a
    # second.
    RETRY_S = (60.0, 180.0, 600.0)
    # Clock mode needs no network but still has to notice midnight.
    CLOCK_PERIOD_S = 120.0

    def __init__(self, cfg: dict) -> None:
        weather_cfg = cfg.get("weather") or {}
        self.sky = str(weather_cfg.get("sky", "weather")).lower()
        if not weather_cfg.get("enabled", True):
            self.sky = "off"
        self.provider = str(weather_cfg.get("provider", "open-meteo")).lower()
        self.api_key = str(weather_cfg.get("api_key", "") or "")
        self.location = str(weather_cfg.get("location", "auto") or "auto")
        self.place_override = str(weather_cfg.get("place", "") or "")
        self.day_starts = str(weather_cfg.get("day_starts", "07:00"))
        self.day_ends = str(weather_cfg.get("day_ends", "19:00"))
        self.period_s = max(60.0, float(weather_cfg.get("refresh_minutes", 15)) * 60.0)

        self._lock = threading.Lock()
        self._weather = Weather(status="not started")
        self._fixed = parse_location(self.location)
        self._where: tuple[float, float, str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._logged = False

    @property
    def enabled(self) -> bool:
        return self.sky in ("weather", "clock")

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="casebuddy-weather",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Deliberately not joined: a socket already inside its 8 s timeout
        # would hold shutdown up for no benefit. The thread is a daemon.
        self._thread = None

    def latest(self) -> Weather | None:
        if not self.enabled:
            return None
        with self._lock:
            return self._weather

    @property
    def located(self) -> str:
        """Human description of where the readings are coming from."""
        if self.sky == "off":
            return "sky disabled"
        if self.sky == "clock":
            return f"clock only, daylight {self.day_starts} to {self.day_ends}"
        if self._where is None:
            return "location not resolved yet"
        lat, lon, name = self._where
        return f"{name or 'unknown'} ({lat:.2f}, {lon:.2f})"

    # --- internals --------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.sky == "clock":
                self._set(clock_weather(self.day_starts, self.day_ends,
                                        self.place_override))
                self._stop.wait(self.CLOCK_PERIOD_S)
                continue
            if self._tick():
                self._failures = 0
                wait = self.period_s
            else:
                wait = self.RETRY_S[min(self._failures, len(self.RETRY_S) - 1)]
                self._failures += 1
            self._stop.wait(wait)

    def _tick(self) -> bool:
        try:
            where = self._locate()
            if where is None:
                self._set(Weather(status="could not determine location"))
                return False
            lat, lon, place = where
            place = self.place_override or place
            if self.provider == "openweather":
                if not self.api_key:
                    self._set(Weather(status="OpenWeather needs an API key"))
                    return False
                reading = fetch_openweather(lat, lon, place, self.api_key)
            else:
                reading = fetch_open_meteo(lat, lon, place)
            self._set(reading)
            return True
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
            if not self._logged:
                self._logged = True
                print(f"[casebuddy] weather unavailable: {exc!r}")
            self._set(Weather(status=type(exc).__name__))
            return False

    def _locate(self) -> tuple[float, float, str] | None:
        """Coordinates, or a place name, or failing both of those, the IP."""
        if self._fixed is not None:
            lat, lon = self._fixed
            return lat, lon, self.place_override
        if self._where is not None:
            return self._where
        named = self.location.strip()
        if named and named.lower() != "auto":
            self._where = geocode(named)
            if self._where is not None:
                return self._where
            print(f"[casebuddy] could not find '{named}'; falling back to your IP")
        self._where = locate_by_ip()
        return self._where

    def _set(self, reading: Weather) -> None:
        with self._lock:
            # A failed refresh must not blank a good reading: a sky fifteen
            # minutes stale beats no sky at all, and daylight keeps moving
            # regardless because it is computed, not stored.
            if reading.ok or not self._weather.ok:
                self._weather = reading


if __name__ == "__main__":  # python -m casebuddy.sources.weather
    watcher = WeatherWatcher({"weather": {"enabled": True, "sky": "weather"}})
    watcher.start()
    for _ in range(30):
        time.sleep(0.5)
        reading = watcher.latest()
        if reading and (reading.ok or reading.status not in ("", "not started")):
            break
    print(watcher.located)
    current = watcher.latest()
    print(current)
    if current and current.sunrise:
        show = time.strftime("%H:%M", time.localtime(current.sunrise))
        down = time.strftime("%H:%M", time.localtime(current.sunset))
        print(f"  sunrise {show}  sunset {down}")
        print(f"  daylight {current.daylight:.3f}  twilight {current.twilight:.3f}"
              f"  arc {current.arc:.3f}  is_day {current.is_day}")
    watcher.stop()
