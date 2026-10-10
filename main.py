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
SENSOR_INTERVAL = 1.0           # период опроса датчика, с
TICK = 0.2                      # период плавного шага, с
SMOOTH = 0.15                   # доля пути до цели за шаг (τ ≈ 1.2 с)
DEADBAND = 0.0005               # мёртвая зона: мельче — не трогаем экран
SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

DEFAULTS = {
    "enabled": True,
    "warmth": 1.0,          # насколько теплее в темноте (0..1)
    "lux_dark": LUX_DARK,
    "lux_bright": LUX_BRIGHT,
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

    async def reset_settings(self) -> dict:
        self.settings = dict(DEFAULTS)
        self._sanitize()
        self._save()
        return await self.get_state()
