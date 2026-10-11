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
check("ниже таблицы — крайняя точка", aw._xy_for_cct(1000), aw._xy_for_cct(2400))
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

print("плавное приближение:")
target = aw._xy_for_cct(2700)
cur = aw.NEUTRAL
steps = 0
while cur != target and steps < 500:
    nxt = aw._approach(cur, target)
    if abs(nxt[0] - cur[0]) > 0.06:
        failures.append("шаг слишком большой: %r -> %r" % (cur, nxt))
    cur = nxt
    steps += 1
check("дошли до цели", cur, target)
check("переход плавный (несколько шагов)", steps > 5, True)
check("мёртвая зона догоняет сразу",
      aw._approach((0.3749, 0.3749), (0.3750, 0.3750)), (0.3750, 0.3750))

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

print("профили устройств:")
check("Steam Deck теплее Legion",
      aw.DEVICE_PROFILES[0][1]["max_warm_k"] < aw.DEVICE_PROFILES[1][1]["max_warm_k"], True)
check("пороги Steam Deck выше",
      aw.DEVICE_PROFILES[0][1]["lux_bright"] > aw.DEVICE_PROFILES[1][1]["lux_bright"], True)
name, profile = aw._device_profile()
check("профиль не ломается", isinstance(name, str) and isinstance(profile, dict), True)

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
      sorted(["enabled", "warmth", "lux_dark", "lux_bright", "max_warm_k", "device"]))

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
