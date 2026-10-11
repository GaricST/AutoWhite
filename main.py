"""AutoWhite — подстройка точки белого gamescope под освещение в комнате.

Датчик освещённости (IIO) -> координаты xy в CIE 1931 -> X-атом gamescope
GAMESCOPE_DISPLAY_VIRTUAL_WHITE. Яркость не меняется.
"""

import asyncio
import glob
import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
import urllib.error
import urllib.request

import decky

ATOM = "GAMESCOPE_DISPLAY_VIRTUAL_WHITE"
NEUTRAL = (0.3128, 0.3292)      # D65, 6500 K
NEUTRAL_K = 6500.0

# Пользователь, которому принадлежит X-сервер gamescope. Decky даёт его в
# DECKY_USER (на SteamOS — deck).
SESSION_USER = str(getattr(decky, "DECKY_USER", "") or "deck")

# Датчики света: канал in_illuminance_* однозначно означает освещённость,
# каналы интенсивности у известных драйверов света — только если у них есть scale.
LUX_CHANNELS = ("in_illuminance_input", "in_illuminance_raw")
INTENSITY_CHANNELS = ("in_intensity_both_raw", "in_intensity_clear_raw", "in_intensity_raw")
ALS_NAMES = ("als", "ltrf216a", "ltr559", "ltr301", "opt3001", "opt3004", "opt4001",
             "apds9960", "isl29023", "cm32181", "stk3310", "gp2ap002", "bh1750")

SENSOR_INTERVAL = 1.0           # период опроса датчика, с
TICK = 0.2                      # период плавного шага, с
SMOOTH = 0.15                   # доля пути до цели за шаг (τ ≈ 1.2 с)
DEADBAND = 0.0005               # мёртвая зона: мельче — не трогаем экран
VERIFY_INTERVAL = 5.0           # как часто сверяем фактическую точку с нашей
VERIFY_TOLERANCE = 0.001        # расхождение, которое считаем чужой правкой
FAIL_DELAYS = (5.0, 15.0, 60.0) # пауза после подряд идущих ошибок записи, с

SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
PLUGIN_MANIFEST = os.path.join(decky.DECKY_PLUGIN_DIR, "plugin.json")
UPDATE_API = "https://api.github.com/repos/GaricST/AutoWhite/releases/latest"
RELEASES_PAGE = "https://github.com/GaricST/AutoWhite/releases"

# Точки чёрного тела в CIE 1931: температура -> (x, y). Интерполяция по mired,
# поэтому кривая идёт вдоль локуса, а не по диагонали x = y (диагональ даёт
# зеленоватый оттенок на промежуточных точках).
CCT_TABLE = (
    (2400, 0.4813, 0.4137),
    (2700, 0.4598, 0.4105),
    (3000, 0.4369, 0.4041),
    (3500, 0.4053, 0.3907),
    (4000, 0.3805, 0.3768),
    (4500, 0.3608, 0.3636),
    (5000, 0.3451, 0.3516),
    (5500, 0.3324, 0.3474),
    (6500, 0.3128, 0.3292),
)

# Модель консоли по DMI (Valve: Jupiter — LCD, Galileo — OLED; Lenovo: 83N0/83E1).
DMI_FILES = (
    "/sys/devices/virtual/dmi/id/product_name",
    "/sys/devices/virtual/dmi/id/product_version",
    "/sys/devices/virtual/dmi/id/board_name",
    "/sys/devices/virtual/dmi/id/sys_vendor",
)

# Профили устройств: тёплый конец кривой в кельвинах и пороги освещённости.
# Профиль применяется один раз и запоминается в настройках ключом device.
DEVICE_PROFILES = (
    (("jupiter", "galileo", "steam deck"),
     {"max_warm_k": 2700, "lux_dark": 30, "lux_bright": 900}),
    (("83e1", "83n0", "83n1", "legion go"),
     {"max_warm_k": 3900, "lux_dark": 15, "lux_bright": 400}),
)

