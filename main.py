import asyncio
import glob
import json
import os
import struct
import subprocess
import time

import decky

ATOM = "GAMESCOPE_DISPLAY_VIRTUAL_WHITE"
NEUTRAL = (0.3128, 0.3292)      # D65
LUX_DARK = 15.0                 # темнее — самая тёплая
LUX_BRIGHT = 250.0              # светлее — нейтральная
STEP = 0.004                    # плавность: шаг за проход
INTERVAL = 3.0                  # период опроса датчика
SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

DEFAULTS = {
    "enabled": True,
    "warmth": 1.0,          # насколько теплее в темноте (0..1)
    "lux_dark": LUX_DARK,
    "lux_bright": LUX_BRIGHT,
    "source": "sensor",     # sensor | schedule
    "night_from": 21,       # час начала «тёплого» расписания
    "night_to": 7,          # час окончания
    "max_warm": 0.3750,
}

_last_state = {"lux": None, "white": None, "applied": False, "error": ""}
_last_white = None


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_als_lux():
    """Освещённость в люксах или None, если датчика нет."""
    for directory in sorted(glob.glob("/sys/bus/iio/devices/iio:device*")):
        try:
            with open(os.path.join(directory, "name")) as f:
                if f.read().strip() != "als":
                    continue
        except OSError:
            continue
        for channel in ("in_illuminance_raw", "in_intensity_both_raw"):
            value = _read_int(os.path.join(directory, channel))
            if value is not None:
                return float(value)
    return None


def _xprop_env():
    """Окружение для xprop: плагин работает от root, поэтому PATH и DISPLAY
    берём явно — иначе xprop не находится или не открывает дисплей."""
    env = dict(os.environ)
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    return env


def _write_white(point) -> bool:
    """Пишет виртуальную точку белого в gamescope (x, y в CIE 1931)."""
    bits = [struct.unpack("<I", struct.pack("<f", float(v)))[0] for v in point]
    # Плагин работает от root, а X-сервер gamescope принадлежит пользователю
    # deck: без runuser xprop отвечает «Authorization required».
    try:
        subprocess.run(
            ["runuser", "-u", "deck", "--", "env", "DISPLAY=:0",
             "xprop", "-root", "-f", ATOM, "32c", "-set", ATOM,
             f"{bits[0]}, {bits[1]}"],
            capture_output=True, timeout=5, check=True)
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


def _target_white(settings, lux):
    """Куда вести точку белого: (x, y) или None, если данных нет."""
    warmth = max(0.0, min(1.0, float(settings.get("warmth", 1.0))))
    warm = (NEUTRAL[0] + (float(settings.get("max_warm", 0.375)) - NEUTRAL[0]) * warmth,
            NEUTRAL[1] + (float(settings.get("max_warm", 0.375)) - NEUTRAL[1]) * warmth)
    if settings.get("source") == "schedule":
        hour = time.localtime().tm_hour
        night_from = int(settings.get("night_from", 21))
        night_to = int(settings.get("night_to", 7))
        night = hour >= night_from or hour < night_to
        t = 0.0 if night else 1.0
    else:
        if lux is None:
            return None
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
                self.settings.update(stored)
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
            self.settings["night_from"] = max(0, min(23, int(self.settings.get("night_from", 21))))
            self.settings["night_to"] = max(0, min(23, int(self.settings.get("night_to", 7))))
        except (TypeError, ValueError):
            self.settings.update(DEFAULTS)
        if self.settings.get("source") not in ("sensor", "schedule"):
            self.settings["source"] = "sensor"

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
        while True:
            try:
                if os.getppid() == 1:
                    decky.logger.warning("autowhite: Decky перезапущен, выходим")
                    os._exit(0)
                settings = self.settings
                if not settings.get("enabled", True):
                    await asyncio.sleep(2.0)
                    continue
                lux = await loop.run_in_executor(None, _read_als_lux)
                _last_state["lux"] = round(lux) if lux is not None else None
                want = _target_white(settings, lux)
                if want is not None:
                    cur = _last_white
                    if cur is None or abs(want[0] - cur[0]) > 0.02:
                        step = want          # большая разница — применяем сразу
                    else:
                        dx = max(-STEP, min(STEP, want[0] - cur[0]))
                        dy = max(-STEP, min(STEP, want[1] - cur[1]))
                        step = (round(cur[0] + dx, 4), round(cur[1] + dy, 4))
                    if cur is None or step != cur:
                        if await loop.run_in_executor(None, _write_white, step):
                            if _last_state["white"] != step:
                                decky.logger.info(
                                    f"autowhite: точка белого {step[0]:.3f} / "
                                    f"{step[1]:.3f} (люкс {_last_state['lux']})")
                            _last_white = step
                            _last_state["white"] = step
                            _last_state["applied"] = True
                            _last_state["error"] = ""
                await asyncio.sleep(INTERVAL)
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
            f"autowhite: старт (источник {self.settings.get('source')}, "
            f"теплота {self.settings.get('warmth')}, "
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
        global _last_white
        loop = asyncio.get_running_loop()
        if isinstance(patch, dict):
            was_enabled = bool(self.settings.get("enabled", True))
            for key in DEFAULTS:
                if key in patch:
                    self.settings[key] = patch[key]
            self._sanitize()
            self._save()
            now_enabled = bool(self.settings.get("enabled", True))
            if not now_enabled:
                # Выключили — возвращаем нейтральную точку сразу.
                _last_white = NEUTRAL
                _last_state["white"] = NEUTRAL
                await loop.run_in_executor(None, _write_white, NEUTRAL)
                if was_enabled:
                    decky.logger.info("autowhite: выключено, точка белого нейтральная")
            elif not was_enabled:
                # Включили — применяем немедленно, не ждём цикла.
                lux = await loop.run_in_executor(None, _read_als_lux)
                want = _target_white(self.settings, lux)
                if want is not None and await loop.run_in_executor(None, _write_white, want):
                    _last_white = want
                    _last_state["white"] = want
                    _last_state["lux"] = round(lux) if lux is not None else None
                    decky.logger.info(
                        f"autowhite: включено, точка белого {want[0]:.3f} / "
                        f"{want[1]:.3f} (люкс {_last_state['lux']})")
        return await self.get_state()

    async def reset_settings(self) -> dict:
        self.settings = dict(DEFAULTS)
        self._sanitize()
        self._save()
        return await self.get_state()
