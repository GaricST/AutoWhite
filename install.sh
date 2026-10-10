#!/usr/bin/env bash
# AutoWhite — установка и обновление на консоли одной командой.
#
# С GitHub (рабочий стол SteamOS → Konsole):
#   curl -fsSL https://raw.githubusercontent.com/GaricST/AutoWhite/main/install.sh | sh
#
# Из локальной папки с исходниками:
#   ./install.sh
#
# Удаление:
#   sudo rm -rf "$HOME/homebrew/plugins/AutoWhite" && sudo systemctl restart plugin_loader.service

set -e

PLUGIN="AutoWhite"
REPO="GaricST/AutoWhite"
BRANCH="${BRANCH:-main}"
PLUGINS_DIR="$HOME/homebrew/plugins"
DEST="$PLUGINS_DIR/$PLUGIN"

if [ "$(id -u)" -eq 0 ]; then
    echo "Не запускайте скрипт от root: sudo вызывается только там, где нужно." >&2
    exit 1
fi

if [ ! -d "$PLUGINS_DIR" ]; then
    echo "Каталог $PLUGINS_DIR не найден — сначала установите Decky Loader." >&2
    exit 1
fi

SELF_DIR=""
if [ -n "${BASH_SOURCE:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/plugin.json" ]; then
    SRC="$SELF_DIR"
    echo "==> Источник: локальная папка $SRC"
else
    echo "==> Скачиваю $REPO ($BRANCH)"
    URL="https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$URL" -o "$TMP/src.tar.gz"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$TMP/src.tar.gz" "$URL"
    else
        echo "Нужен curl или wget." >&2
        exit 1
    fi
    tar -xzf "$TMP/src.tar.gz" -C "$TMP"
    SRC="$TMP/$PLUGIN-$BRANCH"
    [ -f "$SRC/plugin.json" ] || { echo "В архиве нет plugin.json." >&2; exit 1; }
    echo "==> Источник: $REPO@$BRANCH"
fi

echo "==> Устанавливаю в $DEST"
sudo mkdir -p "$DEST"
sudo cp "$SRC/plugin.json" "$SRC/package.json" "$SRC/main.py" "$DEST/"
sudo rm -rf "$DEST/dist" "$DEST/assets"
sudo cp -R "$SRC/dist" "$SRC/assets" "$DEST/"
sudo chown -R root:root "$DEST"

echo "==> Перезапускаю Decky Loader"
sudo systemctl restart plugin_loader.service
sleep 4

echo "==> Готово: AutoWhite установлен. Плагин появится в боковой панели Decky."
echo "    Настройки: $HOME/homebrew/settings/$PLUGIN/"
echo "    Логи:      $HOME/homebrew/logs/$PLUGIN/"