DEFAULTS = {
    "enabled": True,
    "warmth": 1.0,          # насколько теплее в темноте (0..1)
    "lux_dark": 15.0,       # темнее — самая тёплая точка
    "lux_bright": 400.0,    # светлее — нейтральная
    "max_warm_k": 3900,     # температура самого тёплого конца, К
    "device": "",           # модель, под которую подставлен профиль
}

_last_state = {"lux": None, "white": None, "applied": False, "error": "",
               "sensor": None, "target": None, "supported": None}
_last_white = None


# ---------------------------------------------------------------- датчики света

def _read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_number(path):
    """Число из sysfs (бывает дробным) или None."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        value = float(text.split()[0])
    except (ValueError, IndexError):
        return None
    return value if math.isfinite(value) else None


def _scaled_lux(directory, channel, raw):
    """Переводит сырое значение канала в люксы, если драйвер даёт scale/offset."""
    scale_name = channel.replace("_raw", "_scale")
    scale = _read_number(os.path.join(directory, scale_name))
    if scale is None:
        return None
    offset = _read_number(os.path.join(directory, channel.replace("_raw", "_offset"))) or 0.0
    value = (raw + offset) * scale
    return value if math.isfinite(value) else None


def _lux_from_device(directory):
    """(люксы, подпись канала) для одного IIO-устройства или (None, причина)."""
    processed = _read_number(os.path.join(directory, "in_illuminance_input"))
    if processed is not None:
        # Ноль — валидное показание темноты, а не повод менять канал.
        return max(0.0, processed), "in_illuminance_input"
    raw = _read_number(os.path.join(directory, "in_illuminance_raw"))
    if raw is not None:
        scaled = _scaled_lux(directory, "in_illuminance_raw", raw)
        if scaled is not None:
            return max(0.0, scaled), "in_illuminance_raw*scale"
        return max(0.0, raw), "in_illuminance_raw"
    name = _read_text(os.path.join(directory, "name")) or ""
    if name.lower() not in ALS_NAMES:
        return None, None
    for channel in INTENSITY_CHANNELS:
        raw = _read_number(os.path.join(directory, channel))
        if raw is None:
            continue
        scaled = _scaled_lux(directory, channel, raw)
        if scaled is None:
            # Без scale это произвольные счётчики АЦП — за люксы их не выдаём.
            return None, name + "/" + channel + " без scale (не откалиброван)"
        return max(0.0, scaled), name + "/" + channel + "*scale"
    return None, None


def _device_sort_key(path):
    number = re.search(r"(\d+)$", os.path.basename(path))
    return (int(number.group(1)) if number else 0, path)


def _combine_lux(values):
    """Несколько датчиков: при близких показаниях — среднее, иначе первый.

    Двойной ALS есть, например, в Steam Deck OLED. Порядок датчиков —
    по номеру устройства, а не лексикографически.
    """
    values = [v for v in values if v is not None]
    if not values:
        return None
    if len(values) > 1 and min(values) > 0 and max(values) <= min(values) * 4:
        return sum(values) / len(values)
    return values[0]


def _note_sensor(source):
    if _last_state.get("sensor") != source:
        _last_state["sensor"] = source
        if source:
            decky.logger.info(f"autowhite: датчик освещённости {source}")


def _read_als_lux():
    """Освещённость в люксах или None, если датчика нет."""
    readings = []
    notes = []
    for directory in sorted(glob.glob("/sys/bus/iio/devices/iio:device*"), key=_device_sort_key):
        value, source = _lux_from_device(directory)
        if value is not None:
            readings.append((os.path.basename(directory) + "/" + source, value))
        elif source:
            notes.append(source)
    if readings:
        _note_sensor(" + ".join(name for name, _ in readings))
        return _combine_lux([value for _, value in readings])
    if notes and _last_state.get("sensor_note") != notes[0]:
        _last_state["sensor_note"] = notes[0]
        decky.logger.warning(f"autowhite: датчик найден, но не откалиброван: {notes[0]}")
    _note_sensor(None)
    return None


# --------------------------------------------------------------- gamescope / X

def _binary(name, fallback):
    return shutil.which(name) or fallback


def _display():
    """Дисплей игровой сессии: из окружения или по наличию X-сокета."""
    from_env = os.environ.get("DISPLAY", "")
    if from_env:
        return from_env
    candidates = ["/tmp/.X11-unix/X%d" % number for number in range(0, 4)]
    candidates += sorted(glob.glob("/run/user/*/.X11-unix/X*"))
    for socket in candidates:
        match = re.search(r"X(\d+)$", socket)
        if match and os.path.exists(socket):
            return ":" + match.group(1)
    return ":0"


def _xprop(args, text=False):
    """Запускает xprop от имени пользователя сессии.

    Плагин работает от root, а X-сервер gamescope принадлежит пользователю
    сессии, поэтому без runuser xprop отвечает «Authorization required».
    """
    xprop = _binary("xprop", "/usr/bin/xprop")
    display = "DISPLAY=" + _display()
    env_bin = _binary("env", "/usr/bin/env")
    commands = []
    runuser = _binary("runuser", "/usr/sbin/runuser")
    if SESSION_USER and os.path.exists(runuser):
        commands.append([runuser, "-u", SESSION_USER, "--", env_bin, display, xprop] + args)
    commands.append([env_bin, display, xprop] + args)
    last_error = None
    for command in commands:
        try:
            return subprocess.run(command, capture_output=True, timeout=5, check=True, text=text)
        except FileNotFoundError as exc:
            last_error = exc
            continue
        except subprocess.CalledProcessError as exc:
            last_error = exc
            break
    if last_error is not None:
        raise last_error
    raise RuntimeError("xprop не запустился")


def _check_xy(point):
    x, y = point
    return math.isfinite(x) and math.isfinite(y) and 0.0 < x < 1.0 and 0.0 < y < 1.0 and x + y < 1.0


def _write_white(point) -> bool:
    """Пишет виртуальную точку белого в gamescope (x, y в CIE 1931)."""
    if not _check_xy(point):
        _last_state["error"] = "некорректные координаты точки белого"
        return False
    bits = [struct.unpack("<I", struct.pack("<f", float(v)))[0] for v in point]
    try:
        _xprop(["-root", "-f", ATOM, "32c", "-set", ATOM, f"{bits[0]}, {bits[1]}"])
        _last_state["supported"] = True
        return True
    except FileNotFoundError as exc:
        _last_state["error"] = "xprop не найден: " + str(exc)[:60]
        return False
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()[:80]
        _last_state["error"] = "xprop: " + (detail or str(exc.returncode))
        if not _last_state.get("warned"):
            _last_state["warned"] = True
            decky.logger.warning(f"autowhite: не удалось открыть дисплей: {detail}")
        return False
    except (OSError, subprocess.SubprocessError, struct.error) as exc:
        _last_state["error"] = str(exc)[:80]
        return False


def _read_white():
    """Фактическая точка белого из gamescope или None, если прочитать нельзя."""
    try:
        result = _xprop(["-root", ATOM], text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or "=" not in result.stdout:
        return None
    raw = result.stdout.split("=", 1)[1]
    parts = [part.strip() for part in raw.split(",")][:2]
    if len(parts) < 2:
        return None
    try:
        point = tuple(round(struct.unpack("<f", struct.pack("<I", int(part)))[0], 4)
                      for part in parts)
    except (ValueError, struct.error):
        return None
    if not _check_xy(point):
        return None
    _last_state["supported"] = True
    return point


# ------------------------------------------------------------------- расчёт xy

def _xy_for_cct(kelvin):
    """Координаты xy для температуры по локусу (интерполяция по mired)."""
    kelvin = max(CCT_TABLE[0][0], min(CCT_TABLE[-1][0], float(kelvin)))
    points = sorted((1e6 / k, x, y) for k, x, y in CCT_TABLE)
    mired = 1e6 / kelvin
    if mired <= points[0][0]:
        return points[0][1], points[0][2]
    if mired >= points[-1][0]:
        return points[-1][1], points[-1][2]
    for (m1, x1, y1), (m2, x2, y2) in zip(points, points[1:]):
        if m1 <= mired <= m2:
            share = (mired - m1) / (m2 - m1)
            return round(x1 + (x2 - x1) * share, 4), round(y1 + (y2 - y1) * share, 4)
    return NEUTRAL


def _kelvin_mix(warm_k, warmth, t):
    """Температура кривой: ползунок задаёт тёплый конец, свет — положение на нём."""
    warm_k = max(2000.0, min(6500.0, float(warm_k)))
    warmth = max(0.0, min(1.0, float(warmth)))
    dark_mired = 1e6 / warm_k
    neutral_mired = 1e6 / NEUTRAL_K
    # Ползунок: от нейтрали (0) до максимально тёплого конца (1).
    warm_mired = neutral_mired + (dark_mired - neutral_mired) * warmth
    return 1e6 / (warm_mired + (neutral_mired - warm_mired) * max(0.0, min(1.0, t)))


def _target_white(settings, lux):
    """Куда вести точку белого: (x, y) или None, если данных нет."""
    if lux is None:
        return None
    dark = max(1.0, float(settings.get("lux_dark", 15.0)))
    bright = max(dark + 1.0, float(settings.get("lux_bright", 400.0)))
    # Логарифмическая шкала: восприятие света ближе к логарифму, иначе в обычной
    # комнате (100–300 лк) экран почти сразу уходит в нейтраль.
    t = math.log(max(lux, 1.0) / dark) / math.log(bright / dark)
    kelvin = _kelvin_mix(settings.get("max_warm_k", 3900), settings.get("warmth", 1.0), t)
    return _xy_for_cct(kelvin)


def _approach(cur, want, smooth=SMOOTH, deadband=DEADBAND):
    """Один шаг плавного приближения к цели (экспоненциальное сглаживание)."""
    if abs(want[0] - cur[0]) <= deadband and abs(want[1] - cur[1]) <= deadband:
        return want
    nxt = (round(cur[0] + (want[0] - cur[0]) * smooth, 4),
           round(cur[1] + (want[1] - cur[1]) * smooth, 4))
    return nxt if nxt != cur else want


# --------------------------------------------------------- версии и обновления

def _current_version():
    try:
        with open(PLUGIN_MANIFEST) as f:
            return str(json.load(f).get("version") or "")
    except (OSError, ValueError):
        return ""


def _version_key(text):
    """«1.2.0-beta» < «1.2.0» < «1.2.1»: числа, затем стабильная выше беты."""
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not found:
        return ((0, 0, 0), 1, "")
    nums = tuple(int(part) if part else 0 for part in found.groups())
    suffix = str(text).partition("-")[2]
    return (nums, 0 if suffix else 1, suffix)


def _check_update():
    """Спрашивает у GitHub последний релиз и сравнивает с установленной версией."""
    current = _current_version()
    try:
        request = urllib.request.Request(
            UPDATE_API,
            headers={"User-Agent": "AutoWhite", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read(65536).decode("utf-8", errors="replace")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("неожиданный ответ GitHub")
        latest = str(data.get("tag_name") or data.get("name") or "").lstrip("vV")
        return {
            "current": current,
            "latest": latest,
            "url": str(data.get("html_url") or RELEASES_PAGE),
            "has_update": bool(latest) and _version_key(latest) > _version_key(current),
        }
    except urllib.error.HTTPError as exc:
        return {"current": current, "error": str(exc), "code": exc.code, "url": RELEASES_PAGE}
    except Exception as exc:
        return {"current": current, "error": str(exc), "url": RELEASES_PAGE}


# ---------------------------------------------------------------- профиль, файл

def _device_profile():
    """(модель, настройки профиля) под текущую консоль или ("", {})."""
    text = [_read_text(path) or "" for path in DMI_FILES]
    haystack = " ".join(text).lower()
    for keys, values in DEVICE_PROFILES:
        for key in keys:
            if key in haystack:
                return key, dict(values)
    return "", {}


def _save_settings(settings):
    """Атомарная запись настроек: временный файл + os.replace."""
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        tmp = SETTINGS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS)
        return True
    except OSError as exc:
        decky.logger.warning(f"autowhite: настройки не сохранены ({exc})")
        return False


class Plugin:
    def __init__(self):
        self.settings = dict(DEFAULTS)
        self.task = None

    # ------------------------------------------------------------------ настройки

    def _load(self):
        try:
            with open(SETTINGS) as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for key in DEFAULTS:
                    if key in stored:
                        self.settings[key] = stored[key]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            decky.logger.warning(f"autowhite: настройки не прочитаны ({exc})")
        self._sanitize()
        self._apply_device_profile()

    def _apply_device_profile(self):
        """На новой консоли подставляет её профиль (тёплый конец и пороги)."""
        name, profile = _device_profile()
        if not profile or self.settings.get("device") == name:
            return
        self.settings.update(profile)
        self.settings["device"] = name
        self._sanitize()
        _save_settings(self.settings)
        decky.logger.info(
            f"autowhite: профиль устройства {name} "
            f"(тёплый конец {profile['max_warm_k']} К, пороги "
            f"{profile['lux_dark']}–{profile['lux_bright']} лк)")

    def _sanitize(self):
        """Проверяет каждое поле отдельно: одна битая настройка не сбрасывает всё."""
        enabled = self.settings.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("false", "0", "off", "нет", "")
        self.settings["enabled"] = bool(enabled)

        def number(key, default, low, high):
            try:
                value = float(self.settings.get(key, default))
            except (TypeError, ValueError):
                value = float(default)
            if not math.isfinite(value):
                value = float(default)
            self.settings[key] = max(low, min(high, value))

        number("warmth", 1.0, 0.0, 1.0)
        number("lux_dark", 15.0, 0.0, 100000.0)
        number("lux_bright", 400.0, 1.0, 100000.0)
        number("max_warm_k", 3900.0, 2000.0, 6500.0)
        if self.settings["lux_bright"] <= self.settings["lux_dark"]:
            self.settings["lux_bright"] = self.settings["lux_dark"] + 1.0
        device = self.settings.get("device", "")
        self.settings["device"] = str(device)[:64] if isinstance(device, str) else ""

    def _save(self):
        return _save_settings(self.settings)

    # ---------------------------------------------------------------------- цикл

    async def _loop(self):
        loop = asyncio.get_running_loop()
        global _last_white
        want = None
        last_read = 0.0
        last_verify = 0.0
        last_logged = None
        prev_enabled = None
        fails = 0
        retry_at = 0.0
        while True:
            try:
                if os.getppid() == 1:
                    decky.logger.warning("autowhite: Decky перезапущен, выходим")
                    await loop.run_in_executor(None, _write_white, NEUTRAL)
                    os._exit(0)
                settings = dict(self.settings)
                now = time.monotonic()
                enabled = bool(settings.get("enabled", True))
                if enabled != prev_enabled:
                    prev_enabled = enabled
                    last_read = 0.0
                    last_verify = 0.0
                    last_logged = None
                if not enabled:
                    # Выключено: один раз возвращаем нейтраль и больше экран не трогаем.
                    if _last_white is not None and _last_white != NEUTRAL and now >= retry_at:
                        if await loop.run_in_executor(None, _write_white, NEUTRAL):
                            _last_white = NEUTRAL
                            _last_state["white"] = NEUTRAL
                            _last_state["target"] = list(NEUTRAL)
                    await asyncio.sleep(TICK)
                    continue
                if now - last_read >= SENSOR_INTERVAL:
                    lux = await loop.run_in_executor(None, _read_als_lux)
                    _last_state["lux"] = round(lux) if lux is not None else None
                    want = _target_white(settings, lux)
                    _last_state["target"] = list(want) if want else None
                    last_read = now
                if want is not None and now - last_verify >= VERIFY_INTERVAL:
                    last_verify = now
                    actual = await loop.run_in_executor(None, _read_white)
                    if actual is None:
                        # Свойство пропало (перезапуск gamescope) — применяем заново.
                        _last_white = None
                    elif _last_white is not None and (
                            abs(actual[0] - _last_white[0]) > VERIFY_TOLERANCE
                            or abs(actual[1] - _last_white[1]) > VERIFY_TOLERANCE):
                        decky.logger.info(
                            f"autowhite: точку белого изменили извне "
                            f"({actual[0]:.4f} / {actual[1]:.4f}) — возвращаю свою")
                        _last_white = actual
                        _last_state["white"] = actual
                        last_logged = None
                cur = _last_white
                if want is not None and now >= retry_at:
                    step = want if cur is None else _approach(cur, want)
                    if step != cur:
                        if await loop.run_in_executor(None, _write_white, step):
                            _last_white = step
                            _last_state["white"] = step
                            _last_state["applied"] = True
                            _last_state["error"] = ""
                            fails = 0
                            retry_at = 0.0
                            if last_logged is None or abs(step[0] - last_logged[0]) >= 0.002:
                                last_logged = step
                                decky.logger.info(
                                    f"autowhite: точка белого {step[0]:.4f} / "
                                    f"{step[1]:.4f} (люкс {_last_state['lux']})")
                        else:
                            # Ошибка записи: не долбим xprop каждые 0.2 с.
                            fails += 1
                            delay = FAIL_DELAYS[min(fails - 1, len(FAIL_DELAYS) - 1)]
                            retry_at = now + delay
                            decky.logger.warning(
                                f"autowhite: запись не удалась ({fails}), пауза {delay:.0f} с")
                await asyncio.sleep(TICK)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _last_state["error"] = str(exc)[:80]
                decky.logger.error(f"autowhite: ошибка цикла: {exc}")
                await asyncio.sleep(1.0)

    # ------------------------------------------------------- жизненный цикл Decky

    async def _main(self):
        self._load()
        decky.logger.info(
            f"autowhite: старт (теплота {self.settings.get('warmth')}, "
            f"тёплый конец {self.settings.get('max_warm_k'):.0f} К, "
            f"{'включено' if self.settings.get('enabled') else 'выключено'})")
        global _last_white
        loop = asyncio.get_running_loop()
        if self.settings.get("enabled", True):
            lux = await loop.run_in_executor(None, _read_als_lux)
            _last_state["lux"] = round(lux) if lux is not None else None
            want = _target_white(self.settings, lux)
            _last_state["target"] = list(want) if want else None
            if want is not None and await loop.run_in_executor(None, _write_white, want):
                _last_white = want
                _last_state["white"] = want
                _last_state["applied"] = True
                decky.logger.info(
                    f"autowhite: точка белого {want[0]:.4f} / {want[1]:.4f} "
                    f"(люкс {_last_state['lux']})")
        else:
            _last_white = NEUTRAL
            _last_state["white"] = NEUTRAL
            await loop.run_in_executor(None, _write_white, NEUTRAL)
        self.task = asyncio.create_task(self._loop())

    async def _unload(self):
        loop = asyncio.get_running_loop()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                decky.logger.warning(f"autowhite: цикл остановлен с ошибкой ({exc})")
            self.task = None
        global _last_white
        if _last_white is not None and _last_white != NEUTRAL:
            # Отключили/выгрузили плагин — возвращаем нейтральную точку.
            await loop.run_in_executor(None, _write_white, NEUTRAL)
            _last_white = NEUTRAL
            _last_state["white"] = NEUTRAL

    async def _uninstall(self):
        await self._unload()
        await asyncio.get_running_loop().run_in_executor(None, _write_white, NEUTRAL)

    # -------------------------------------------------------------- API панели

    async def get_state(self) -> dict:
        return {
            "settings": dict(self.settings),
            "state": dict(_last_state),
            "neutral": list(NEUTRAL),
        }

    async def set_settings(self, patch: dict) -> dict:
        if isinstance(patch, dict):
            was_enabled = bool(self.settings.get("enabled", True))
            for key in DEFAULTS:
                if key == "device":
                    continue
                if key in patch:
                    self.settings[key] = patch[key]
            self._sanitize()
            self._save()
            now_enabled = bool(self.settings.get("enabled", True))
            if was_enabled and not now_enabled:
                decky.logger.info("autowhite: выключено, возвращаю нейтральную точку")
            elif not was_enabled and now_enabled:
                decky.logger.info("autowhite: включено")
        return await self.get_state()

    async def check_update(self) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _check_update)

    async def reset_settings(self) -> dict:
        device = self.settings.get("device", "")
        self.settings = dict(DEFAULTS)
        self.settings["device"] = device
        self._sanitize()
        self._apply_device_profile()
        self._save()
        return await self.get_state()
