import asyncio
import glob
import json
import os
import re
import struct
import subprocess
import time
import urllib.error
import urllib.request

import decky

ATOM = "GAMESCOPE_DISPLAY_VIRTUAL_WHITE"
# Пользователь, которому принадлежит X-сервер gamescope. Decky даёт его в
# DECKY_USER (на SteamOS — deck); если переменной нет, пробуем «deck».
SESSION_USER = str(getattr(decky, "DECKY_USER", "") or "deck")
# Датчики света: у консолей разные драйверы (Legion — HID «als», Steam Deck —
# ltrf216a и другие). Канал in_illuminance_* однозначно означает освещённость,
# поэтому его ищем у любого устройства, а каналы интенсивности — только у
# известных драйверов света.
# Сначала обработанный ядром канал (в люксах), затем «сырой» — у части драйверов
# (например, ltrf216a на Steam Deck) raw отдаёт не люксы.
LUX_CHANNELS = ("in_illuminance_input", "in_illuminance_raw")
INTENSITY_CHANNELS = ("in_intensity_both_raw", "in_intensity_clear_raw", "in_intensity_raw")
ALS_NAMES = ("als", "ltrf216a", "ltr559", "ltr301", "opt3001", "opt3004", "opt4001",
             "apds9960", "isl29023", "cm32181", "stk3310", "gp2ap002", "bh1750")
NEUTRAL = (0.3128, 0.3292)      # D65
LUX_DARK = 15.0                 # темнее — самая тёплая
LUX_BRIGHT = 250.0              # светлее — нейтральная
SENSOR_INTERVAL = 1.0           # период опроса датчика, с
TICK = 0.2                      # период плавного шага, с
SMOOTH = 0.15                   # доля пути до цели за шаг (τ ≈ 1.2 с)
DEADBAND = 0.0005               # мёртвая зона: мельче — не трогаем экран
VERIFY_INTERVAL = 5.0           # как часто сверяем фактическую точку с нашей
VERIFY_TOLERANCE = 0.001        # расхождение, которое считаем чужой правкой
SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
PLUGIN_MANIFEST = os.path.join(decky.DECKY_PLUGIN_DIR, "plugin.json")
UPDATE_API = "https://api.github.com/repos/GaricST/AutoWhite/releases/latest"
RELEASES_PAGE = "https://github.com/GaricST/AutoWhite/releases"

DEFAULTS = {
    "enabled": True,
    "warmth": 1.0,          # насколько теплее в темноте (0..1)
    "lux_dark": LUX_DARK,
    "lux_bright": LUX_BRIGHT,
    "max_warm": 0.3750,
}

_last_state = {"lux": None, "white": None, "applied": False, "error": "",
               "sensor": None}
_last_white = None


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _note_sensor(source):
    if _last_state.get("sensor") != source:
        _last_state["sensor"] = source
        decky.logger.info(f"autowhite: датчик освещённости {source}")


def _combine_lux(values):
    """Несколько датчиков света: при близких показаниях — среднее, иначе первый.

    Двойной ALS есть, например, в Steam Deck OLED.
    """
    if not values:
        return None
    if len(values) > 1 and min(values) > 0 and max(values) <= min(values) * 4:
        return sum(values) / len(values)
    return values[0]


