from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ui.widgets import make_icon


_XDG_HOME_DEFAULTS = [
    "Documents", "Документы",
    "Downloads", "Загрузки",
    "Pictures", "Изображения",
    "Music", "Музыка",
    "Videos", "Видео",
    "Desktop", "Рабочий стол",
]

_FOLDER_ICONS: dict[str, str] = {
    "Documents": "folder-documents-symbolic",
    "Документы": "folder-documents-symbolic",
    "Downloads": "folder-download-symbolic",
    "Загрузки": "folder-download-symbolic",
    "Pictures": "folder-pictures-symbolic",
    "Изображения": "folder-pictures-symbolic",
    "Music": "folder-music-symbolic",
    "Музыка": "folder-music-symbolic",
    "Videos": "folder-videos-symbolic",
    "Видео": "folder-videos-symbolic",
    "Desktop": "user-desktop-symbolic",
    "Рабочий стол": "user-desktop-symbolic",
    "Templates": "folder-templates-symbolic",
    "Шаблоны": "folder-templates-symbolic",
    "Public": "folder-publicshare-symbolic",
}

_HOME_PICKER_CSS = """
flowboxchild {
    border-radius: 10px;
    padding: 2px;
}
flowboxchild:hover {
    background-color: alpha(currentColor, 0.06);
}
flowboxchild.checked {
    background-color: alpha(@accent_bg_color, 0.18);
    outline: 2px solid @accent_color;
    outline-offset: -2px;
}
"""


class HomeDirPickerDialog(Adw.Window):

    def __init__(self, parent, dirs: list[str], selected: list[str], on_apply):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Папки домашней директории")
        self.set_default_size(740, 520)
        self._on_apply = on_apply
        self._selected: set[str] = set(selected)
        self._child_map: dict[str, Gtk.FlowBoxChild] = {}

        css = Gtk.CssProvider()
        css.load_from_string(_HOME_PICKER_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        header = Adw.HeaderBar()
        btn_all = Gtk.Button(label="Выбрать всё")
        btn_all.add_css_class("flat")
        btn_all.connect("clicked", lambda _: self._set_all(True))
        btn_none = Gtk.Button(label="Снять всё")
        btn_none.add_css_class("flat")
        btn_none.connect("clicked", lambda _: self._set_all(False))
        header.pack_start(btn_none)
        header.pack_end(btn_all)

        self._flow = Gtk.FlowBox()
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_activate_on_single_click(True)
        self._flow.set_max_children_per_line(7)
        self._flow.set_min_children_per_line(3)
        self._flow.set_column_spacing(4)
        self._flow.set_row_spacing(4)
        self._flow.set_margin_start(16)
        self._flow.set_margin_end(16)
        self._flow.set_margin_top(16)
        self._flow.set_margin_bottom(8)
        self._flow.set_homogeneous(True)
        self._flow.connect("child-activated", self._toggle)

        for name in dirs:
            child = self._make_child(name)
            self._flow.append(child)
            self._child_map[name] = child
            if name in self._selected:
                child.add_css_class("checked")

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._flow)

        btn_apply = Gtk.Button(label="Применить")
        btn_apply.add_css_class("suggested-action")
        btn_apply.connect("clicked", self._apply)
        action_bar = Gtk.ActionBar()
        action_bar.pack_end(btn_apply)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(scroll)
        box.append(action_bar)
        self.set_content(box)

    def _make_child(self, name: str) -> Gtk.FlowBoxChild:
        icon_name = _FOLDER_ICONS.get(name, "folder-symbolic")

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(52)
        icon.set_margin_top(10)

        check_img = Gtk.Image.new_from_icon_name("object-select-symbolic")
        check_img.set_pixel_size(16)
        check_img.set_halign(Gtk.Align.END)
        check_img.set_valign(Gtk.Align.START)
        check_img.set_margin_top(4)
        check_img.set_margin_end(4)
        check_img.add_css_class("accent")
        check_img.set_visible(name in self._selected)

        overlay = Gtk.Overlay()
        overlay.set_child(icon)
        overlay.add_overlay(check_img)

        label = Gtk.Label(label=name)
        label.set_max_width_chars(11)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_margin_top(4)
        label.set_margin_bottom(10)
        label.add_css_class("caption")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_halign(Gtk.Align.CENTER)
        vbox.set_size_request(90, -1)
        vbox.append(overlay)
        vbox.append(label)

        child = Gtk.FlowBoxChild()
        child.set_child(vbox)
        child._check_img = check_img
        return child

    def _toggle(self, _flow, child):
        name = next((n for n, c in self._child_map.items() if c is child), None)
        if name is None:
            return
        if name in self._selected:
            self._selected.discard(name)
            child.remove_css_class("checked")
            child._check_img.set_visible(False)
        else:
            self._selected.add(name)
            child.add_css_class("checked")
            child._check_img.set_visible(True)

    def _set_all(self, state: bool):
        for name, child in self._child_map.items():
            if state:
                self._selected.add(name)
                child.add_css_class("checked")
                child._check_img.set_visible(True)
            else:
                self._selected.discard(name)
                child.remove_css_class("checked")
                child._check_img.set_visible(False)

    def _apply(self, _):
        self._on_apply(sorted(self._selected))
        self.close()


