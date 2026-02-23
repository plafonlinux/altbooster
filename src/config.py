"""
config.py — конфигурация, состояние сессии и данные приложений/задач.

Состояние хранится в ~/.config/altbooster/state.json и загружается
один раз при старте через load_state(). Все изменения сохраняются
немедленно через state_set().
"""

import json
import subprocess
from pathlib import Path


# ── Пути к файлам конфигурации ────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / ".config" / "altbooster"
CONFIG_FILE = CONFIG_DIR / "window.json"
STATE_FILE  = CONFIG_DIR / "state.json"


# ── Пути к кэшу DaVinci Resolve по умолчанию ─────────────────────────────────

DV_CACHE_DEFAULT = "/mnt/datassd/DaVinci Resolve/Work Folders/CacheClip"
DV_PROXY_DEFAULT = "/mnt/datassd/DaVinci Resolve/Work Folders/ProxyMedia"

# Gsettings-схемы — чтобы не повторять строки по всему коду
GSETTINGS_MUTTER      = "org.gnome.mutter"
GSETTINGS_KEYBINDINGS = "org.gnome.desktop.wm.keybindings"


# ── Состояние сессии ──────────────────────────────────────────────────────────

_state: dict = {}


def load_state() -> None:
    """Загружает сохранённое состояние из файла. Вызывается один раз при старте."""
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
    except (OSError, json.JSONDecodeError):
        _state = {}