def _read_als_lux():
    """Освещённость в люксах или None, если датчика нет."""
    devices = sorted(glob.glob("/sys/bus/iio/devices/iio:device*"))
    # 1. Любой датчик с каналом освещённости — это точно ALS.
    readings = []
    for directory in devices:
        # Обработанный канал (люксы) предпочтительнее; если он пуст или нулевой,
        # а «сырой» даёт значение — берём сырой (бывает у ltrf216a на старте).
        processed = _read_int(os.path.join(directory, LUX_CHANNELS[0]))
        raw = _read_int(os.path.join(directory, LUX_CHANNELS[1]))
        if processed not in (None, 0):
            channel, value = LUX_CHANNELS[0], processed
        elif raw is not None:
            channel, value = LUX_CHANNELS[1], raw
        elif processed is not None:
            channel, value = LUX_CHANNELS[0], processed
        else:
            continue
        readings.append((os.path.basename(directory) + "/" + channel, float(value)))
    if readings:
        _note_sensor(" + ".join(name for name, _ in readings))
        return _combine_lux([value for _, value in readings])
    # 2. Известные драйверы света: у них бывает только канал интенсивности.
    for directory in devices:
        try:
            with open(os.path.join(directory, "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name.lower() not in ALS_NAMES:
            continue
        for channel in INTENSITY_CHANNELS:
            value = _read_int(os.path.join(directory, channel))
            if value is not None:
                _note_sensor(name + "/" + channel)
                return float(value)
    return None


def _display():
    """Дисплей игровой сессии: из окружения или по наличию X-сокета.

    На SteamOS это обычно :0, но на других сборках gamescope может занять :1.
    """
    from_env = os.environ.get("DISPLAY", "")
    if from_env:
        return from_env
    for number in (0, 1, 2):
        if os.path.exists("/tmp/.X11-unix/X%d" % number):
            return ":%d" % number
    return ":0"


def _xprop(args, text=False):
    """Запускает xprop от имени пользователя сессии.

    Плагин работает от root, а X-сервер gamescope принадлежит пользователю
    сессии, поэтому без runuser xprop отвечает «Authorization required».
    Если runuser недоступен, пробуем напрямую.
    """
    display = "DISPLAY=" + _display()
    if SESSION_USER:
        try:
            return subprocess.run(
                ["runuser", "-u", SESSION_USER, "--", "env", display, "xprop"] + args,
                capture_output=True, timeout=5, check=True, text=text)
        except FileNotFoundError:
            pass
    return subprocess.run(
        ["env", display, "xprop"] + args,
        capture_output=True, timeout=5, check=True, text=text)


def _write_white(point) -> bool:
    """Пишет виртуальную точку белого в gamescope (x, y в CIE 1931)."""
    bits = [struct.unpack("<I", struct.pack("<f", float(v)))[0] for v in point]
    try:
        _xprop(["-root", "-f", ATOM, "32c", "-set", ATOM, f"{bits[0]}, {bits[1]}"])
        return True
    except FileNotFoundError as exc:
        _last_state["error"] = "xprop не найден: " + str(exc)[:60]
        decky.logger.warning("autowhite: xprop не найден в PATH")
        return False
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()[:80]
        _last_state["error"] = "xprop: " + (detail or str(exc.returncode))
        if not _last_state.get("warned"):
            _last_state["warned"] = True
            decky.logger.warning(f"autowhite: не удалось открыть дисплей: {detail}")
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        _last_state["error"] = str(exc)[:80]
        return False


def _read_white():
    """Фактическая точка белого из gamescope или None, если прочитать нельзя.

    Нужна, чтобы заметить чужую правку: после перезагрузки консоли gamescope
    создаёт атом заново (в нейтрали) уже после старта плагина, поэтому одного
    «записать при старте» недостаточно.
    """
    try:
        result = _xprop(["-root", ATOM], text=True)
        if result.returncode != 0 or "=" not in result.stdout:
            return None
        raw = result.stdout.split("=", 1)[1]
        parts = [part.strip() for part in raw.split(",")][:2]
        if len(parts) < 2:
            return None
        return tuple(round(struct.unpack("<f", struct.pack("<I", int(part)))[0], 4)
                     for part in parts)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _current_version():
    try:
        with open(PLUGIN_MANIFEST) as f:
            return str(json.load(f).get("version") or "")
    except (OSError, ValueError):
        return ""


def _version_key(text):
    """«1.2.0-beta» < «1.2.0» < «1.2.1»: сначала числа, потом стабильная выше беты."""
    text = str(text or "").strip().lstrip("vV")
    main, _, suffix = text.partition("-")
    nums = []
    for part in main.split("."):
        digits = re.match(r"\d+", part)
        nums.append(int(digits.group(0)) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (tuple(nums), 0 if suffix else 1, suffix)


def _check_update():
    """Спрашивает у GitHub последний релиз и сравнивает с установленной версией."""
    current = _current_version()
    try:
        request = urllib.request.Request(
            UPDATE_API,
            headers={"User-Agent": "AutoWhite", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"current": current, "error": str(exc), "code": exc.code,
                "url": RELEASES_PAGE}
    except Exception as exc:
        return {"current": current, "error": str(exc), "url": RELEASES_PAGE}
    latest = str(data.get("tag_name") or data.get("name") or "").lstrip("vV")
    return {
        "current": current,
        "latest": latest,
        "url": data.get("html_url") or RELEASES_PAGE,
        "has_update": bool(latest) and _version_key(latest) > _version_key(current),
    }


def _approach(cur, want, smooth=SMOOTH, deadband=DEADBAND):
    """Один шаг плавного приближения к цели (экспоненциальное сглаживание).

    Возвращает новую точку белого: чем дальше цель, тем крупнее шаг, поэтому
    переход идёт ровно, без ступенек и без мгновенных скачков.
    """
    if abs(want[0] - cur[0]) <= deadband and abs(want[1] - cur[1]) <= deadband:
        return want
    nxt = (round(cur[0] + (want[0] - cur[0]) * smooth, 4),
           round(cur[1] + (want[1] - cur[1]) * smooth, 4))
    return nxt if nxt != cur else want


def _target_white(settings, lux):
    """Куда вести точку белого: (x, y) или None, если данных нет."""
    if lux is None:
        return None
    warmth = max(0.0, min(1.0, float(settings.get("warmth", 1.0))))
    max_warm = float(settings.get("max_warm", 0.375))
    warm = (NEUTRAL[0] + (max_warm - NEUTRAL[0]) * warmth,
            NEUTRAL[1] + (max_warm - NEUTRAL[1]) * warmth)
    dark = float(settings.get("lux_dark", LUX_DARK))
    bright = max(dark + 1.0, float(settings.get("lux_bright", LUX_BRIGHT)))
    t = max(0.0, min(1.0, (lux - dark) / (bright - dark)))
    return (round(warm[0] + (NEUTRAL[0] - warm[0]) * t, 4),
            round(warm[1] + (NEUTRAL[1] - warm[1]) * t, 4))


class Plugin:
    def __init__(self):
        self.settings = dict(DEFAULTS)
        self.task = None

    def _load(self):
        try:
            with open(SETTINGS) as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                # Берём только известные ключи: старые версии писали лишнее.
                for key in DEFAULTS:
                    if key in stored:
                        self.settings[key] = stored[key]
        except FileNotFoundError:
            pass
        except Exception as exc:
            decky.logger.warning(f"autowhite: настройки не прочитаны ({exc})")
        self._sanitize()

    def _sanitize(self):
        self.settings["enabled"] = bool(self.settings.get("enabled", True))
        try:
            self.settings["warmth"] = max(0.0, min(1.0, float(self.settings.get("warmth", 1.0))))
            self.settings["max_warm"] = max(0.32, min(0.42, float(self.settings.get("max_warm", 0.375))))
            self.settings["lux_dark"] = max(0, int(self.settings.get("lux_dark", LUX_DARK)))
            self.settings["lux_bright"] = max(
                self.settings["lux_dark"] + 1, int(self.settings.get("lux_bright", LUX_BRIGHT)))
        except (TypeError, ValueError):
            self.settings.update(DEFAULTS)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            with open(SETTINGS, "w") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            decky.logger.warning(f"autowhite: настройки не сохранены ({exc})")

    async def _loop(self):
        loop = asyncio.get_running_loop()
        global _last_white
        want = None
        last_read = 0.0
        last_verify = 0.0
        last_logged = None
        prev_enabled = None
        while True:
            try:
                if os.getppid() == 1:
                    decky.logger.warning("autowhite: Decky перезапущен, выходим")
                    os._exit(0)
                settings = self.settings
                now = time.monotonic()
                enabled = bool(settings.get("enabled", True))
                if enabled != prev_enabled:
                    prev_enabled = enabled
                    last_read = 0.0          # после переключения читаем датчик сразу
                if not enabled:
                    want = NEUTRAL
                elif now - last_read >= SENSOR_INTERVAL:
                    lux = await loop.run_in_executor(None, _read_als_lux)
                    _last_state["lux"] = round(lux) if lux is not None else None
                    want = _target_white(settings, lux)
                    last_read = now
                # Сверяем атом только когда есть цель: без датчика плагин
                # вообще не трогает X (важно для консолей, где ALS нет).
                if want is not None and now - last_verify >= VERIFY_INTERVAL:
                    last_verify = now
                    actual = await loop.run_in_executor(None, _read_white)
                    if actual is not None and _last_white is not None:
                        if (abs(actual[0] - _last_white[0]) > VERIFY_TOLERANCE
                                or abs(actual[1] - _last_white[1]) > VERIFY_TOLERANCE):
                            # Точку изменили извне (gamescope после перезагрузки,
                            # ночной режим Steam, другой плагин) — возвращаем свою.
                            decky.logger.info(
                                f"autowhite: точку белого изменили извне "
                                f"({actual[0]:.4f} / {actual[1]:.4f}) — возвращаю свою")
                            _last_white = actual
                            _last_state["white"] = actual
                            last_logged = None
                cur = _last_white
                if want is not None:
                    # cur is None — первое применение после старта: без разгона.
                    step = want if cur is None else _approach(cur, want)
                    if step != cur:
                        if await loop.run_in_executor(None, _write_white, step):
                            _last_white = step
                            _last_state["white"] = step
                            _last_state["applied"] = True
                            _last_state["error"] = ""
                            if last_logged is None or abs(step[0] - last_logged[0]) >= 0.002:
                                last_logged = step
                                decky.logger.info(
                                    f"autowhite: точка белого {step[0]:.4f} / "
                                    f"{step[1]:.4f} (люкс {_last_state['lux']})")
                await asyncio.sleep(TICK)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _last_state["error"] = str(exc)[:80]
                decky.logger.error(f"autowhite: ошибка цикла: {exc}")
                await asyncio.sleep(1.0)

    # --- жизненный цикл Decky ---
    async def _main(self):
        self._load()
        decky.logger.info(
            f"autowhite: старт (теплота {self.settings.get('warmth')}, "
            f"{'включено' if self.settings.get('enabled') else 'выключено'})")
        global _last_white
        loop = asyncio.get_running_loop()
        if self.settings.get("enabled", True):
            lux = await loop.run_in_executor(None, _read_als_lux)
            want = _target_white(self.settings, lux)
            if want is not None and await loop.run_in_executor(None, _write_white, want):
                _last_white = want
                _last_state["white"] = want
                _last_state["lux"] = round(lux) if lux is not None else None
        else:
            _last_white = NEUTRAL
            await loop.run_in_executor(None, _write_white, NEUTRAL)
        self.task = asyncio.create_task(self._loop())

    async def _unload(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass
            self.task = None

    async def _uninstall(self):
        await self._unload()
        await asyncio.get_running_loop().run_in_executor(None, _write_white, NEUTRAL)

    # --- API для панели ---
    async def get_state(self) -> dict:
        return {
            "settings": self.settings,
            "state": dict(_last_state),
            "neutral": list(NEUTRAL),
        }

    async def set_settings(self, patch: dict) -> dict:
        if isinstance(patch, dict):
            was_enabled = bool(self.settings.get("enabled", True))
            for key in DEFAULTS:
                if key in patch:
                    self.settings[key] = patch[key]
            self._sanitize()
            self._save()
            now_enabled = bool(self.settings.get("enabled", True))
            # К экрану не прикасаемся: цикл сам плавно сведёт точку белого
            # к новой цели (к нейтральной — при выключении).
            if was_enabled and not now_enabled:
                decky.logger.info("autowhite: выключено, возвращаю нейтральную точку")
            elif not was_enabled and now_enabled:
                decky.logger.info("autowhite: включено")
        return await self.get_state()

    async def check_update(self) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _check_update)

    async def reset_settings(self) -> dict:
        self.settings = dict(DEFAULTS)
        self._sanitize()
        self._save()
        return await self.get_state()
