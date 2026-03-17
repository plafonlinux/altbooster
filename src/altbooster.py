#!/usr/bin/env python3

import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_DEBUG = "--debug" in sys.argv
if _DEBUG:
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
from gi.repository import Gio, Adw, GLib

from core import config
config.DEBUG = _DEBUG
from ui import PlafonWindow

class AltBoosterApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="ru.altbooster.app",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        config.load_state()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        win = PlafonWindow(application=app)
        win.present()
        GLib.timeout_add(400, win.ask_password)

if __name__ == "__main__":
    if os.geteuid() == 0:
        print("⚠  Не запускайте GUI от root. Используйте обычного пользователя.")
        sys.exit(1)
    try:
        AltBoosterApp().run(sys.argv)
    except KeyboardInterrupt:
        pass
