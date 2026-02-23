import os
import json

CONFIG_DIR   = os.path.join(os.path.expanduser("~"), ".config", "altbooster")
CONFIG_FILE  = os.path.join(CONFIG_DIR, "window.json")
STATE_FILE   = os.path.join(CONFIG_DIR, "state.json")
DV_CACHE_DEFAULT = "/mnt/datassd/DaVinci Resolve/Work Folders/CacheClip"
DV_PROXY_DEFAULT = "/mnt/datassd/DaVinci Resolve/Work Folders/ProxyMedia"

_state: dict = {}

def load_state():
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
    except Exception:
        _state = {}

def save_state():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except OSError:
        pass

def state_get(key: str, default=None):
    return _state.get(key, default)

def state_set(key: str, value):
    _state[key] = value
    save_state()

def get_dv_cache() -> str:
    return state_get("dv_cache_path") or DV_CACHE_DEFAULT

def get_dv_proxy() -> str:
    return state_get("dv_proxy_path") or DV_PROXY_DEFAULT

TASKS = [
    {"id": "apt",          "icon": "user-trash-symbolic",           "label": "Очистка APT",     "desc": "apt-get clean — удаляет кэш пакетов",            "cmd": ["apt-get", "clean"]},
    {"id": "flatpak",      "icon": "application-x-addon-symbolic",  "label": "Уборка Flatpak",  "desc": "Удаляет неиспользуемые runtime-библиотеки",       "cmd": ["flatpak", "uninstall", "--unused", "-y"]},
    {"id": "journal",      "icon": "document-open-recent-symbolic", "label": "Сжатие журналов", "desc": "journalctl --vacuum-time=14d",                    "cmd": ["journalctl", "--vacuum-time=14d"]},
    {"id": "btrfs_bal",    "icon": "drive-multidisk-symbolic",      "label": "Баланс Btrfs",    "desc": "btrfs balance -dusage=50 -musage=50 /",           "cmd": ["btrfs", "balance", "start", "-dusage=50", "-musage=50", "/"]},
    {"id": "btrfs_defrag", "icon": "emblem-synchronizing-symbolic", "label": "Дефрагментация",  "desc": "btrfs filesystem defragment -r -czstd /home",     "cmd": ["btrfs", "filesystem", "defragment", "-r", "-czstd", "/home"]},
    {"id": "trim",         "icon": "media-flash-symbolic",          "label": "SSD TRIM",        "desc": "fstrim -av — оптимизация блоков SSD",             "cmd": ["fstrim", "-av"]},
]

