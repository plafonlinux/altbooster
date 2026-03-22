import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk


def make_icon(name: str, size: int = 22, fallback: str = "application-x-executable-symbolic") -> Gtk.Image:
    gicon = Gio.ThemedIcon.new_from_names([name, fallback])
    icon = Gtk.Image.new_from_gicon(gicon)
    icon.set_pixel_size(size)
    return icon


def make_button(label: str, width: int = 130, style: str = "suggested-action") -> Gtk.Button:
    btn = Gtk.Button(label=label)
    if width > 0:
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

    clamp = Adw.Clamp()
    clamp.set_maximum_size(1152)
    clamp.set_tightening_threshold(864)
    clamp.set_child(body)
    scroll.set_child(clamp)
    return scroll, body


def scroll_child_into_view(scrolled: Gtk.ScrolledWindow, child: Gtk.Widget) -> None:
    """Прокрутить так, чтобы виджет оказался в зоне видимости (GTK ≥ 4.12)."""
    if scrolled is None or child is None:
        return
    try:
        scrolled.scroll_child(child, Gtk.ScrollChildScrollFlags.FOCUS)
    except (AttributeError, TypeError):
        child.grab_focus()
