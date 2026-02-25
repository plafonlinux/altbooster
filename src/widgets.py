"""
widgets.py — общие фабрики виджетов GTK4 / Adwaita.

Единственный источник для _make_icon, _make_button, _make_status_icon и т.д.
Используется как в ui-модулях, так и в dynamic_page.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk


def make_icon(name: str, size: int = 22) -> Gtk.Image:
    icon = Gtk.Image.new_from_icon_name(name)
    icon.set_pixel_size(size)
    return icon


def make_button(label: str, width: int = 130, style: str = "suggested-action") -> Gtk.Button:
    btn = Gtk.Button(label=label)
    btn.set_size_request(width, -1)
    btn.add_css_class(style)
    btn.add_css_class("pill")
    return btn


def make_status_icon() -> Gtk.Image:
    icon = Gtk.Image()
    icon.set_pixel_size(18)
    return icon


def set_status_ok(icon: Gtk.Image) -> None:
    icon.set_from_icon_name("object-select-symbolic")
    icon.add_css_class("success")


def set_status_error(icon: Gtk.Image) -> None:
    icon.set_from_icon_name("dialog-error-symbolic")
    icon.remove_css_class("success")


def clear_status(icon: Gtk.Image) -> None:
    icon.clear()
    icon.remove_css_class("success")


def make_suffix_box(*widgets) -> Gtk.Box:
    box = Gtk.Box(spacing=10)
    box.set_valign(Gtk.Align.CENTER)
    for w in widgets:
        if w is not None:
            box.append(w)
    return box


def make_scrolled_page() -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_hexpand(True)
    scroll.set_vexpand(True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    body.set_margin_top(20)
    body.set_margin_bottom(20)
    body.set_margin_start(20)
    body.set_margin_end(20)
    scroll.set_child(body)
    return scroll, body
