"""Тесты логики AutoWhite: точка белого, датчики, версии, санитизация.

Заглушка decky позволяет запускать тесты на любом компьютере:
    python3 tests/test_logic.py
"""
import os
import sys
import types

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_SETTINGS_DIR = "/tmp/autowhite-test"
decky.DECKY_PLUGIN_DIR = PLUGIN_DIR


class _Log:
    def info(self, *a):
        pass

    def warning(self, *a):
        pass

    def error(self, *a):
        pass


decky.logger = _Log()
sys.modules["decky"] = decky

import main as aw  # noqa: E402

failures = []


def check(name, got, want, eps=1e-9):
    if isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(want, bool):
        ok = abs(got - want) <= eps
    else:
        ok = got == want
    if not ok:
        failures.append("%s: получено %r, ожидалось %r" % (name, got, want))
    print(("  ok   " if ok else "  FAIL ") + name)


print("точка белого по локусу:")
S = dict(aw.DEFAULTS, warmth=1.0, lux_dark=15.0, lux_bright=400.0, max_warm_k=3900)
check("нет датчика -> None", aw._target_white(S, None), None)
dark_point = aw._target_white(S, 0)
check("в темноте точка на тёплом конце", dark_point, aw._xy_for_cct(3900))
bright_point = aw._target_white(S, 5000)
check("при ярком свете точка D65", bright_point, aw.NEUTRAL)
mid = aw._target_white(S, 77)
check("середина между тёплой и нейтральной",
      aw.NEUTRAL[0] < mid[0] < dark_point[0], True)
check("warmth = 0 -> всегда D65", aw._target_white(dict(S, warmth=0.0), 0), aw.NEUTRAL)
check("точка лежит в допустимой области", aw._check_xy(dark_point), True)

print("локус и температуры:")
check("6500 К = D65", aw._xy_for_cct(6500), aw.NEUTRAL)
check("чем теплее, тем больше x", aw._xy_for_cct(2700)[0] > aw._xy_for_cct(3900)[0], True)
check("чем теплее, тем больше y", aw._xy_for_cct(2700)[1] > aw._xy_for_cct(3900)[1], True)
check("ниже таблицы — крайняя точка", aw._xy_for_cct(1000),
      (aw.CCT_TABLE[0][1], aw.CCT_TABLE[0][2]))
check("выше таблицы — D65", aw._xy_for_cct(20000), aw.NEUTRAL)
check("kelvin: warmth=0 -> 6500 К", round(aw._kelvin_mix(2700, 0.0, 0.0)), 6500)
check("kelvin: warmth=1, t=0 -> тёплый конец", round(aw._kelvin_mix(2700, 1.0, 0.0)), 2700)
check("kelvin: t=1 -> нейтраль", round(aw._kelvin_mix(2700, 1.0, 1.0)), 6500)
check("логарифмическая шкала: в комнате (150 лк) экран ещё теплее нейтрали",
      aw._target_white(S, 150)[0] > aw.NEUTRAL[0] + 0.015, True)
check("при 400 лк экран уже почти нейтрален",
      abs(aw._target_white(S, 400)[0] - aw.NEUTRAL[0]) < 0.005, True)
check("логарифмическая шкала: в темноте тёплый край",
      aw._target_white(S, 5)[0] > aw.NEUTRAL[0] + 0.05, True)

print("плавность (фильтр 2-го порядка, как в LegionToolkit):")
target = aw._xy_for_cct(2700)
smooth = aw._SmoothWhite(transition_s=2.0, max_rate=0.08, deadband=0.0005)
smooth.prime(aw.NEUTRAL)
smooth.set_target(target, 2.0)
prev = smooth.point
steps = 0
overshoot = 0.0
while abs(smooth.point[0] - target[0]) > 1e-4 and steps < 2000:
    nxt = smooth.advance(1.0 / 30.0)
    if abs(nxt[0] - prev[0]) > 0.08 * (1.0 / 30.0) * 1.05 + 1e-5:
        failures.append("превышено ограничение скорости: %r -> %r" % (prev, nxt))
    overshoot = max(overshoot, abs(nxt[0] - target[0]))
    prev = nxt
    steps += 1
check("дошли до цели", abs(smooth.point[0] - target[0]) < 1e-4, True)
check("переход плавный (десятки шагов)", steps > 20, True)
check("без перерегулирования", overshoot <= abs(target[0] - aw.NEUTRAL[0]) + 0.002, True)

