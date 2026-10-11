![AutoWhite — ambient-light adaptive white point for gamescope](img/banner-en.png)

# AutoWhite

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/GaricST/AutoWhite/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-SteamOS%20%7C%20gamescope-informational.svg)](#requirements)
[![Decky Loader](https://img.shields.io/badge/Decky-Loader-9147ff.svg)](https://github.com/SteamDeckHomebrew/decky-loader)

**English** · [Русский](README.ru.md)

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that keeps the
**display white point in step with the ambient light**: warmer when the room is dark,
neutral in bright light. Brightness is never touched — only the colour temperature of white.

The idea is the same as Apple's **True Tone** on the iPhone and iPad: the display's white
balance follows the light in the room. AutoWhite brings that behaviour to SteamOS handhelds
through gamescope's virtual white point — no schedule, no manual switching.

## What it does

The plugin reads the handheld's ambient light sensor (ALS) and drives gamescope's virtual
white point atom `GAMESCOPE_DISPLAY_VIRTUAL_WHITE` (CIE 1931 x/y):

| Illuminance | White point |
|---|---|
| below 15 lx | warmest (up to 0.3750, 0.3750) |
| 15 … 250 lx | smooth blend from warm to neutral |
| above 250 lx | neutral D65 (0.3128, 0.3292) |

## Features

- **Sensor-driven** — the ambient light sensor sets the white point automatically; no
  schedule, no manual switching
- **Warm in the dark, neutral in bright light** — the point moves with a critically damped
  second-order filter (the same approach as auto-brightness in LegionToolkit): the transition
  takes `smooth_s` seconds (1.2 by default — change it in `settings.json` if you want it
  slower or faster), speed is limited, there is a dead band and an
  input filter on the sensor, and the step is computed from real time — so flipping a light
  switch produces a smooth glide, not a jump
- **Applies immediately at boot** — Decky loads the plugin at startup and the white point
  is set before the panel is ever opened
- **Enable / disable toggle** — switching the plugin off restores the neutral D65 point
- **Cooler — Warmer slider** — how warm the dark end becomes (0…100 %)
- **Greener — Redder slider** — shifts the point off the black-body locus (Duv), so you can
  go for a pink or a green tint when the panel needs it; the middle position keeps the pure
  locus
- **Live status** — current illuminance and the current white point
- **Restores itself if the point is changed** — the atom is read back every 5 s, so a reset
  by gamescope after a reboot is corrected automatically
- **Update check** — a button in the panel asks GitHub for the latest release
- **English / Russian UI**, chosen automatically from the system language
- **Self-contained** — no daemons, no background network requests, no dependency on other
  plugins (the update check runs only when you press the button)

## How it works

| What | Where |
|---|---|
| Ambient light | `/sys/bus/iio/devices/iio:device*` where `name` is `als`; channels `in_illuminance_raw` / `in_intensity_both_raw` |
| White point | gamescope root atom `GAMESCOPE_DISPLAY_VIRTUAL_WHITE` — two CARDINALs holding the float32 bits of x and y |
| Writing it | `runuser -u deck -- env DISPLAY=:0 xprop -root -f GAMESCOPE_DISPLAY_VIRTUAL_WHITE 32c -set … "bits_x, bits_y"` |
| Verification | every 5 s the atom is read back; a difference over 0.001 means someone else changed it, and the plugin glides back to its target |
| Settings | `$DECKY_PLUGIN_SETTINGS_DIR/settings.json` |
| Logs | `$DECKY_PLUGIN_LOG_DIR` (one file per plugin start) |

Decky plugins run as **root**, while the gamescope X server belongs to user `deck` — a plain
`xprop` from root fails with `Authorization required`. That is why the atom is written
through `runuser -u deck`.

Mapping: the warm end is set as a colour **temperature** (`max_warm_k`) and its xy is taken
from the black-body locus with interpolation in mired, so intermediate points stay on a
neutral tint instead of drifting green — interpolating along the `x = y` diagonal does not
follow any real illuminant. The **Cooler — Warmer** slider scales that warm end, and
illuminance is mapped on a **logarithmic** scale (`t = log(lux/dark) / log(bright/dark)`),
which matches how the eye perceives light.

## Panel

| Item | What it does |
|---|---|
| **Enabled** | master switch |
| **Cooler — Warmer** | 0…100 % — the maximum warmth reached in the dark |
| **State** | current illuminance and the current white point |
| **Updates** | **Check for updates** — asks GitHub for the latest release and shows the result |

## Installation

### One command (SteamOS Desktop → Konsole)

```bash
curl -fsSL https://raw.githubusercontent.com/GaricST/AutoWhite/main/install.sh | sh
```

The script downloads the current version, copies it into `~/homebrew/plugins/AutoWhite`
and restarts Decky. Re-run the same command to update.

### Through Decky

1. Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader).
2. Download `AutoWhite-<version>.zip` from Releases.
3. Decky → **Settings** → **General** → **Install plugin from file** → pick the ZIP.
4. Open **AutoWhite** in the Decky sidebar.

### From a checkout

```bash
./install.sh
```

The plugin registers the `root` flag, because it writes an X property and reads sysfs.

## Requirements

- Decky Loader
- A gamescope session (SteamOS / SteamOS-like handheld)
- `xprop` and `runuser` (present on SteamOS)
- An ambient light sensor exposed through IIO — without one the plugin simply idles and
  shows "sensor not found"

## Compatibility

| Device | Ambient light sensor | Status |
|---|---|---|
| Lenovo Legion Go 2 (SteamOS) | yes | **verified** |
| Lenovo Legion Go | yes | expected to work |
| Steam Deck LCD / OLED | yes — Valve lists an ALS in the specs of both models | expected to work; the driver differs (`ltrf216a`), so the sensor is found by its illuminance channel, not by a fixed name |
| ROG Ally / Ally X | not listed in the specs | installs and runs, but there is nothing to read — the panel shows "sensor not found" and the screen is left alone |
| Other SteamOS handhelds | depends on the model | works if `/sys/bus/iio/devices` exposes an `als` device |

The plugin never breaks the screen: without a sensor it simply does nothing. It is not tied
to a particular user name or display either — the session user comes from Decky
(`DECKY_USER`) and the X display is detected (`:0`, `:1`, …).

Steam Deck notes: Valve lists an ALS for both models — [LCD](https://www.steamdeck.com/en/tech/deck)
and [OLED](https://www.steamdeck.com/en/tech/oled) (dual ALS on OLED). The kernel driver is
`ltrf216a`, whose `in_illuminance_input` is exactly what SteamOS itself uses for
auto-brightness ([LKML patch](https://lkml.org/lkml/2022/8/12/262)); owners confirm the sensor
is visible on both LCD and OLED as `/sys/bus/iio/devices/iio*/in_illuminance_*`
([Jovian-NixOS #271](https://github.com/Jovian-Experiments/Jovian-NixOS/issues/271)).
gamescope handles the `GAMESCOPE_DISPLAY_VIRTUAL_WHITE` atom in its shared code
([steamcompmgr.cpp](https://github.com/ValveSoftware/gamescope/blob/master/src/steamcompmgr.cpp)),
so the same mechanism is available on the Deck.

The device is detected by DMI and gets its own profile, because the same white point
looks different on different panels and the light sensors report different scales:

| Device | Warm end | Thresholds |
|---|---|---|
| Steam Deck (Jupiter LCD / Galileo OLED) | 2300 K | 30 … 900 lx |
| Legion Go / Legion Go 2 (83E1 / 83N0) | 3200 K | 15 … 400 lx |

The warm end can be changed in `settings.json` (`max_warm_k`, 1500–6500 K) if you want it
warmer or cooler than the profile default.

The profile is applied once per device (recorded as `device` in `settings.json`) and never
overrides your own values afterwards.

Sensors differ between devices, so the plugin accepts any IIO device with an
`in_illuminance_*` channel, and also knows common light-sensor drivers
(`als`, `ltrf216a`, `ltr559`, `opt3001`, `apds9960`, `cm32181`, `stk3310`, …) by their
intensity channels: `in_intensity_both_raw`, `in_intensity_clear_raw`, `in_intensity_raw`.

To see what your device exposes:

```bash
for d in /sys/bus/iio/devices/iio:device*; do
  echo "$d: $(cat "$d/name" 2>/dev/null)"
  ls "$d" | grep -E "illuminance|intensity" | sed 's/^/    /'
done
```

The plugin logs the sensor it picked: `autowhite: датчик освещённости <device>/<channel>`.
If your sensor reports on a different scale, adjust `lux_dark` / `lux_bright` in
`settings.json`.

## Repository layout

```
plugin.json          Decky manifest (name, version, entry points)
main.py              backend: sensor read, white-point maths, gamescope write, panel API
dist/index.js        frontend bundle (Decky panel UI), committed as-is — no build step
tests/test_logic.py  unit tests: white-point maths, smoothing, settings sanitisation
assets/icon.svg      plugin icon
img/banner-en.png    header image (English) for this README
img/banner-ru.png    header image (Russian) for README.ru.md
install.sh           install / update from this folder on the device
logs/                sample logs from the development device
```

## Tests

```bash
python3 tests/test_logic.py
```

The tests stub the `decky` module, so they run anywhere — no handheld required. They cover
the white-point maths (dark/bright thresholds, D65, warmth scaling), the exponential
smoothing step and settings sanitisation.

## Similar projects

Checked on 2026-10-10 — no direct equivalent was found. The closest projects:

| Project | What it does | How AutoWhite differs |
|---|---|---|
| SteamOS **Night Mode** (built-in) | warms the screen manually or on a schedule | no ambient light sensor; the user sets it by hand |
| [vibrantDeck](https://github.com/libvibrant/vibrantDeck) | saturation and gamma | no white point, no sensor |
| [SDH-AutoNightMode](https://github.com/KingShibe/SDH-AutoNightMode) | night mode by local sunset/sunrise | deprecated (SteamOS 3.3 added it natively); schedule, not sensor |
| [decky-plugin-from-dusk-till-dawn](https://github.com/IvanKarpan/decky-plugin-from-dusk-till-dawn) | night-mode scheduler by sunset/sunrise | schedule, not sensor |
| [steamdeck-gamma](https://github.com/sweetbbak/steamdeck-gamma) | gamma and red-shift night mode | gamma/red shift, manual |
| [LeGo2BrightnessFix](https://github.com/Rayekkk/LeGo2BrightnessFix) | brightness slider fix and panel calibration for the Legion Go 2 | brightness and per-panel calibration; no ALS-driven white point |
| [LegionGo2Companion](https://github.com/Rayekkk/LegionGo2Companion) | TDP, brightness, HDR, lighting, gyro in one overlay | no ambient-light-driven white point |
| HHD (Handheld Daemon) | TDP, RGB, buttons, device configuration | no display white point control |

AutoWhite is the only one that closes the loop: **ALS → white point, continuously and
automatically**, without a schedule and without user input.

## Notes and limitations

- If Steam's own night mode is enabled, both effects add up — AutoWhite does not read
  `GAMESCOPE_COLOR_NIGHT_MODE`.
- `settings.json` keeps only known keys: anything left over from older builds is ignored
  on load and dropped on the next save.
- Brightness is deliberately untouched: use the system slider or a brightness plugin.
- The sensor must be exposed to SteamOS. On some firmware revisions it may report 0 lx
  until the panel is active.

## Credits

The idea was born while working on Legion Go 2 brightness plugins; thanks to the
[LeGo2BrightnessFix](https://github.com/Rayekkk/LeGo2BrightnessFix) project for proving
that gamescope atoms can be written from a Decky plugin.

## License

MIT — see [LICENSE](LICENSE).
