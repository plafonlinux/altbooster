
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ui.dynamic_page import DynamicPage
from ui.common import load_module


class AmdPage(DynamicPage):
    def __init__(self, log_fn):
        super().__init__(load_module("amd"), log_fn)
        self._log = log_fn

