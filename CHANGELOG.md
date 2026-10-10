# Changelog

## 1.0.0 — 10.10.2026

Первый публичный выпуск. / First public release.

**Возможности / Features**

- Автоматическая подстройка точки белого по датчику освещённости (ALS) через виртуальную
  точку белого gamescope — `GAMESCOPE_DISPLAY_VIRTUAL_WHITE` (CIE 1931 x/y).
  / Ambient-light adaptive white point driven through gamescope's virtual white point.
- Плавное сглаживание перехода: тик 0.2 с, опрос датчика раз в 1 с, мёртвая зона 0.0005 —
  без рывков и ступенек. / Smooth exponential transitions — no jumps.
- Применение сразу при старте консоли; при выключении возвращается нейтральная D65.
  / Applied at boot; disabling restores neutral D65.
- Панель: тумблер, ползунок «Холоднее — Теплее», статус (освещённость и текущая точка).
  / Panel: toggle, Cooler — Warmer slider, live status.
- Русский и английский интерфейс — автоматически по языку системы.
  / Russian and English UI, picked from the system language.
- Самодостаточность: нужны только Decky Loader, `xprop`, `runuser`, gamescope и ALS.
  / Self-contained: Decky Loader, `xprop`, `runuser`, gamescope, ALS.

**Установка / Install**

```bash
curl -fsSL https://raw.githubusercontent.com/GaricST/AutoWhite/main/install.sh | sh
```

**Проверено на / Verified on:** Lenovo Legion Go 2 (SteamOS), датчик `als` в
`/sys/bus/iio/devices/iio:device1`.

**Зависимости / Requirements:** Decky Loader, gamescope, `xprop`, `runuser`.

**Лицензия / License:** MIT.