slow = aw._SmoothWhite(transition_s=5.0)
slow.prime(aw.NEUTRAL)
slow.set_target(target, 5.0)
fast = aw._SmoothWhite(transition_s=1.0)
fast.prime(aw.NEUTRAL)
fast.set_target(target, 1.0)
for _ in range(30):
    slow.advance(1.0 / 30.0)
    fast.advance(1.0 / 30.0)
check("переход 5 с медленнее, чем 1 с", slow.point[0] < fast.point[0], True)

one = aw._SmoothWhite(transition_s=2.0)
one.prime(aw.NEUTRAL)
one.set_target(target, 2.0)
many = aw._SmoothWhite(transition_s=2.0)
many.prime(aw.NEUTRAL)
many.set_target(target, 2.0)
for _ in range(3):
    one.advance(0.1)
for _ in range(30):
    many.advance(0.01)
check("одинаковый переход при разном dt (в пределах тика)",
      abs(one.point[0] - many.point[0]) < 0.002, True)

tiny = aw._SmoothWhite(deadband=0.001)
tiny.prime((0.3128, 0.3292))
tiny.set_target((0.3130, 0.3294))
check("мёртвая зона игнорирует мелкую цель", tiny.target, (0.3128, 0.3292))

lux = aw._SmoothWhite()
first = lux.smooth_lux(0.0, 1.0)
second = lux.smooth_lux(1000.0, 1.0)
check("фильтр люксов не прыгает сразу", 0.0 < second < 1000.0, True)
check("фильтр люксов принимает первый замер", first, 0.0)
check("фильтр люксов переживает None", lux.smooth_lux(None, 1.0), second)

print("датчики:")
check("один датчик", aw._combine_lux([10.0]), 10.0)
check("двойной ALS: близкие показания усредняются", aw._combine_lux([10.0, 12.0]), 11.0)
check("разные шкалы: берём первый", aw._combine_lux([10.0, 1000.0]), 10.0)
check("пустой список", aw._combine_lux([]), None)
check("нули не ломают среднее", aw._combine_lux([0.0, 0.0]), 0.0)
check("None отбрасывается", aw._combine_lux([None, 5.0]), 5.0)
check("порядок устройств по номеру",
      aw._device_sort_key("/sys/bus/iio/devices/iio:device2")
      < aw._device_sort_key("/sys/bus/iio/devices/iio:device10"), True)
check("драйвер Steam Deck в списке", "ltrf216a" in aw.ALS_NAMES, True)

print("чтение числа из sysfs:")
check("целое", aw._read_number("/dev/null"), None)
tmp = "/tmp/autowhite-number"
open(tmp, "w").write("12.500000\n")
check("дробное", aw._read_number(tmp), 12.5)
open(tmp, "w").write("мусор")
check("мусор -> None", aw._read_number(tmp), None)
open(tmp, "w").write("inf")
check("inf -> None", aw._read_number(tmp), None)
os.remove(tmp)

print("чтение датчиков (sysfs):")
devdir = "/tmp/aw-device"
os.makedirs(devdir, exist_ok=True)


def sysfs(name, value):
    with open(os.path.join(devdir, name), "w") as handle:
        handle.write(str(value))


sysfs("name", "ltrf216a")
sysfs("in_illuminance_input", "120")
sysfs("in_illuminance_raw", "5000")
sysfs("in_illuminance_scale", "0.001")
check("обработанный канал приоритетен", aw._lux_from_device(devdir),
      (120.0, "in_illuminance_input"))
sysfs("in_illuminance_input", "0")
sysfs("in_illuminance_scale", "0.01")
value, source = aw._lux_from_device(devdir)
check("нулевой processed при живом raw -> raw*scale (как на Steam Deck)",
      round(value, 3), 50.0)
os.remove(os.path.join(devdir, "in_illuminance_input"))
value, source = aw._lux_from_device(devdir)
check("без обработанного канала -> raw*scale", round(value, 3), 50.0)
for entry in os.listdir(devdir):
    if "intensity" in entry:
        os.remove(os.path.join(devdir, entry))
sysfs("in_illuminance_input", "0")
sysfs("in_illuminance_raw", "0")
check("полная темнота -> 0 люкс", aw._lux_from_device(devdir),
      (0.0, "in_illuminance_input"))