def save_state() -> None:
    """Записывает текущее состояние на диск."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except OSError:
        pass


def state_get(key: str, default=None):
    """Читает значение из состояния сессии."""
    return _state.get(key, default)


def state_set(key: str, value) -> None:
    """Записывает значение в состояние и сохраняет на диск."""
    _state[key] = value
    save_state()


def reset_state() -> None:
    """Полностью сбрасывает сохранённое состояние."""
    _state.clear()
    save_state()


# ── Пути к кэшу DaVinci (с учётом пользовательских настроек) ─────────────────

def get_dv_cache() -> str:
    return state_get("dv_cache_path") or DV_CACHE_DEFAULT


def get_dv_proxy() -> str:
    return state_get("dv_proxy_path") or DV_PROXY_DEFAULT


# ── Определение файловой системы ──────────────────────────────────────────────

def is_btrfs() -> bool:
    """Возвращает True если в системе есть хотя бы один Btrfs-раздел."""
    try:
        result = subprocess.run(
            ["findmnt", "-t", "btrfs", "-n", "-o", "TARGET"],
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except OSError:
        return False


# ── Задачи обслуживания ───────────────────────────────────────────────────────

TASKS: list[dict] = [
    {
        "id": "apt",
        "icon": "user-trash-symbolic",
        "label": "Очистка APT",
        "desc": "apt-get clean — удаляет кэш пакетов",
        "cmd": ["apt-get", "clean"],
    },
    {
        "id": "flatpak",
        "icon": "application-x-addon-symbolic",
        "label": "Уборка Flatpak",
        "desc": "Удаляет неиспользуемые runtime-библиотеки",
        "cmd": ["flatpak", "uninstall", "--unused", "-y"],
    },
    {
        "id": "journal",
        "icon": "document-open-recent-symbolic",
        "label": "Сжатие журналов",
        "desc": "journalctl --vacuum-time=14d",
        "cmd": ["journalctl", "--vacuum-time=14d"],
    },
    {
        "id": "btrfs_bal",
        "icon": "drive-multidisk-symbolic",
        "label": "Баланс Btrfs",
        "desc": "btrfs balance -dusage=50 -musage=50 /",
        "cmd": ["btrfs", "balance", "start", "-dusage=50", "-musage=50", "/"],
    },
    {
        "id": "btrfs_defrag",
        "icon": "emblem-synchronizing-symbolic",
        "label": "Дефрагментация",
        "desc": "Дефрагментация всех Btrfs разделов",
        "cmd": [
            "bash", "-c",
            'findmnt -t btrfs -n -o TARGET | while read mp; do'
            '  echo ">>> $mp";'
            '  btrfs filesystem defragment -r -czstd "$mp" 2>&1'
            '    | grep -v "Text file busy" || true;'
            ' done',
        ],
    },
    {
        "id": "btrfs_scrub",
        "icon": "security-high-symbolic",
        "label": "Scrub Btrfs",
        "desc": "Проверка и исправление ошибок данных",
        "cmd": [
            "bash", "-c",
            'findmnt -t btrfs -n -o TARGET | while read mp; do'
            '  echo ">>> Scrub $mp";'
            '  btrfs scrub start -B "$mp";'
            ' done',
        ],
    },
    {
        "id": "trim",
        "icon": "media-flash-symbolic",
        "label": "SSD TRIM",
        "desc": "fstrim -av — оптимизация блоков SSD",
        "cmd": ["fstrim", "-av"],
    },
]


# ── Приложения ────────────────────────────────────────────────────────────────

def _flatpak(app_id: str) -> dict:
    """Описание источника Flathub."""
    return {
        "label": "Flathub",
        "cmd": ["flatpak", "install", "-y", "flathub", app_id],
        "check": ("flatpak", app_id),
    }


def _epm_install(pkg: str) -> dict:
    """Описание источника EPM (epm -i)."""
    return {
        "label": "EPM",
        "cmd": ["epm", "-i", pkg],
        "check": ("rpm", pkg),
    }


def _epm_play(pkg: str, check_pkg: str | None = None) -> dict:
    """Описание источника EPM (epm play)."""
    return {
        "label": "EPM",
        "cmd": ["epm", "play", pkg],
        "check": ("rpm", check_pkg or pkg),
    }


APPS: list[dict] = [
    {"group": "Общение", "items": [
        {"id": "telegram",    "label": "Telegram",    "desc": "Мессенджер",
         "source": _flatpak("org.telegram.desktop")},
        {"id": "thunderbird", "label": "Thunderbird", "desc": "Почтовый клиент",
         "source": _flatpak("org.mozilla.Thunderbird")},
        {"id": "aurynk",      "label": "Aurynk",      "desc": "Клиент для общения",
         "source": _flatpak("io.github.IshuSinghSE.aurynk")},
        {"id": "cassette",    "label": "Cassette",    "desc": "Неофициальный клиент Яндекс Музыки",
         "source": _flatpak("space.rirusha.Cassette")},
    ]},
    {"group": "Браузеры", "items": [
        {"id": "firefox",   "label": "Firefox",       "desc": "Браузер Mozilla Firefox",
         "source": _flatpak("org.mozilla.firefox")},
        {"id": "chrome",    "label": "Google Chrome", "desc": "Браузер Google Chrome",
         "source": _flatpak("com.google.Chrome")},
        {"id": "yandex",    "label": "Яндекс Браузер", "desc": "Официальный браузер от Яндекса",
         "source": _flatpak("ru.yandex.Browser")},
    ]},
    {"group": "Работа с документами", "items": [
        {"id": "libreoffice", "label": "LibreOffice", "desc": "Офисный пакет",
         "source": _flatpak("org.libreoffice.LibreOffice")},
        {"id": "onlyoffice",  "label": "OnlyOffice",  "desc": "Офисный пакет с совместимостью MS",
         "source": _flatpak("org.onlyoffice.desktopeditors")},
        {"id": "brother_printer", "label": "Принтер Brother", "desc": "cups-browsed avahi-daemon — поддержка сети и AirPrint",
         "source": {
             "label": "APT",
             "cmd": [
                 "bash", "-c",
                 "apt-get install -y cups-browsed avahi-daemon libnss-mdns sane sane-airscan"
                 " && systemctl enable --now cups-browsed"
                 " && systemctl enable --now avahi-daemon",
             ],
             "check": ("rpm", "cups-browsed"),
         }},
    ]},
    {"group": "Мультимедиа", "items": [
        {"id": "obs", "label": "OBS Studio", "desc": "Запись и стриминг",
         "source": {
             "label": "Flathub",
             "cmd": ["flatpak", "install", "-y", "flathub",
                     "com.obsproject.Studio",
                     "com.obsproject.Studio.Plugin.WaylandHotkeys"],
             "check": ("flatpak", "com.obsproject.Studio"),
         }},
        {"id": "kdenlive",  "label": "KDENlive",  "desc": "Видеоредактор",
         "source": _flatpak("org.kde.kdenlive")},
        {"id": "audacity",  "label": "Audacity",  "desc": "Аудиоредактор",
         "source": _flatpak("org.audacityteam.Audacity")},
        {"id": "krita",     "label": "Krita",     "desc": "Растровый графический редактор",
         "source": _flatpak("org.kde.krita")},
        {"id": "inkscape",  "label": "Inkscape",  "desc": "Векторный редактор",
         "source": _flatpak("org.inkscape.Inkscape")},
        {"id": "spotify",   "label": "Spotify",   "desc": "Музыкальный стриминг",
         "source": _flatpak("com.spotify.Client")},
    ]},
    {"group": "Утилиты", "items": [
        {"id": "alacarte",   "label": "Alacarte",    "desc": "Редактор главного меню",
         "source": _epm_install("alacarte")},
        {"id": "bitwarden",  "label": "Bitwarden",   "desc": "Менеджер паролей",
         "source": _flatpak("com.bitwarden.desktop")},
        {"id": "foliate",    "label": "Foliate",     "desc": "Читалка книг",
         "source": _flatpak("com.github.johnfactotum.Foliate")},
        {"id": "calibre",    "label": "Calibre",     "desc": "Управление библиотекой книг",
         "source": _flatpak("com.calibre_ebook.calibre")},
        {"id": "parabolic",  "label": "Parabolic",   "desc": "Загрузчик видео (YouTube и др.)",
         "source": _flatpak("org.nickvision.tubeconverter")},
        {"id": "resources",  "label": "Ресурсы",     "desc": "Мониторинг системных ресурсов",
         "source": _flatpak("net.nokyan.Resources")},
        {"id": "protonplus", "label": "Proton Plus", "desc": "Менеджер версий Proton для Steam",
         "source": _flatpak("com.vysp3r.ProtonPlus")},
        {"id": "warehouse",  "label": "Warehouse",   "desc": "Управление Flatpak-приложениями",
         "source": _flatpak("io.github.flattool.Warehouse")},
        {"id": "flatseal",   "label": "Flatseal",    "desc": "Управление правами Flatpak",
         "source": _flatpak("com.github.tchx84.Flatseal")},
        {"id": "monic", "label": "Monic", "desc": "Управление монитором (DDC/CI)",
         "source": {
             "label": "GitHub",
             "cmd": [
                 "bash", "-c",
                 "set -e; "
                 "REAL_USER=$SUDO_USER; "
                 "HOME_DIR=$(getent passwd $REAL_USER | cut -d: -f6); "
                 "apt-get install -y python3-module-pip; "
                 "mkdir -p /etc/modules-load.d; echo i2c-dev > /etc/modules-load.d/i2c-dev.conf; "
                 "groupadd -f i2c; "
                 "chown :i2c /dev/i2c-* 2>/dev/null || true; "
                 "usermod -aG i2c $REAL_USER; "
                 "echo 'KERNEL==\"i2c-[0-9]*\", GROUP=\"i2c\"' > /etc/udev/rules.d/10-i2c.rules; "
                 "udevadm control --reload-rules; udevadm trigger; "
                 "sudo -u $REAL_USER bash -c "
                 "  'cd /tmp && rm -rf Monic"
                 "   && git clone https://github.com/toxblh/Monic.git"
                 "   && cd Monic && ./install.sh'; "
                 "sudo -u $REAL_USER bash"
                 " $HOME_DIR/.local/share/monitor-control/run_monitor_control.sh",
             ],
             "check": ("path", "~/.local/share/monitor-control/run_monitor_control.sh"),
         }},
    ]},
    {"group": "Диагностика", "items": [
        {"id": "furmark", "label": "FurMark", "desc": "Стресс-тест GPU",
         "source": _epm_play("furmark")},
        {"id": "occt",    "label": "OCCT",    "desc": "Стресс-тест CPU/GPU/RAM",
         "source": _epm_play("occt", check_pkg="OCCT")},
    ]},
]