class FlatpakDataPickerDialog(Adw.Window):

    def __init__(self, parent, dirs: list[str], selected: list[str], on_apply, icons: dict | None = None):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Данные Flatpak — выбор приложений")
        self.set_default_size(728, 600)
        self._on_apply = on_apply
        self._selected: set[str] = set(selected)
        self._row_map: dict[str, tuple[Gtk.ListBoxRow, Gtk.CheckButton]] = {}
        self._icons: dict = icons or {}

        header = Adw.HeaderBar()
        btn_all = Gtk.Button(label="Выбрать всё")
        btn_all.add_css_class("flat")
        btn_all.connect("clicked", lambda _: self._set_all(True))
        btn_none = Gtk.Button(label="Снять всё")
        btn_none.add_css_class("flat")
        btn_none.connect("clicked", lambda _: self._set_all(False))
        header.pack_start(btn_none)
        header.pack_end(btn_all)

        self._search = Gtk.SearchEntry()
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search)

        self._list = Gtk.ListBox()
        self._list.add_css_class("boxed-list")
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.set_filter_func(self._filter)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        self._list.set_margin_top(8)
        self._list.set_margin_bottom(8)

        for name in dirs:
            row, cb = self._make_row(name)
            self._list.append(row)
            self._row_map[name] = (row, cb)

        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_bar.set_margin_start(12)
        search_bar.set_margin_end(12)
        search_bar.set_margin_top(8)
        search_bar.append(self._search)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._list)

        btn_apply = Gtk.Button(label="Применить")
        btn_apply.add_css_class("suggested-action")
        btn_apply.connect("clicked", self._apply)
        action_bar = Gtk.ActionBar()
        action_bar.pack_end(btn_apply)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(search_bar)
        box.append(scroll)
        box.append(action_bar)
        self.set_content(box)

    def _make_row(self, name: str) -> tuple[Gtk.ListBoxRow, Gtk.CheckButton]:
        row = Adw.ActionRow()
        parts = name.rsplit(".", 1)
        row.set_title(parts[-1] if len(parts) > 1 else name)
        row.set_subtitle(name)
        row.set_subtitle_selectable(False)

        icon_path = self._icons.get(name)
        if icon_path:
            try:
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 32, 32, True)
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                img = Gtk.Image()
                img.set_from_paintable(texture)
                img.set_size_request(32, 32)
                row.add_prefix(img)
            except Exception:
                row.add_prefix(make_icon("application-x-executable-symbolic"))
        else:
            row.add_prefix(make_icon("application-x-executable-symbolic"))

        cb = Gtk.CheckButton()
        cb.set_active(name in self._selected)
        cb.set_valign(Gtk.Align.CENTER)
        cb.connect("toggled", self._on_toggled, name)
        row.add_suffix(cb)
        row.set_activatable_widget(cb)
        row._app_id = name
        return row, cb

    def _on_toggled(self, cb, name: str):
        if cb.get_active():
            self._selected.add(name)
        else:
            self._selected.discard(name)

    def _set_all(self, state: bool):
        for name, (row, cb) in self._row_map.items():
            if row.get_visible():
                cb.set_active(state)

    def _on_search(self, entry):
        self._list.invalidate_filter()

    def _filter(self, row) -> bool:
        q = self._search.get_text().lower()
        if not q:
            return True
        return q in row._app_id.lower()

    def _apply(self, _):
        self._on_apply(sorted(self._selected))
        self.close()


class FolderPickerDialog(Adw.Window):

    def __init__(self, parent, title: str, dirs: list[str], selected: list[str], on_apply):
        super().__init__(transient_for=parent, modal=True)
        self.set_title(title)
        self.set_default_size(1100, 800)
        self._on_apply = on_apply
        self._checks: dict[str, Gtk.CheckButton] = {}

        header = Adw.HeaderBar()
        btn_all = Gtk.Button(label="Выбрать всё")
        btn_all.add_css_class("flat")
        btn_all.connect("clicked", lambda _: [c.set_active(True) for c in self._checks.values()])
        btn_none = Gtk.Button(label="Снять всё")
        btn_none.add_css_class("flat")
        btn_none.connect("clicked", lambda _: [c.set_active(False) for c in self._checks.values()])
        header.pack_start(btn_none)
        header.pack_end(btn_all)

        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(0)
        flow.set_row_spacing(0)
        flow.set_margin_start(12)
        flow.set_margin_end(12)
        flow.set_margin_top(8)
        flow.set_margin_bottom(8)
        flow.set_homogeneous(True)

        for name in sorted(dirs):
            check = Gtk.CheckButton(label=name)
            check.set_active(name in selected)
            check.set_margin_top(4)
            check.set_margin_bottom(4)
            check.set_margin_start(8)
            check.set_margin_end(8)
            self._checks[name] = check
            flow.append(check)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(flow)

        btn_apply = Gtk.Button(label="Применить")
        btn_apply.add_css_class("suggested-action")
        btn_apply.connect("clicked", self._apply)

        action_bar = Gtk.ActionBar()
        action_bar.pack_end(btn_apply)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(scroll)
        box.append(action_bar)
        self.set_content(box)

    def _apply(self, _):
        selected = [name for name, check in self._checks.items() if check.get_active()]
        self._on_apply(selected)
        self.close()
