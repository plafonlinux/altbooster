#!/bin/bash
# ALT Booster — Install Script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/usr/local/share/altbooster"
ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="/usr/local/share/applications"
BIN="/usr/local/bin/altbooster"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'

if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}🔒 Требуются права root...${NC}"
    sudo "$0" "$@"
    exit $?
fi

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════╗"
echo "  ║      ALT Booster  Installer      ║"
echo "  ╚══════════════════════════════════╝"
echo -e "${NC}"

step() { echo -ne "  ${YELLOW}▶${NC} $1... "; }
ok()   { echo -e "${GREEN}✔${NC}"; }
fail() { echo -e "${RED}✘ $1${NC}"; exit 1; }

# Проверка зависимостей
step "Проверка зависимостей"
MISSING=()
python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" 2>/dev/null \
    || MISSING+=("python3-module-pygobject3 libgtk4-gir")
python3 -c "import gi; gi.require_version('Adw','1'); from gi.repository import Adw" 2>/dev/null \
    || MISSING+=("libadwaita-gir")
[[ ${#MISSING[@]} -gt 0 ]] && fail "Установите: sudo apt-get install ${MISSING[*]}"
ok

# Файлы приложения
step "Копирование файлов"
install -d "$APP_DIR"
install -m 755 "$SCRIPT_DIR/src/altbooster.py" "$APP_DIR/altbooster.py"
ok

# Иконка
step "Установка иконки"
install -d "$ICON_DIR"
install -m 644 "$SCRIPT_DIR/icons/altbooster.svg" "$ICON_DIR/altbooster.svg"
gtk-update-icon-cache /usr/local/share/icons/hicolor 2>/dev/null || true
ok

# .desktop
step "Создание ярлыка"
install -d "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/altbooster.desktop" << DESKTOP
[Desktop Entry]
Name=ALT Booster
GenericName=System Maintenance
Comment=Утилита обслуживания системы ALT Linux
Exec=$BIN
Icon=altbooster
Terminal=false
Type=Application
Categories=System;Settings;
Keywords=system;maintenance;clean;btrfs;trim;apt;flatpak;
StartupNotify=true
StartupWMClass=ru.altbooster.app
DESKTOP
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
ok

# Команда в PATH
step "Создание команды altbooster"
cat > "$BIN" << 'BINEOF'
#!/bin/bash
exec python3 /usr/local/share/altbooster/altbooster.py "$@"
BINEOF
chmod +x "$BIN"
ok

echo ""
echo -e "  ${GREEN}${BOLD}✅ ALT Booster успешно установлен!${NC}"
echo ""
echo -e "  Запуск: ${BOLD}altbooster${NC}  или через меню приложений GNOME"
echo ""
