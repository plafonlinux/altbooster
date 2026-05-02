#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path

import gettext as _gettext

_APP_DIR = str(Path(__file__).resolve().parent)
if importlib.util.find_spec("core") is None:
    sys.path.insert(0, _APP_DIR)

_LOCALE_DIRS = [
    Path(__file__).resolve().parent.parent / "locale",
    Path("/usr/share/locale"),
    Path("/usr/local/share/locale"),
]

def _setup_i18n() -> None:
    for locale_dir in _LOCALE_DIRS:
        if (locale_dir / "ru" / "LC_MESSAGES" / "altbooster.mo").exists():
            _gettext.bindtextdomain("altbooster", str(locale_dir))
            break
    _gettext.textdomain("altbooster")
    builtins_module = sys.modules.get("builtins") or __import__("builtins")
    if not hasattr(builtins_module, "_"):
        builtins_module._ = _gettext.gettext

_setup_i18n()

_TAB_FLAGS = {
    "-s": "setup",
    "-a": "apps",
    "-e": "extensions",
    "-f": "tweaks",
    "-t": "borg",
    "-m": "maintenance",
    "-n": "niri",
}


def main() -> int:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(
            "Использование: altbooster [ОПЦИЯ]\n\n"
            "Опции:\n"
            "  -s    Открыть вкладку «Начало»\n"
            "  -a    Открыть вкладку «Приложения»\n"
            "  -e    Открыть вкладку «Расширения»\n"
            "  -f    Открыть вкладку «Твики и фиксы»\n"
            "  -t    Открыть вкладку «TimeSync»\n"
            "  -m    Открыть вкладку «Обслуживание»\n"
            "  -n    Открыть вкладку «Niri» (только интерфейс Niri, без сайдбара)\n"
            "          --oboi      открыть сразу на подвкладке «Обои»\n"
            "          --pakety    открыть сразу на подвкладке «Пакеты»\n"
            "          --konfigi   открыть сразу на подвкладке «Конфиги»\n"
            "  -h    Показать эту справку\n"
            "  --debug  Режим отладки\n"
        )
        return 0

    niri_standalone = "-n" in sys.argv
    initial_tab = ""
    for flag, tab in _TAB_FLAGS.items():
        if flag in sys.argv:
            sys.argv.remove(flag)
            initial_tab = tab
            break

    _NIRI_SUBTAB_FLAGS = {
        "--oboi":    "wallpapers",
        "--pakety":  "packages",
        "--konfigi": "configs",
    }
    niri_subtab = ""
    for flag, subtab in _NIRI_SUBTAB_FLAGS.items():
        if flag in sys.argv:
            sys.argv.remove(flag)
            niri_subtab = subtab
            break

    debug = "--debug" in sys.argv
    if debug:
        sys.argv.remove("--debug")
        os.environ.setdefault("G_MESSAGES_DEBUG", "all")

        def _excepthook(exc_type, exc_value, exc_tb):
            print("\n[DEBUG] Необработанное исключение:", file=sys.stderr)
            traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)

        sys.excepthook = _excepthook
        print(f"[DEBUG] ALT Booster запущен в режиме отладки. Python {sys.version}")

    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gio, Adw

    from core import config

    config.init_runtime(debug=debug, initial_tab=initial_tab, niri_standalone=niri_standalone, niri_subtab=niri_subtab)

    from ui import PlafonWindow

    class AltBoosterApp(Adw.Application):
        def __init__(self):
            # NON_UNIQUE при standalone (-n) — всегда новое окно,
            # иначе FLAGS_NONE — повторный запуск поднимает существующее окно
            flags = (
                Gio.ApplicationFlags.NON_UNIQUE
                if niri_standalone
                else Gio.ApplicationFlags.FLAGS_NONE
            )
            super().__init__(
                application_id="ru.altbooster.app",
                flags=flags,
            )
            self.connect("activate", self._on_activate)

        def _on_activate(self, app):
            config.load_state()
            quit_action = Gio.SimpleAction.new("quit", None)
            quit_action.connect("activate", lambda *_: self.quit())
            self.add_action(quit_action)
            self.set_accels_for_action("app.quit", ["<Primary>q"])
            win = PlafonWindow(application=app)

    try:
        return int(AltBoosterApp().run(sys.argv))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    if os.geteuid() == 0:
        print("⚠  Не запускайте GUI от root. Используйте обычного пользователя.")
        raise SystemExit(1)
    raise SystemExit(main())
