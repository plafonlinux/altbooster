#!/usr/bin/env python3

import sys
import os

# Добавляем директорию скрипта в путь поиска модулей
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gi
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, Adw, GLib

import config
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
        win = PlafonWindow(application=app)
        win.present()
        GLib.idle_add(win.ask_password)

if __name__ == "__main__":
    if os.geteuid() == 0:
        print("⚠  Не запускайте GUI от root. Используйте обычного пользователя.")
        sys.exit(1)
    try:
        AltBoosterApp().run(sys.argv)
    except KeyboardInterrupt:
        pass