sysfs("in_illuminance_input", "0")
sysfs("in_illuminance_raw", "300")
sysfs("in_illuminance_scale", "0.02")
value, _ = aw._lux_from_device(devdir)
check("сырой канал пересчитывается через scale", round(value, 2), 6.0)
for entry in ("in_illuminance_input", "in_illuminance_raw", "in_illuminance_scale"):
    path = os.path.join(devdir, entry)
    if os.path.exists(path):
        os.remove(path)
sysfs("name", "unknown_light")
sysfs("in_intensity_both_raw", "321")
value, source = aw._lux_from_device(devdir)
check("канал интенсивности работает и без scale (как в 0.2)",
      (round(value, 1), "in_intensity_both_raw" in source), (321.0, True))

print("профили устройств:")
check("Steam Deck теплее Legion",
      aw.DEVICE_PROFILES[0][1]["max_warm_k"] < aw.DEVICE_PROFILES[1][1]["max_warm_k"], True)
check("пороги Steam Deck выше",
      aw.DEVICE_PROFILES[0][1]["lux_bright"] > aw.DEVICE_PROFILES[1][1]["lux_bright"], True)
name, profile = aw._device_profile()
check("профиль не ломается", isinstance(name, str) and isinstance(profile, dict), True)

original_profile = aw._device_profile
aw._device_profile = lambda: ("jupiter", {"max_warm_k": 2700, "lux_dark": 30, "lux_bright": 900})
pdev = aw.Plugin()
pdev.settings.update({"device": "jupiter", "profile_version": 0, "max_warm_k": 3900})
pdev._apply_device_profile()
check("профиль применяется заново при новой версии", pdev.settings["max_warm_k"], 2700)
check("версия профиля записана", pdev.settings["profile_version"], aw.PROFILE_VERSION)
pdev2 = aw.Plugin()
pdev2.settings.update({"device": "jupiter", "profile_version": aw.PROFILE_VERSION,
                       "max_warm_k": 3900})
pdev2._apply_device_profile()
check("актуальный профиль не перезаписывает настройки", pdev2.settings["max_warm_k"], 3900)
aw._device_profile = original_profile

print("санитизация:")
p = aw.Plugin()
p.settings.update({"warmth": 5, "max_warm_k": 99999, "lux_dark": -10, "lux_bright": -50})
p._sanitize()
check("warmth зажат в 0..1", p.settings["warmth"], 1.0)
check("max_warm_k зажат в 2000..6500", p.settings["max_warm_k"], 6500.0)
check("lux_dark не отрицательный", p.settings["lux_dark"], 0.0)
check("lux_bright больше lux_dark", p.settings["lux_bright"] > p.settings["lux_dark"], True)

p2 = aw.Plugin()
p2.settings.update({"warmth": "мусор"})
p2._sanitize()
check("битое поле -> значение по умолчанию", p2.settings["warmth"], aw.DEFAULTS["warmth"])
check("остальные поля при этом целы", p2.settings["lux_dark"], aw.DEFAULTS["lux_dark"])

p3 = aw.Plugin()
p3.settings.update({"enabled": "false"})
p3._sanitize()
check('строка "false" -> выключено', p3.settings["enabled"], False)
p4 = aw.Plugin()
p4.settings.update({"enabled": "true"})
p4._sanitize()
check('строка "true" -> включено', p4.settings["enabled"], True)
check("обязательные ключи на месте", sorted(aw.DEFAULTS),
      sorted(["enabled", "warmth", "lux_dark", "lux_bright", "max_warm_k", "smooth_s",
              "device", "profile_version"]))

print("версии и обновления:")
check("текущая версия из манифеста", aw._current_version(), "1.0.0")
check("1.0.0 новее 0.9.9", aw._version_key("1.0.0") > aw._version_key("0.9.9"), True)
check("1.0.1 новее 1.0.0", aw._version_key("1.0.1") > aw._version_key("1.0.0"), True)
check("v1.0.0 = 1.0.0", aw._version_key("v1.0.0") == aw._version_key("1.0.0"), True)
check("1.1.0 новее 1.1.0-beta", aw._version_key("1.1.0") > aw._version_key("1.1.0-beta"), True)
check("текстовый тег распознаётся",
      aw._version_key("AutoWhite v1.2.3") == aw._version_key("1.2.3"), True)

print()
if failures:
    print("ПРОВАЛЕНО: %d" % len(failures))
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("Все проверки пройдены")
