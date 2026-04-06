#!/bin/bash
# ALT Booster — Install Script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/usr/local/share/altbooster"
ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="/usr/local/share/applications"
BIN="/usr/local/bin/altbooster"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'

# ── Обнаружение Niri (только в user-контексте) ──────────────────────────────
_detect_niri() {
    [[ "${XDG_SESSION_DESKTOP:-}" == "niri" ]] && return 0
    [[ "${XDG_CURRENT_DESKTOP:-}" == *niri* ]] && return 0
    return 1
}

# ── Добавить polkit agent в автостарт Niri (как user) ───────────────────────
_setup_niri_autostart() {
    local agent=""
    for _p in \
        "/usr/libexec/polkit-1/polkit-gnome-authentication-agent-1" \
        "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1" \
        "/usr/libexec/polkit-gnome-authentication-agent-1" \
        "/usr/libexec/xfce-polkit" \
        "/usr/lib/xfce4/polkit-xfce-authentication-agent-1" \
        "lxpolkit"
    do
        if [[ -x "$_p" ]] || command -v "$_p" >/dev/null 2>&1; then
            agent="$_p"
            break
        fi
    done

    if [[ -z "$agent" ]]; then
        echo -e "  ${YELLOW}⚠  Polkit agent не найден — добавьте его в автостарт Niri вручную.${NC}"
        return
    fi

    local cfg="$HOME/.config/niri/config.kdl"
    if [[ -f "$cfg" ]] && grep -qF "$agent" "$cfg"; then
        echo -e "  ${GREEN}✔${NC} Polkit agent уже в конфиге Niri."
        return
    fi

    printf '\n// Polkit authentication agent (добавлен ALT Booster installer)\nspawn-at-startup "%s"\n' "$agent" >> "$cfg"
    echo -e "  ${GREEN}✔${NC} Polkit agent добавлен в автостарт Niri: ${BOLD}$agent${NC}"
}

if [[ $EUID -ne 0 ]]; then
    DE="${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-unknown}}"
    if [[ ! "$DE" =~ [Gg][Nn][Oo][Mm][Ee] ]]; then
        echo -e "${YELLOW}⚠  ALT Booster разработан для GNOME.${NC}"
        echo -e "   Обнаружено окружение: ${BOLD}${DE}${NC}"
        echo -e "   Приложение использует GTK4 + libadwaita и не тестировалось на других DE."
        echo ""
        read -r -p "   Продолжить установку? [y/N] " _confirm
        [[ "$_confirm" =~ ^[Yy]$ ]] || exit 0
        echo ""
    fi

    # Передаём флаг --niri в root-часть скрипта
    _extra_args=()
    _detect_niri && _extra_args+=("--niri")

    echo -e "${YELLOW}🔒 Требуются права root...${NC}"
    if command -v pkexec >/dev/null 2>&1; then
        pkexec "$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")" "$@" "${_extra_args[@]}"
        _root_exit=$?
    else
        sudo "$0" "$@" "${_extra_args[@]}"
        _root_exit=$?
    fi

    # После root-установки настраиваем Niri в user-контексте
    if [[ $_root_exit -eq 0 ]] && _detect_niri; then
        echo ""
        echo -e "  ${YELLOW}▶${NC} Настройка Niri..."
        _setup_niri_autostart
    fi

    exit $_root_exit
fi

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════╗"
echo "  ║      ALT Booster  Installer      ║"
echo "  ╚══════════════════════════════════╝"
echo -e "${NC}"

step() { echo -ne "  ${YELLOW}▶${NC} $1... "; }
ok()   { echo -e "${GREEN}✔${NC}"; }
fail() { echo -e "${RED}✘ $1${NC}"; exit 1; }

# Установка зависимостей
step "Проверка зависимостей"
MISSING=()
python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" 2>/dev/null \
    || MISSING+=("python3-module-pygobject3" "libgtk4-gir")
python3 -c "import gi; gi.require_version('Adw','1'); from gi.repository import Adw" 2>/dev/null \
    || MISSING+=("libadwaita-gir")
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo -e "  ${YELLOW}Устанавливаю зависимости: ${MISSING[*]}${NC}"
    apt-get install -y "${MISSING[@]}" || fail "Не удалось установить зависимости"
fi
ok

# Установка polkit-gnome для Niri
if [[ " $* " == *" --niri "* ]]; then
    step "Установка polkit agent для Niri"
    _agent_found=false
    for _p in \
        "/usr/libexec/polkit-1/polkit-gnome-authentication-agent-1" \
        "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1" \
        "/usr/libexec/polkit-gnome-authentication-agent-1"
    do
        [[ -x "$_p" ]] && { _agent_found=true; break; }
    done
    if [[ "$_agent_found" == "false" ]]; then
        apt-get install -y polkit-gnome 2>/dev/null || true
    fi
    ok
fi

# Файлы приложения
step "Копирование файлов"
install -d "$APP_DIR"
cp -r "$SCRIPT_DIR/src/"* "$APP_DIR/"
chmod +x "$APP_DIR/altbooster.py"
ok

# Иконки
step "Установка иконок"
install -d "$ICON_DIR"
install -m 644 "$SCRIPT_DIR/icons/altbooster.svg" "$ICON_DIR/altbooster.svg"
install -d "/usr/local/share/icons/hicolor/scalable/apps"
for _svg in "$SCRIPT_DIR/icons/hicolor/scalable/apps/"*.svg; do
    install -m 644 "$_svg" "/usr/local/share/icons/hicolor/scalable/apps/"
done
install -d "/usr/local/share/icons/hicolor/scalable/devices"
for _svg in "$SCRIPT_DIR/icons/hicolor/scalable/devices/"*.svg; do
    install -m 644 "$_svg" "/usr/local/share/icons/hicolor/scalable/devices/"
done
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

# Справка (Yelp / Mallard)
step "Установка справки"
install -d "/usr/local/share/help/C/altbooster"
cp -r "$SCRIPT_DIR/help/C/"* "/usr/local/share/help/C/altbooster/"
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
