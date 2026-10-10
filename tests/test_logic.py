"""Тесты логики AutoWhite: расчёт точки белого, санитизация настроек.

Заглушка decky позволяет запускать тесты на любом компьютере:
    python3 tests/test_logic.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


S = {"enabled": True, "warmth": 1.0, "lux_dark": 15, "lux_bright": 250, "max_warm": 0.375}

print("точка белого:")
check("нет датчика -> None", aw._target_white(S, None), None)
check("темно (0 лк) -> самая тёплая", aw._target_white(S, 0), (0.3750, 0.3750))
check("ярко (500 лк) -> D65", aw._target_white(S, 500), aw.NEUTRAL)
check("порог 15 лк -> тёплая", aw._target_white(S, 15), (0.3750, 0.3750))
check("порог 250 лк -> D65", aw._target_white(S, 250), aw.NEUTRAL)
mid = aw._target_white(S, (15 + 250) / 2)
check("середина диапазона между тёплой и D65",
      aw.NEUTRAL[0] < mid[0] < 0.3750 and aw.NEUTRAL[1] < mid[1] < 0.3750, True)
check("warmth = 0 -> всегда D65", aw._target_white(dict(S, warmth=0.0), 0), aw.NEUTRAL)
check("warmth = 0.5 -> теплее D65", aw._target_white(dict(S, warmth=0.5), 0)[0] > aw.NEUTRAL[0], True)

print("плавное приближение:")
target = (0.3750, 0.3750)
cur = aw.NEUTRAL
seen = [cur]
for _ in range(200):
    nxt = aw._approach(cur, target)
    if nxt == cur:
        break
    check_ok = abs(nxt[0] - cur[0]) < 0.02 and abs(nxt[1] - cur[1]) < 0.02
    if not check_ok:
        failures.append("шаг слишком большой: %r -> %r" % (cur, nxt))
    seen.append(nxt)
    cur = nxt
check("дошли до цели за разумное время", cur, target)
check("переход занял больше одного шага (плавно, не рывком)", len(seen) > 5, True)
check("движение монотонное", all(seen[i][0] <= seen[i + 1][0] for i in range(len(seen) - 1)), True)
check("мёртвая зона: мелкую разницу догоняем сразу",
      aw._approach((0.3749, 0.3749), (0.3750, 0.3750)), (0.3750, 0.3750))
check("шаг не перескакивает цель", aw._approach((0.3740, 0.3740), (0.3760, 0.3760))[0] <= 0.3760, True)

print("версии и обновления:")
check("текущая версия из манифеста", aw._current_version(), "1.0.0")
check("1.0.0 новее 0.9.9", aw._version_key("1.0.0") > aw._version_key("0.9.9"), True)
check("1.0.1 новее 1.0.0", aw._version_key("1.0.1") > aw._version_key("1.0.0"), True)
check("v1.0.0 = 1.0.0", aw._version_key("v1.0.0") == aw._version_key("1.0.0"), True)
check("1.1.0 новее 1.1.0-beta", aw._version_key("1.1.0") > aw._version_key("1.1.0-beta"), True)
check("одинаковые версии равны", aw._version_key("1.0.0") == aw._version_key("1.0.0"), True)

print("санитизация:")
p = aw.Plugin()
p.settings.update({"warmth": 5, "max_warm": 9, "lux_dark": -10, "lux_bright": -50, "enabled": 0})
p._sanitize()
check("warmth зажат в 0..1", p.settings["warmth"], 1.0)
check("max_warm зажат в 0.32..0.42", p.settings["max_warm"], 0.42)
check("lux_dark не отрицательный", p.settings["lux_dark"], 0)
check("lux_bright больше lux_dark", p.settings["lux_bright"] > p.settings["lux_dark"], True)
check("enabled приводится к bool", p.settings["enabled"], False)

p2 = aw.Plugin()
p2.settings.update({"warmth": "мусор"})
p2._sanitize()
check("мусор -> значения по умолчанию", p2.settings["warmth"], aw.DEFAULTS["warmth"])
check("удалённых ключей нет", "source" in aw.DEFAULTS, False)
check("обязательные ключи на месте", sorted(aw.DEFAULTS), sorted(["enabled", "warmth", "lux_dark", "lux_bright", "max_warm"]))

print()
if failures:
    print("ПРОВАЛЕНО: %d" % len(failures))
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("Все проверки пройдены")
