#!/usr/bin/env bash
# AutoWhite — установка и обновление плагина на консоли одной командой.
#
# Запускать на устройстве из папки с исходниками:
#   ./install.sh
#
# Удаление:
#   sudo rm -rf "$HOME/homebrew/plugins/AutoWhite" && sudo systemctl restart plugin_loader.service

set -e

PLUGIN="AutoWhite"
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/homebrew/plugins/$PLUGIN"

if [ "$(id -u)" -eq 0 ]; then
    echo "Не запускайте скрипт от root: sudo вызывается только там, где нужно." >&2
    exit 1
fi

if [ ! -d "$HOME/homebrew/plugins" ]; then
    echo "Каталог $HOME/homebrew/plugins не найден — сначала установите Decky Loader." >&2
    exit 1
fi

echo "==> Копирую $PLUGIN в $DEST"
sudo mkdir -p "$DEST"
sudo cp "$SRC/plugin.json" "$SRC/package.json" "$SRC/main.py" "$DEST/"
sudo rm -rf "$DEST/dist" "$DEST/assets"
sudo cp -R "$SRC/dist" "$SRC/assets" "$DEST/"
sudo chown -R root:root "$DEST"

echo "==> Перезапускаю Decky Loader"
sudo systemctl restart plugin_loader.service
sleep 4

echo "==> Готово. Логи: $HOME/homebrew/logs/$PLUGIN/"
