# Changelog

## 1.0.0 — 10.10.2026

Первый публичный выпуск. / First public release.

**Возможности / Features**

- Автоматическая подстройка точки белого по датчику освещённости (ALS) через виртуальную
  точку белого gamescope — `GAMESCOPE_DISPLAY_VIRTUAL_WHITE` (CIE 1931 x/y).
  / Ambient-light adaptive white point driven through gamescope's virtual white point.
- Плавное сглаживание перехода: тик 0.2 с, опрос датчика раз в 1 с, мёртвая зона 0.0005 —
  без рывков и ступенек: критически демпфированный фильтр 2-го порядка (как автояркость
  в LegionToolkit) — скорость, мёртвая зона, фильтр датчика, шаг по реальному времени,
  настройка `smooth_s` (5 с по умолчанию).
  / No jumps: a critically damped second-order filter (same as auto-brightness in
  LegionToolkit) — velocity, dead band, sensor filter, real-time step, `smooth_s` setting.
- Применение сразу при старте консоли; при выключении возвращается нейтральная D65.
  / Applied at boot; disabling restores neutral D65.
- Панель: тумблер, ползунок «Холоднее — Теплее», статус (освещённость и текущая точка).
  / Panel: toggle, Cooler — Warmer slider, live status.
- Русский и английский интерфейс — автоматически по языку системы.
  / Russian and English UI, picked from the system language.
- Самодостаточность: нужны только Decky Loader, `xprop`, `runuser`, gamescope и ALS.
  / Self-contained: Decky Loader, `xprop`, `runuser`, gamescope, ALS.
- Профиль устройства по DMI: Steam Deck — тёплый конец 2700 К и пороги 30–900 лк,
  Legion Go — 3900 К и 15–400 лк.
  / Per-device profile from DMI: Steam Deck — 2700 K warm end, 30–900 lx;
  Legion Go — 3900 K, 15–400 lx.
- Точка белого считается по локусу чёрного тела с интерполяцией по mired (раньше — по
  диагонали x = y, что давало зеленоватый оттенок); освещённость переводится в положение
  на кривой по логарифмической шкале.
  / The white point now follows the black-body locus with mired interpolation (the x = y
  diagonal drifted green), and illuminance is mapped logarithmically.
- Сырые каналы датчиков больше не выдаются за люксы: используются обработанный канал,
  scale/offset драйвера или честное «датчик не откалиброван».
  / Raw sensor channels are no longer treated as lux: processed channel, driver scale/offset,
  or an explicit "sensor not calibrated".
- Пауза после ошибок записи (5/15/60 с), возврат нейтральной точки при выключении плагина
  и при выгрузке, атомарное сохранение настроек, диагностика в панели (статус, датчик,
  текущая и целевая точка).
  / Backoff after write errors (5/15/60 s), neutral point restored on disable and unload,
  atomic settings writes, panel diagnostics (status, sensor, current and target point).
- Самовосстановление: точка белого сверяется каждые 5 с, поэтому сброс после перезагрузки
  консоли или чужое вмешательство исправляются автоматически.
  / Self-healing: the white point is verified every 5 s, so a reset after a reboot or an
  external change is corrected automatically.
- Кнопка **«Проверить обновление»** в панели: спрашивает у GitHub последний релиз.
  / **Check for updates** button in the panel: asks GitHub for the latest release.
- Переносимость: пользователь сессии берётся из `DECKY_USER` (не жёстко «deck»),
  X-дисплей определяется автоматически, датчик освещённости ищется по каналу
  `in_illuminance_*` и по известным драйверам света (Steam Deck — `ltrf216a` и др.),
  выбранный датчик пишется в лог.
  / Portability: the session user comes from `DECKY_USER` (not a hard-coded `deck`),
  the X display is auto-detected, and the light sensor is found by its `in_illuminance_*`
  channel or by known light-sensor drivers (Steam Deck — `ltrf216a` etc.); the chosen
  sensor is logged.

**Установка / Install**

```bash
curl -fsSL https://raw.githubusercontent.com/GaricST/AutoWhite/main/install.sh | sh
```

**Проверено на / Verified on:** Lenovo Legion Go 2 (SteamOS), датчик `als` в
`/sys/bus/iio/devices/iio:device1`.

**Зависимости / Requirements:** Decky Loader, gamescope, `xprop`, `runuser`.

**Лицензия / License:** MIT.