APPS = [
    {"group": "Общение", "items": [
        {"id": "telegram",    "label": "Telegram",    "desc": "Мессенджер",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.telegram.desktop"],  "check": ("flatpak", "org.telegram.desktop")}},
        {"id": "thunderbird", "label": "Thunderbird", "desc": "Почтовый клиент",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.mozilla.Thunderbird"], "check": ("flatpak", "org.mozilla.Thunderbird")}},
        {"id": "aurynk",      "label": "Aurynk",      "desc": "Клиент для общения",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "io.github.IshuSinghSE.aurynk"], "check": ("flatpak", "io.github.IshuSinghSE.aurynk")}},
        {"id": "cassette",    "label": "Cassette",    "desc": "Неофициальный клиент Яндекс Музыки",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "space.rirusha.Cassette"], "check": ("flatpak", "space.rirusha.Cassette")}},
    ]},
    {"group": "Работа с документами", "items": [
        {"id": "libreoffice", "label": "LibreOffice", "desc": "Офисный пакет",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.libreoffice.LibreOffice"],    "check": ("flatpak", "org.libreoffice.LibreOffice")}},
        {"id": "onlyoffice",  "label": "OnlyOffice",  "desc": "Офисный пакет с совместимостью MS",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.onlyoffice.desktopeditors"],  "check": ("flatpak", "org.onlyoffice.desktopeditors")}},
    ]},
    {"group": "Мультимедиа", "items": [
        {"id": "obs",         "label": "OBS Studio",  "desc": "Запись и стриминг",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.obsproject.Studio", "com.obsproject.Studio.Plugin.WaylandHotkeys"], "check": ("flatpak", "com.obsproject.Studio")}},
        {"id": "kdenlive",    "label": "KDENlive",    "desc": "Видеоредактор",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.kde.kdenlive"],      "check": ("flatpak", "org.kde.kdenlive")}},
        {"id": "audacity",    "label": "Audacity",    "desc": "Аудиоредактор",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.audacityteam.Audacity"], "check": ("flatpak", "org.audacityteam.Audacity")}},
        {"id": "krita",       "label": "Krita",       "desc": "Растровый графический редактор",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.kde.krita"],         "check": ("flatpak", "org.kde.krita")}},
        {"id": "inkscape",    "label": "Inkscape",    "desc": "Векторный редактор",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.inkscape.Inkscape"], "check": ("flatpak", "org.inkscape.Inkscape")}},
        {"id": "spotify",     "label": "Spotify",     "desc": "Музыкальный стриминг",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.spotify.Client"],    "check": ("flatpak", "com.spotify.Client")}},
    ]},
    {"group": "Утилиты", "items": [
        {"id": "alacarte",    "label": "Alacarte",    "desc": "Редактор главного меню",
         "source": {"label": "EPM",     "cmd": ["epm", "-i", "alacarte"],                                        "check": ("rpm", "alacarte")}},
        {"id": "bitwarden",   "label": "Bitwarden",   "desc": "Менеджер паролей",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.bitwarden.desktop"], "check": ("flatpak", "com.bitwarden.desktop")}},
        {"id": "foliate",     "label": "Foliate",     "desc": "Читалка книг",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.github.johnfactotum.Foliate"], "check": ("flatpak", "com.github.johnfactotum.Foliate")}},
        {"id": "calibre",     "label": "Calibre",     "desc": "Управление библиотекой книг",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.calibre_ebook.calibre"], "check": ("flatpak", "com.calibre_ebook.calibre")}},
        {"id": "parabolic",   "label": "Parabolic",   "desc": "Загрузчик видео (YouTube и др.)",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "org.nickvision.tubeconverter"], "check": ("flatpak", "org.nickvision.tubeconverter")}},
        {"id": "resources",   "label": "Ресурсы",     "desc": "Мониторинг системных ресурсов",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "net.nokyan.Resources"],  "check": ("flatpak", "net.nokyan.Resources")}},
        {"id": "protonplus",  "label": "Proton Plus", "desc": "Менеджер версий Proton для Steam",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.vysp3r.ProtonPlus"], "check": ("flatpak", "com.vysp3r.ProtonPlus")}},
        {"id": "warehouse",   "label": "Warehouse",   "desc": "Управление Flatpak-приложениями",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "io.github.flattool.Warehouse"], "check": ("flatpak", "io.github.flattool.Warehouse")}},
        {"id": "flatseal",    "label": "Flatseal",    "desc": "Управление правами Flatpak",
         "source": {"label": "Flathub", "cmd": ["flatpak", "install", "-y", "flathub", "com.github.tchx84.Flatseal"], "check": ("flatpak", "com.github.tchx84.Flatseal")}},
        {"id": "monic",       "label": "Monic",       "desc": "Управление монитором (DDC/CI)",
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
                 "sudo -u $REAL_USER bash -c 'cd /tmp && rm -rf Monic && git clone https://github.com/toxblh/Monic.git && cd Monic && ./install.sh'; "
                 "sudo -u $REAL_USER bash $HOME_DIR/.local/share/monitor-control/run_monitor_control.sh"
             ],
             "check": ("path", "~/.local/share/monitor-control/run_monitor_control.sh")
         }},
    ]},
    {"group": "Диагностика", "items": [
        {"id": "furmark",     "label": "FurMark",     "desc": "Стресс-тест GPU",
         "source": {"label": "EPM",     "cmd": ["epm", "play", "furmark"],                                       "check": ("rpm", "furmark")}},
        {"id": "occt",        "label": "OCCT",        "desc": "Стресс-тест CPU/GPU/RAM",
         "source": {"label": "EPM", "cmd": ["epm", "play", "occt"], "check": ("rpm", "OCCT")}},
    ]},
]
