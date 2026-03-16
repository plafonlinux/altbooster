from __future__ import annotations

import re
import socket
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

import backend
import config
from widgets import (
    make_icon, make_scrolled_page, make_button,
    make_status_icon, set_status_ok, set_status_error, clear_status,
    make_suffix_box,
)
from ui.flatpak_page import _build_icon_index

_INTERVALS = [
    (1,   "Каждый час"),
    (6,   "Каждые 6 часов"),
    (24,  "Раз в сутки"),
    (168, "Раз в неделю"),
]

_BTRFS_INTERVALS = [
    (1, "Каждый час"),
    (6, "Каждые 6 часов"),
    (24, "Ежедневно"),
]


class BtrfsRestoreDialog(Adw.AlertDialog):
    def __init__(self, parent, snapshot: dict, log_fn):
        super().__init__(
            heading=f"Восстановить снимок",
            body=f"Снимок: {snapshot.get('date_str', snapshot.get('name'))}",
        )
        self._snapshot = snapshot
        self._log = log_fn
        self._target_dir = str(Path.home())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(4)

        for icon_name, text in [
            ("emblem-ok-symbolic",    "Все файлы ~/"),
            ("emblem-ok-symbolic",    "Настройки GNOME и расширений"),
            ("emblem-ok-symbolic",    "Данные и бинарники Flatpak (user)"),
            ("dialog-error-symbolic", "Системные пакеты RPM — не затрагиваются"),
            ("dialog-error-symbolic", "System-wide Flatpak — не затрагивается"),
        ]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(make_icon(icon_name, 16))
            lbl = Gtk.Label(label=text)
            lbl.set_halign(Gtk.Align.START)
            row.append(lbl)
            box.append(row)

        sep = Gtk.Separator()
        sep.set_margin_top(6)
        sep.set_margin_bottom(2)
        box.append(sep)

        warn = Gtk.Label(label="⚠ Файлы ~/  будут перезаписаны. После восстановления перезайдите в систему.")
        warn.add_css_class("caption")
        warn.add_css_class("dim-label")
        warn.set_halign(Gtk.Align.START)
        warn.set_wrap(True)
        box.append(warn)

        self.set_extra_child(box)
        self.add_response("cancel", "Отмена")
        self.add_response("extract", "Извлечь в папку…")
        self.add_response("restore", "Восстановить ~/")
        self.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        self.set_default_response("cancel")
        self.connect("response", self._on_response)
        self.set_transient_for(parent)

    def _on_response(self, _d, response):
        if response == "cancel":
            return
        if response == "extract":
            self._pick_folder_then_restore()
            return
        self._do_restore(str(Path.home()))

    def _pick_folder_then_restore(self):
        try:
            fd = Gtk.FileDialog()
            fd.set_title("Папка для извлечения")
            fd.select_folder(self.get_root(), None, self._on_folder_selected, None)
        except AttributeError:
            fc = Gtk.FileChooserNative(
                title="Папка для извлечения",
                action=Gtk.FileChooserAction.SELECT_FOLDER,
                transient_for=self.get_root(),
                accept_label="Выбрать",
                cancel_label="Отмена",
            )
            def _resp(d, r):
                if r == Gtk.ResponseType.ACCEPT:
                    self._do_restore(d.get_file().get_path())
                d.unref()
            fc.connect("response", _resp)
            fc.show()

    def _on_folder_selected(self, dialog, result, _):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._do_restore(folder.get_path())
        except GLib.Error:
            pass

    def _do_restore(self, target_dir: str):
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Восстановление снимка...")
        self._log(f"\n▶  Восстановление снимка {self._snapshot['name']} → {target_dir}...\n")

        def on_done(ok):
            GLib.idle_add(self._finish, ok, win, target_dir == str(Path.home()))

        backend.btrfs_snapshot_restore(self._snapshot["path"], target_dir, self._log, on_done)

    def _finish(self, ok, win, is_home: bool):
        if ok:
            msg = "✔  Восстановление завершено!"
            if is_home:
                msg += " Перезайдите в систему для применения настроек GNOME."
            self._log(msg + "\n")
        else:
            self._log("✘  Ошибка при восстановлении\n")
        if hasattr(win, "stop_progress"):
            win.stop_progress(ok)


class BorgArchiveBrowserDialog(Adw.Window):

    def __init__(self, parent, repo_path: str, archive_name: str):
        super().__init__(transient_for=parent, modal=True)
        self.set_title(f"Архив: {archive_name}")
        self.set_default_size(700, 580)

        self._repo_path = repo_path
        self._archive_name = archive_name
        self._all_items: list[dict] = []
        self._children: dict = {}
        self._nav_stack: list[str] = []
        self._current_path: str = ""

        self._back_btn = Gtk.Button()
        self._back_btn.set_icon_name("go-previous-symbolic")
        self._back_btn.connect("clicked", self._go_back)
        self._back_btn.set_sensitive(False)

        self._path_label = Gtk.Label(label="/")
        self._path_label.set_ellipsize(Pango.EllipsizeMode.START)
        self._path_label.set_hexpand(True)

        self._search_btn = Gtk.ToggleButton()
        self._search_btn.set_icon_name("system-search-symbolic")
        self._search_btn.connect("toggled", self._toggle_search)

        header = Adw.HeaderBar()
        header.pack_start(self._back_btn)
        header.set_title_widget(self._path_label)
        header.pack_end(self._search_btn)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search)

        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        self._search_bar.set_search_mode(False)

        self._list_box = Gtk.ListBox()
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_margin_start(12)
        scroll.set_margin_end(12)
        scroll.set_margin_bottom(12)
        scroll.set_child(self._list_box)

        self._spinner = Gtk.Spinner()
        self._spinner.start()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_vexpand(True)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._spinner, "loading")
        self._stack.add_named(scroll, "list")
        self._stack.set_visible_child_name("loading")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self._search_bar)
        box.append(self._stack)
        self.set_content(box)

        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        items = backend.borg_list_archive(self._repo_path, self._archive_name)
        GLib.idle_add(self._on_loaded, items)

    def _on_loaded(self, items: list[dict]):
        self._all_items = items
        self._children = self._build_tree(items)
        self._navigate_to("")

    def _build_tree(self, items: list[dict]) -> dict:
        children = {}

        def _ensure_chain(path):
            if path in children:
                return
            children[path] = {"dirs": [], "files": []}
            if path:
                parent = "/".join(path.split("/")[:-1])
                _ensure_chain(parent)
                if path not in children[parent]["dirs"]:
                    children[parent]["dirs"].append(path)

        for item in items:
            path = item.get("path", "").rstrip("/")
            if not path:
                continue
            itype = item.get("type", "-")
            parent = "/".join(path.split("/")[:-1])
            if itype == "d":
                _ensure_chain(path)
            else:
                _ensure_chain(parent)
                children[parent]["files"].append(item)

        for v in children.values():
            v["dirs"].sort()
            v["files"].sort(key=lambda x: x.get("path", ""))

        return children

    def _navigate_to(self, path: str):
        self._current_path = path
        self._path_label.set_text("/" + path if path else "/")
        self._back_btn.set_sensitive(bool(self._nav_stack))
        self._populate_dir(path)
        self._stack.set_visible_child_name("list")

    def _clear_list(self):
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

    def _populate_dir(self, path: str):
        self._clear_list()
        data = self._children.get(path, {"dirs": [], "files": []})
        dirs = data["dirs"]
        files = data["files"]

        if not dirs and not files:
            row = Adw.ActionRow()
            row.set_title("Папка пуста")
            self._list_box.append(row)
            return

        for d in dirs:
            name = d.split("/")[-1]
            sub = self._children.get(d, {"dirs": [], "files": []})
            count = len(sub["dirs"]) + len(sub["files"])
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(f"{count} элем." if count else "")
            row.set_activatable(True)
            icon = make_icon("folder-symbolic", 16)
            row.add_prefix(icon)
            chevron = make_icon("go-next-symbolic", 16)
            chevron.add_css_class("dim-label")
            row.add_suffix(chevron)
            row.connect("activated", lambda _, d=d: self._click_dir(d))
            self._list_box.append(row)

        for item in files:
            path_full = item.get("path", "")
            name = path_full.split("/")[-1]
            size = item.get("size", 0)
            mtime = (item.get("mtime") or "")[:16].replace("T", " ")
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(mtime)
            icon = make_icon(self._file_icon(name), 16)
            row.add_prefix(icon)
            lbl = Gtk.Label(label=_fmt_size(size))
            lbl.add_css_class("dim-label")
            lbl.add_css_class("caption")
            lbl.set_valign(Gtk.Align.CENTER)
            row.add_suffix(lbl)
            self._list_box.append(row)

    def _file_icon(self, name: str) -> str:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in ("jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"):
            return "image-x-generic-symbolic"
        if ext in ("mp4", "mkv", "avi", "mov", "webm", "flv"):
            return "video-x-generic-symbolic"
        if ext in ("mp3", "flac", "ogg", "wav", "opus", "aac", "m4a"):
            return "audio-x-generic-symbolic"
        if ext in ("zip", "tar", "gz", "bz2", "xz", "7z", "rar", "zst"):
            return "package-x-generic-symbolic"
        if ext in ("py", "js", "ts", "c", "cpp", "h", "rs", "go", "java", "sh", "rb"):
            return "text-x-script-symbolic"
        if ext in ("pdf",):
            return "x-office-document-symbolic"
        return "text-x-generic-symbolic"

    def _click_dir(self, path: str):
        self._nav_stack.append(self._current_path)
        self._navigate_to(path)

    def _go_back(self, _=None):
        if self._nav_stack:
            self._navigate_to(self._nav_stack.pop())

    def _toggle_search(self, btn):
        active = btn.get_active()
        self._search_bar.set_search_mode(active)
        if active:
            self._search_entry.grab_focus()
        else:
            self._search_entry.set_text("")
            self._populate_dir(self._current_path)

    def _on_search(self, entry):
        q = entry.get_text().lower()
        if not q:
            self._populate_dir(self._current_path)
            return
        results = [i for i in self._all_items if q in i.get("path", "").lower()]
        self._clear_list()
        if not results:
            row = Adw.ActionRow()
            row.set_title("Ничего не найдено")
            self._list_box.append(row)
            return
        for item in results[:300]:
            path_full = item.get("path", "")
            name = path_full.split("/")[-1]
            size = item.get("size", 0)
            mtime = (item.get("mtime") or "")[:16].replace("T", " ")
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(path_full)
            icon = make_icon(self._file_icon(name), 16)
            row.add_prefix(icon)
            lbl = Gtk.Label(label=f"{_fmt_size(size)}  {mtime}")
            lbl.add_css_class("dim-label")
            lbl.add_css_class("caption")
            lbl.set_valign(Gtk.Align.CENTER)
            row.add_suffix(lbl)
            self._list_box.append(row)
        if len(results) > 300:
            row = Adw.ActionRow()
            row.set_title(f"... и ещё {len(results) - 300}")
            row.add_css_class("dim-label")
            self._list_box.append(row)


class BorgRestoreDialog(Adw.AlertDialog):

    def __init__(self, repo_path: str, archive_name: str, log_fn, archive_date: str = ""):
        super().__init__(
            heading="Восстановить архив",
            body="Файлы будут распакованы в указанную папку.",
        )
        self._repo_path = repo_path
        self._archive_name = archive_name
        self._log = log_fn

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(8)

        info_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = make_icon("drive-harddisk-symbolic", 16)
        lbl = Gtk.Label(label=archive_date or archive_name)
        lbl.add_css_class("dim-label")
        info_row.append(icon)
        info_row.append(lbl)
        box.append(info_row)

        sep = Gtk.Separator()
        sep.set_margin_top(4)
        box.append(sep)

        self._cb_altbooster = Gtk.CheckButton(label="Настройки ALT Booster")
        self._cb_altbooster.set_active(True)
        self._cb_flatpak = Gtk.CheckButton(label="Данные Flatpak")
        self._cb_flatpak.set_active(True)
        self._cb_files = Gtk.CheckButton(label="Пользовательские файлы")
        self._cb_files.set_active(True)
        self._cb_packages = Gtk.CheckButton(label="Переустановить RPM-пакеты")
        self._cb_packages.set_active(False)
        self._cb_dconf = Gtk.CheckButton(label="Восстановить настройки GNOME (dconf)")
        self._cb_dconf.set_active(True)
        box.append(self._cb_altbooster)
        box.append(self._cb_flatpak)
        box.append(self._cb_files)
        box.append(self._cb_packages)
        box.append(self._cb_dconf)

        sep2 = Gtk.Separator()
        sep2.set_margin_top(4)
        box.append(sep2)

        target_label = Gtk.Label(label="Папка для восстановления:")
        target_label.set_halign(Gtk.Align.START)
        box.append(target_label)

        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._target_entry = Gtk.Entry()
        self._target_entry.set_text(str(Path.home()))
        self._target_entry.set_hexpand(True)
        pick_btn = Gtk.Button(label="Выбрать…")
        pick_btn.add_css_class("flat")
        pick_btn.connect("clicked", self._on_pick_folder)
        path_row.append(self._target_entry)
        path_row.append(pick_btn)
        box.append(path_row)

        warn = Gtk.Label(label="⚠ Borg извлечёт файлы относительно выбранной папки")
        warn.add_css_class("dim-label")
        warn.add_css_class("caption")
        warn.set_halign(Gtk.Align.START)
        warn.set_wrap(True)
        box.append(warn)

        self.set_extra_child(box)
        self.add_response("cancel", "Отмена")
        self.add_response("restore", "Восстановить")
        self.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        self.set_default_response("cancel")
        self.connect("response", self._on_response)

    def _on_pick_folder(self, _btn):
        try:
            fd = Gtk.FileDialog()
            fd.set_title("Выберите папку для восстановления")
            fd.select_folder(self.get_root(), None, self._on_folder_selected, None)
        except AttributeError:
            fc = Gtk.FileChooserNative(
                title="Выберите папку для восстановления",
                action=Gtk.FileChooserAction.SELECT_FOLDER,
                transient_for=self.get_root(),
                accept_label="Выбрать",
                cancel_label="Отмена",
            )
            def _resp(d, r):
                if r == Gtk.ResponseType.ACCEPT:
                    self._target_entry.set_text(d.get_file().get_path())
                d.unref()
            fc.connect("response", _resp)
            fc.show()

    def _on_folder_selected(self, dialog, result, _):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._target_entry.set_text(folder.get_path())
        except GLib.Error:
            pass

    def _on_response(self, _d, response):
        if response != "restore":
            return
        target_dir = self._target_entry.get_text().strip()
        if not target_dir:
            return
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Восстановление архива...")
        self._log(f"\n▶  Восстановление {self._archive_name} → {target_dir}...\n")

        restore_flatpak = self._cb_flatpak.get_active()
        restore_packages = self._cb_packages.get_active()
        restore_dconf = self._cb_dconf.get_active()

        def _done(ok):
            if not ok:
                GLib.idle_add(self._finish, False, win)
                return

            meta_dir = Path(target_dir) / "tmp" / "altbooster-backup-meta"
            if not meta_dir.exists():
                meta_dir = Path(target_dir) / "altbooster-backup-meta"

            def _step_dconf(ok_pkgs):
                if restore_dconf and meta_dir.exists():
                    self._log("▶  Восстановление настроек GNOME (dconf)...\n")
                    ok_dconf = backend.restore_dconf_meta(meta_dir)
                    if ok_dconf:
                        self._log("   ✔ dconf загружен\n")
                    else:
                        self._log("   ⚠ Ошибка при загрузке dconf\n")
                GLib.idle_add(self._finish, True, win)

            def _step_packages(ok_flat):
                if restore_packages and meta_dir.exists():
                    backend.restore_packages_meta(meta_dir, self._log, _step_dconf)
                else:
                    _step_dconf(True)

            if restore_flatpak and meta_dir.exists():
                backend.restore_flatpak_meta(meta_dir, self._log, _step_packages)
            else:
                _step_packages(True)

        backend.borg_extract(
            self._repo_path, self._archive_name, target_dir, [],
            self._log, _done,
        )

    def _finish(self, ok, win):
        msg = "✔  Восстановление завершено!\n" if ok else "✘  Ошибка при восстановлении\n"
        self._log(msg)
        if hasattr(win, "stop_progress"):
            win.stop_progress(ok)


def _fmt_size(size: int) -> str:
    if not isinstance(size, (int, float)) or size < 0:
        return ""
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} ПБ"


def _fmt_archive_date(name: str) -> str:
    parts = name.split("-", 1)
    if len(parts) == 2:
        ts = parts[1].replace("T", " ").replace("-", ".").replace(".", " ", 2)
        return ts
    return name


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


class BorgBackupSummaryDialog(Adw.Window):

    def __init__(self, parent, repo_path: str, opts: dict, on_confirm):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Сводка резервной копии")
        self.set_default_size(1100, 600)

        self._repo_path = repo_path
        self._opts = opts
        self._on_confirm = on_confirm

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        cancel_btn = Gtk.Button(label="Отмена")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        self._confirm_btn = Gtk.Button(label="Создать резервную копию")
        self._confirm_btn.add_css_class("suggested-action")
        self._confirm_btn.set_sensitive(False)
        self._confirm_btn.connect("clicked", self._on_confirm_clicked)
        header.pack_end(self._confirm_btn)

        self._spinner = Gtk.Spinner()
        self._spinner.start()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_vexpand(True)

        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self._content_box.set_margin_start(20)
        self._content_box.set_margin_end(20)
        self._content_box.set_margin_top(16)
        self._content_box.set_margin_bottom(20)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._content_box)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._spinner, "loading")
        self._stack.add_named(scroll, "content")
        self._stack.set_visible_child_name("loading")

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root_box.append(header)
        root_box.append(self._stack)
        self.set_content(root_box)

        threading.Thread(target=self._load_data, daemon=True).start()

    @staticmethod
    def _du(path: str) -> str:
        return BorgBackupSummaryDialog._du_pair(path)[0]

    @staticmethod
    def _du_pair(path: str) -> tuple[str, int]:
        try:
            r = subprocess.run(
                ["du", "-sk", path],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            if r.returncode == 0:
                kb = int(r.stdout.split("\t")[0].strip())
                if kb >= 1024 * 1024:
                    return f"{kb / 1024 / 1024:.1f} ГБ", kb
                if kb >= 1024:
                    return f"{kb / 1024:.0f} МБ", kb
                return f"{kb} КБ", kb
        except Exception:
            pass
        return "", 0

    @staticmethod
    def _fmt_kb(kb: int) -> str:
        if kb >= 1024 * 1024:
            return f"{kb / 1024 / 1024:.1f} ГБ"
        if kb >= 1024:
            return f"{kb / 1024:.0f} МБ"
        return f"{kb} КБ"

    @staticmethod
    def _du_children(parent: str) -> dict[str, str]:
        try:
            r = subprocess.run(
                ["du", "-sh", "--max-depth=1", parent],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            result = {}
            for line in r.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    size = parts[0].strip()
                    name = Path(parts[1].strip()).name
                    if name and name != Path(parent).name:
                        result[name] = size
            return result
        except Exception:
            return {}

    @staticmethod
    def _fmt_bytes(kb_str: str) -> str:
        try:
            kb = int(kb_str)
            if kb >= 1024 * 1024:
                return f"{kb / 1024 / 1024:.1f} ГБ"
            if kb >= 1024:
                return f"{kb / 1024:.0f} МБ"
            return f"{kb} КБ"
        except Exception:
            return ""

    def _load_data(self):
        data = {}
        home = Path.home()
        total_kb = 0
        var_app_str = str(home / ".var" / "app")

        raw_paths = list(self._opts.get("paths", []))
        paths_with_size = []
        paths_kb = 0
        for p in raw_paths:
            s, kb = self._du_pair(p)
            paths_with_size.append((p, s))
            if not p.startswith(var_app_str):
                paths_kb += kb
                total_kb += kb
        data["paths"] = paths_with_size
        data["paths_kb"] = paths_kb

        if self._opts.get("flatpak_apps"):
            source_mode = self._opts.get("flatpak_apps_source", 0)
            data["flatpak_apps_source"] = source_mode
            try:
                r = subprocess.run(
                    ["flatpak", "list", "--app", "--columns=name,application,size"],
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                if r.returncode != 0:
                    r = subprocess.run(
                        ["flatpak", "list", "--app", "--columns=name,application"],
                        capture_output=True, text=True, encoding="utf-8", timeout=15,
                    )
                installed = {}
                for line in r.stdout.splitlines():
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split("\t")]
                    name = parts[0] if len(parts) > 0 else ""
                    app_id = parts[1] if len(parts) > 1 else ""
                    size = parts[2] if len(parts) > 2 else ""
                    if app_id:
                        installed[app_id] = (name or app_id, size)
            except Exception:
                installed = {}

            if source_mode == 1:
                try:
                    raw = backend.flatpak_apps_from_booster_list()
                    apps = []
                    for name, app_id in raw:
                        if app_id in installed:
                            inst_name, size = installed[app_id]
                            apps.append((inst_name or name, app_id, size))
                    data["flatpak_apps"] = apps
                except Exception:
                    data["flatpak_apps"] = []
            else:
                data["flatpak_apps"] = [
                    (name, app_id, size) for app_id, (name, size) in installed.items()
                ]

        if self._opts.get("flatpak_remotes"):
            try:
                r = subprocess.run(
                    ["flatpak", "remotes", "--columns=name,url"],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                remotes = []
                for line in r.stdout.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        remotes.append((parts[0].strip(), parts[1].strip(), ""))
                    elif parts[0].strip():
                        remotes.append((parts[0].strip(), "", ""))
                data["flatpak_remotes"] = remotes
            except Exception:
                data["flatpak_remotes"] = []

        if self._opts.get("extensions"):
            try:
                r = subprocess.run(
                    ["gnome-extensions", "list", "--enabled"],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                data["extensions"] = [u.strip() for u in r.stdout.splitlines() if u.strip()]
            except Exception:
                data["extensions"] = []

        if self._opts.get("flatpak_data"):
            var_app = home / ".var" / "app"
            sizes = self._du_children(str(var_app))
            try:
                all_dirs = sorted(p.name for p in var_app.iterdir() if p.is_dir()) if var_app.exists() else []
            except Exception:
                all_dirs = []
            flt = self._opts.get("flatpak_data_filter")
            dirs = [d for d in all_dirs if d in flt] if flt is not None else all_dirs
            data["flatpak_data_dirs"] = [(d, sizes.get(d, "")) for d in dirs]
            flatpak_total_str, flatpak_kb = self._du_pair(str(var_app))
            data["flatpak_data_total"] = flatpak_total_str if flt is None else ""
            total_kb += flatpak_kb

        if self._opts.get("home_dirs"):
            home_dirs = self._opts["home_dirs"]
            home_pairs = [(d, *self._du_pair(str(home / d))) for d in home_dirs]
            data["home_dirs"] = [(d, s) for d, s, _ in home_pairs]
            home_kb = sum(kb for _, _, kb in home_pairs)
            data["home_dirs_kb"] = home_kb
            total_kb += home_kb

        if self._opts.get("custom_paths"):
            custom = self._opts["custom_paths"]
            custom_pairs = [(p, *self._du_pair(p)) for p in custom]
            data["custom_paths"] = [(p, s) for p, s, _ in custom_pairs]
            total_kb += sum(kb for _, _, kb in custom_pairs)

        try:
            r = subprocess.run(
                ["rpm", "-qa", "--queryformat", "%{NAME}\n"],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            data["packages_count"] = len([line for line in r.stdout.splitlines() if line.strip()])
        except Exception:
            data["packages_count"] = 0

        data["total_kb"] = total_kb
        GLib.idle_add(self._populate, data)

    def _make_card(self, title: str, subtitle: str = "", icon: str = "", size: str = "", chip: str = "") -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("card")
        card.set_hexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)

        if icon:
            ic = make_icon(icon, 16)
            ic.set_valign(Gtk.Align.CENTER)
            inner.append(ic)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        lbl = Gtk.Label(label=title)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(28)
        text_box.append(lbl)

        if subtitle:
            sub = Gtk.Label(label=subtitle)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            sub.set_halign(Gtk.Align.START)
            sub.set_ellipsize(Pango.EllipsizeMode.END)
            sub.set_max_width_chars(28)
            text_box.append(sub)

        inner.append(text_box)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        right.set_valign(Gtk.Align.CENTER)
        right.set_halign(Gtk.Align.END)

        if chip:
            chip_lbl = Gtk.Label(label=chip)
            chip_lbl.add_css_class("accent")
            chip_lbl.add_css_class("caption")
            chip_lbl.set_halign(Gtk.Align.END)
            right.append(chip_lbl)

        if size:
            size_lbl = Gtk.Label(label=size)
            size_lbl.add_css_class("dim-label")
            size_lbl.add_css_class("caption")
            size_lbl.set_halign(Gtk.Align.END)
            right.append(size_lbl)

        if chip or size:
            inner.append(right)

        card.append(inner)
        return card

    def _make_section(self, title: str, badge: str = "") -> tuple[Gtk.Expander, Gtk.FlowBox]:
        label_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        title_lbl.set_halign(Gtk.Align.START)
        label_box.append(title_lbl)

        if badge:
            badge_lbl = Gtk.Label(label=badge)
            badge_lbl.add_css_class("dim-label")
            badge_lbl.add_css_class("caption")
            badge_lbl.set_valign(Gtk.Align.CENTER)
            label_box.append(badge_lbl)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_homogeneous(True)
        flow.set_column_spacing(6)
        flow.set_row_spacing(6)

        flow_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        flow_wrap.set_margin_top(8)
        flow_wrap.append(flow)

        expander = Gtk.Expander()
        expander.set_label_widget(label_box)
        expander.set_expanded(True)
        expander.set_child(flow_wrap)

        return expander, flow

    def _add_cards(self, flow: Gtk.FlowBox, items: list, icon: str, limit: int = 200):
        for item in items[:limit]:
            if isinstance(item, tuple):
                title = item[0] or (item[1] if len(item) > 1 else "")
                subtitle = item[1] if len(item) > 1 and item[0] else ""
                size = item[2] if len(item) > 2 else ""
            else:
                title, subtitle, size = item, "", ""
            flow.append(self._make_card(title, subtitle, icon, size))
        if len(items) > limit:
            flow.append(self._make_card(f"… и ещё {len(items) - limit}", icon="view-more-symbolic"))

    def _populate(self, data: dict):
        # 1. Хранилище
        total_kb = data.get("total_kb", 0)
        total_badge = f"Всего: {self._fmt_kb(total_kb)}" if total_kb else ""
        repo_section, repo_flow = self._make_section("Хранилище", total_badge)
        repo_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        repo_card.add_css_class("card")
        repo_card.set_hexpand(True)
        repo_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        repo_inner.set_margin_start(12)
        repo_inner.set_margin_end(12)
        repo_inner.set_margin_top(10)
        repo_inner.set_margin_bottom(10)
        repo_inner.append(make_icon("drive-harddisk-symbolic", 16))
        repo_path_lbl = Gtk.Label(label=self._repo_path)
        repo_path_lbl.set_halign(Gtk.Align.START)
        repo_path_lbl.set_wrap(True)
        repo_path_lbl.set_wrap_mode(Pango.WrapMode.CHAR)
        repo_path_lbl.set_selectable(True)
        repo_inner.append(repo_path_lbl)
        repo_card.append(repo_inner)
        repo_flow.append(repo_card)
        self._content_box.append(repo_section)

        # 2. Папки домашнего каталога (перед конфигами)
        home_dirs = data.get("home_dirs")
        if home_dirs:
            home_kb = data.get("home_dirs_kb", 0)
            badge = f"{len(home_dirs)} папок"
            if home_kb:
                badge += f"  ·  {self._fmt_kb(home_kb)}"
            section, flow = self._make_section("Папки домашнего каталога", badge)
            self._add_cards(flow, [(f"~/{d[0]}", "", d[1]) for d in home_dirs], "folder-home-symbolic")
            self._content_box.append(section)

        # 3. Конфигурационные файлы
        var_app_str = str(Path.home() / ".var" / "app")
        paths = [(p, s) for p, s in data.get("paths", [])
                 if not p.startswith(var_app_str)]
        if paths:
            paths_kb = data.get("paths_kb", 0)
            badge = f"{len(paths)} источников"
            if paths_kb:
                badge += f"  ·  {self._fmt_kb(paths_kb)}"
            section, flow = self._make_section("Конфигурационные файлы", badge)
            self._add_cards(flow, paths, "folder-symbolic")
            self._content_box.append(section)

        # 4. Flatpak — репозитории + приложения (объединено с данными)
        remotes = data.get("flatpak_remotes")
        if remotes is not None:
            section, flow = self._make_section("Репозитории Flatpak", f"{len(remotes)} источников")
            self._add_cards(flow, remotes, "network-server-symbolic")
            self._content_box.append(section)

        apps = data.get("flatpak_apps")
        flatpak_dirs = data.get("flatpak_data_dirs")
        if apps is not None:
            data_map = {d: s for d, s in flatpak_dirs} if flatpak_dirs is not None else {}
            with_data = sum(1 for _, app_id, _ in apps if app_id in data_map)
            total = data.get("flatpak_data_total", "")

            source_mode = data.get("flatpak_apps_source", 0)
            source_label = "ALT Booster" if source_mode == 1 else "установленные"
            badge = str(len(apps)) + f" приложений  ·  {source_label}"
            if flatpak_dirs is not None:
                badge += f"  ·  с данными: {with_data}"
                if total:
                    badge += f" ({total})"
            else:
                badge += "  ·  только список"

            section, flow = self._make_section("Приложения Flatpak", badge)
            for name, app_id, size in apps:
                actual_id = app_id or name
                if actual_id in data_map:
                    data_size = data_map[actual_id]
                    chip = f"+ данные{('  ' + data_size) if data_size else ''}"
                    flow.append(self._make_card(name, app_id, "application-x-executable-symbolic", size, chip))
                else:
                    flow.append(self._make_card(name, app_id, "application-x-executable-symbolic", size))
            if len(apps) > 200:
                flow.append(self._make_card(f"… и ещё {len(apps) - 200}", icon="view-more-symbolic"))
            self._content_box.append(section)
        elif flatpak_dirs is not None:
            total = data.get("flatpak_data_total", "")
            badge = str(len(flatpak_dirs)) + " приложений" + (f"  ·  {total}" if total else "")
            section, flow = self._make_section("Данные Flatpak (~/.var/app)", badge)
            self._add_cards(flow, flatpak_dirs, "folder-symbolic")
            self._content_box.append(section)

        # 5. Расширения
        exts = data.get("extensions")
        if exts is not None:
            section, flow = self._make_section("Расширения GNOME Shell", f"{len(exts)} включено")
            self._add_cards(flow, exts, "application-x-addon-symbolic")
            self._content_box.append(section)

        # 6. Дополнительные пути
        custom = data.get("custom_paths")
        if custom:
            section, flow = self._make_section("Дополнительные пути")
            self._add_cards(flow, custom, "folder-symbolic")
            self._content_box.append(section)

        # 7. Системные пакеты
        packages_count = data.get("packages_count", 0)
        if packages_count > 0:
            section, flow = self._make_section("Системные пакеты", f"{packages_count} пакетов")
            flow.append(self._make_card(f"{packages_count} пакетов", "", "package-x-generic-symbolic"))
            self._content_box.append(section)

        self._stack.set_visible_child_name("content")
        self._confirm_btn.set_sensitive(True)

    def _on_confirm_clicked(self, _btn):
        self.close()
        self._on_confirm()


class BorgPage(Gtk.Box):

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._archives_group: Adw.PreferencesGroup | None = None
        self._archive_rows: list = []
        self._compact_row = None

        self._stack = Adw.ViewStack()

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_margin_top(4)
        switcher.set_margin_bottom(4)
        switcher.set_hexpand(False)
        switcher.set_halign(Gtk.Align.CENTER)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(640)
        clamp.set_child(switcher)
        self._tm_clamp = clamp

        self.append(clamp)
        self.append(self._stack)

        tm_tab = self._build_time_machine_tab()
        self._stack.add_titled_with_icon(tm_tab, "timemachine", "Time Machine", "document-revert-symbolic")

        self._build_info_page()
        self._build_settings_page()
        self._build_schedule_page()

        if backend.is_home_on_btrfs():
            btrfs_tab, self._btrfs_body = self._build_btrfs_tab()
            self._stack.add_titled_with_icon(btrfs_tab, "btrfs", "Снимки", "camera-photo-symbolic")

        self._page_tm = self._stack.get_page(self._stack.get_child_by_name("timemachine"))
        self._page_info = self._stack.get_page(self._stack.get_child_by_name("info"))
        self._page_settings = self._stack.get_page(self._stack.get_child_by_name("settings"))
        self._page_schedule = self._stack.get_page(self._stack.get_child_by_name("schedule"))
        btrfs_w = self._stack.get_child_by_name("btrfs")
        self._page_btrfs = self._stack.get_page(btrfs_w) if btrfs_w else None

        self._update_sections_visibility()
        threading.Thread(target=self._refresh_status_thread, daemon=True).start()
        self._apply_mode(config.state_get("borg_expert_mode", False))

    def _build_info_page(self):
        scroll, self._body = make_scrolled_page()
        self._build_status_group()
        self._info_repo_slot = Gtk.Box()
        self._body.append(self._info_repo_slot)
        self._build_repo_group()
        self._info_repo_slot.append(self._repo_group)
        self._build_archives_group()
        self._build_actions_group()
        scroll.connect("map", lambda _: self._move_repo_group_to(self._info_repo_slot))
        self._stack.add_titled_with_icon(scroll, "info", "Хранилище", "drive-harddisk-symbolic")

    def _build_settings_page(self):
        scroll, self._body = make_scrolled_page()
        self._build_sources_group()
        self._stack.add_titled_with_icon(scroll, "settings", "Источники", "preferences-system-symbolic")

    def _build_schedule_page(self):
        scroll, self._body = make_scrolled_page()
        self._build_schedule_group()
        self._build_prune_group()
        self._stack.add_titled_with_icon(scroll, "schedule", "График", "alarm-symbolic")

    def _build_time_machine_tab(self) -> Gtk.Widget:
        self._tm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._tm_box.set_margin_top(20)
        self._tm_box.set_margin_bottom(20)
        self._tm_box.set_margin_start(20)
        self._tm_box.set_margin_end(20)
        self._tm_box.set_hexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1152)
        clamp.set_tightening_threshold(864)
        clamp.set_child(self._tm_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(clamp)

        # ── строка: заголовок + эксперт-кнопка ──────────────────────────
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page_title = Gtk.Label(label="Time Machine")
        page_title.add_css_class("heading")
        page_title.set_halign(Gtk.Align.START)
        page_title.set_hexpand(True)
        self._tm_expert_btn = Gtk.Button()
        self._tm_expert_btn.add_css_class("flat")
        self._tm_expert_btn.connect("clicked", self._tm_toggle_expert)
        top_bar.append(page_title)
        top_bar.append(self._tm_expert_btn)
        self._tm_box.append(top_bar)

        # ── блок «Этот компьютер» (fastfetch) ────────────────────────────
        sysinfo_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sysinfo_card.add_css_class("card")
        sysinfo_card.set_overflow(Gtk.Overflow.HIDDEN)

        def _make_info_label():
            lbl = Gtk.Label()
            lbl.add_css_class("monospace")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_valign(Gtk.Align.START)
            lbl.set_hexpand(True)
            lbl.set_selectable(True)
            lbl.set_wrap(False)
            lbl.set_margin_top(12)
            lbl.set_margin_bottom(12)
            lbl.set_margin_start(16)
            lbl.set_margin_end(16)
            return lbl

        self._tm_sysinfo_label = _make_info_label()
        self._tm_sysinfo_label.set_label("Загрузка...")
        sysinfo_card.append(self._tm_sysinfo_label)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_top(12)
        sep.set_margin_bottom(12)
        sysinfo_card.append(sep)

        self._tm_sysinfo_label2 = _make_info_label()
        self._tm_sysinfo_label2.set_label("")
        sysinfo_card.append(self._tm_sysinfo_label2)

        self._tm_sysinfo_card = sysinfo_card
        self._tm_box.append(sysinfo_card)
        threading.Thread(target=self._tm_load_sysinfo, daemon=True).start()

        def _update_sysinfo_visibility(*_):
            root = self._tm_sysinfo_card.get_root()
            if root:
                should = root.get_width() >= 800
                if self._tm_sysinfo_card.get_visible() != should:
                    self._tm_sysinfo_card.set_visible(should)

        def _on_sysinfo_mapped(widget):
            root = widget.get_root()
            if root:
                root.connect("notify::default-width", _update_sysinfo_visibility)
                root.connect("notify::maximized", _update_sysinfo_visibility)
                _update_sysinfo_visibility()

        sysinfo_card.connect("map", _on_sysinfo_mapped)

        # ── заголовок «Резервные копии» + кнопка обновить ────────────────
        backups_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        backups_label = Gtk.Label(label="Резервные копии")
        backups_label.add_css_class("heading")
        backups_label.set_halign(Gtk.Align.START)
        backups_label.set_hexpand(True)
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.add_css_class("circular")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.set_tooltip_text("Обновить список архивов")
        refresh_btn.connect("clicked", lambda _: self._tm_refresh_archives())
        backups_header.append(backups_label)
        backups_header.append(refresh_btn)
        self._tm_box.append(backups_header)

        # ── спиннер / пустое состояние ───────────────────────────────────
        self._tm_spinner = Gtk.Spinner()
        self._tm_spinner.set_halign(Gtk.Align.CENTER)
        self._tm_spinner.set_visible(False)
        self._tm_box.append(self._tm_spinner)

        self._tm_placeholder = Adw.StatusPage()
        self._tm_placeholder.add_css_class("compact")
        self._tm_placeholder.set_visible(False)
        self._tm_box.append(self._tm_placeholder)

        # ── карусель ─────────────────────────────────────────────────────
        self._tm_carousel = Adw.Carousel()
        self._tm_carousel.set_hexpand(True)
        self._tm_carousel.set_spacing(8)
        self._tm_carousel.set_allow_scroll_wheel(False)
        self._tm_carousel.connect("page-changed", lambda _c, _i: self._tm_update_nav_buttons())
        self._tm_carousel.set_visible(False)

        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(self._tm_carousel)
        dots.set_halign(Gtk.Align.CENTER)
        dots.set_visible(False)
        self._tm_dots = dots

        self._tm_btn_prev = Gtk.Button(icon_name="go-previous-symbolic")
        self._tm_btn_prev.add_css_class("circular")
        self._tm_btn_prev.set_valign(Gtk.Align.CENTER)
        self._tm_btn_prev.connect("clicked", self._tm_carousel_prev)

        self._tm_btn_next = Gtk.Button(icon_name="go-next-symbolic")
        self._tm_btn_next.add_css_class("circular")
        self._tm_btn_next.set_valign(Gtk.Align.CENTER)
        self._tm_btn_next.connect("clicked", self._tm_carousel_next)

        carousel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        carousel_row.append(self._tm_btn_prev)
        carousel_row.append(self._tm_carousel)
        carousel_row.append(self._tm_btn_next)
        self._tm_carousel_row = carousel_row
        carousel_row.set_visible(False)

        self._tm_box.append(carousel_row)
        self._tm_box.append(dots)

        # ── кнопки ───────────────────────────────────────────────────────
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.CENTER)

        self._tm_create_btn = Gtk.Button(label="Создать резервную копию")
        self._tm_create_btn.add_css_class("suggested-action")
        self._tm_create_btn.add_css_class("pill")
        self._tm_create_btn.connect("clicked", lambda _: self._tm_on_create())
        btns.append(self._tm_create_btn)

        self._tm_delete_btn = Gtk.Button(label="Удалить снимки")
        self._tm_delete_btn.add_css_class("destructive-action")
        self._tm_delete_btn.add_css_class("pill")
        self._tm_delete_btn.connect("clicked", lambda _: self._tm_on_delete_archives())
        btns.append(self._tm_delete_btn)

        self._tm_box.append(btns)

        self._tm_repo_slot = Gtk.Box()
        self._tm_box.append(self._tm_repo_slot)

        # ── блок переноса на другой компьютер ────────────────────────────
        transfer_group = Adw.PreferencesGroup()
        transfer_group.set_title("Перенос на другой компьютер")

        self._sw_tar_export = Adw.SwitchRow()
        self._sw_tar_export.set_title("Сохранять .tar после бэкапа")
        self._sw_tar_export.set_subtitle("Копирует borg-репозиторий в один .tar-файл на указанный носитель")
        self._sw_tar_export.set_active(config.state_get("borg_tar_export_enabled", False))
        self._sw_tar_export.connect("notify::active", self._on_tar_export_toggled)
        transfer_group.add(self._sw_tar_export)

        self._row_tar_path = Adw.EntryRow()
        self._row_tar_path.set_title("Папка назначения")
        self._row_tar_path.set_text(config.state_get("borg_tar_export_path", "") or "")
        self._row_tar_path.set_show_apply_button(True)
        self._row_tar_path.connect("apply", lambda _: config.state_set("borg_tar_export_path", self._row_tar_path.get_text().strip()))
        self._row_tar_path.set_visible(config.state_get("borg_tar_export_enabled", False))

        tar_pick_btn = Gtk.Button()
        tar_pick_btn.set_icon_name("folder-open-symbolic")
        tar_pick_btn.add_css_class("flat")
        tar_pick_btn.set_valign(Gtk.Align.CENTER)
        tar_pick_btn.connect("clicked", self._on_pick_tar_folder)
        self._row_tar_path.add_suffix(tar_pick_btn)
        transfer_group.add(self._row_tar_path)

        self._tm_box.append(transfer_group)

        scroll.connect("map", lambda _: (self._tm_refresh_archives(), self._move_repo_group_to(self._tm_repo_slot)))
        return scroll

    def _tm_load_sysinfo(self):
        _ansi = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
        for cmd in [
            ["fastfetch", "--logo", "none", "--pipe"],
            ["neofetch", "--off", "--stdout"],
        ]:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", timeout=8)
                if r.returncode == 0 and r.stdout.strip():
                    text = _ansi.sub("", r.stdout).strip()
                    col1, col2 = self._tm_split_sysinfo(text)
                    GLib.idle_add(self._tm_sysinfo_label.set_label, col1)
                    GLib.idle_add(self._tm_sysinfo_label2.set_label, col2)
                    return
            except Exception:
                continue
        GLib.idle_add(self._tm_sysinfo_label.set_label, "fastfetch не установлен")

    @staticmethod
    def _tm_split_sysinfo(text: str) -> tuple[str, str]:
        lines = text.split("\n")
        blocks, current = [], []
        for line in lines:
            if line.strip():
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        if len(blocks) <= 1:
            return text, ""
        _hw = re.compile(r'GHz|GiB|MiB|\d{3,4}x\d{3,4}|\d+ Hz|btrfs|ext4|xfs|ntfs')
        hw, sw = [], []
        for blk in blocks:
            if any(_hw.search(l) for l in blk):
                hw.append(blk)
            else:
                sw.append(blk)
        col1 = "\n\n".join("\n".join(b) for b in hw)
        col2 = "\n\n".join("\n".join(b) for b in sw)
        return col2, col1

    def _move_repo_group_to(self, slot: Gtk.Box):
        parent = self._repo_group.get_parent()
        if parent is slot:
            return
        if parent is not None:
            parent.remove(self._repo_group)
        simple = (slot is self._tm_repo_slot)
        self._repo_group.set_title("Куда сохранять" if simple else "Расположение")
        self._row_repo_path.set_title("Папка для резервных копий" if simple else "Путь к хранилищу")
        self._row_passphrase.set_visible(True)
        self._ssh_section.set_visible(not simple)
        slot.append(self._repo_group)

    def _tm_on_delete_archives(self):
        repo_path = config.state_get("borg_repo_path", "") or ""
        if not repo_path or not backend.is_repo_initialized(repo_path):
            return

        def _worker():
            archives, _ = backend.borg_list(repo_path)
            GLib.idle_add(self._tm_show_delete_dialog, list(reversed(archives)))

        threading.Thread(target=_worker, daemon=True).start()

    def _tm_show_delete_dialog(self, archives: list[dict]):
        if not archives:
            return

        dialog = Adw.AlertDialog(heading="Удалить резервные копии")
        dialog.set_body("Выберите копии для удаления:")
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        checks = []
        for a in archives:
            name = a.get("name", "")
            raw = a.get("start") or a.get("time") or ""
            label = raw[:16].replace("T", " ") if raw else name
            cb = Gtk.CheckButton(label=label)
            box.append(cb)
            checks.append((cb, name))
        dialog.set_extra_child(box)

        def _on_response(d, response):
            if response != "delete":
                return
            to_delete = [n for cb, n in checks if cb.get_active()]
            if not to_delete:
                return
            repo = config.state_get("borg_repo_path", "") or ""
            win = self.get_root()

            def _delete_next(names):
                if not names:
                    GLib.idle_add(self._tm_refresh_archives)
                    return
                n = names[0]
                self._log(f"▶  Удаление {n}...\n")
                backend.borg_delete_archive(
                    repo, n, self._log,
                    lambda ok: (self._log("✔\n" if ok else "✘\n"), _delete_next(names[1:]))
                )

            if hasattr(win, "start_progress"):
                win.start_progress("Удаление архивов...")
            _delete_next(to_delete)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _tm_refresh_archives(self):
        repo_path = config.state_get("borg_repo_path", "") or ""
        if not repo_path or not backend.is_repo_initialized(repo_path):
            self._tm_show_placeholder("not_configured")
            return
        self._tm_show_placeholder("loading")

        def _worker():
            archives, error = backend.borg_list(repo_path)
            GLib.idle_add(self._tm_populate_archives, list(reversed(archives)), error)

        threading.Thread(target=_worker, daemon=True).start()

    def _tm_populate_archives(self, archives: list[dict], error: str = ""):
        while self._tm_carousel.get_n_pages() > 0:
            self._tm_carousel.remove(self._tm_carousel.get_nth_page(0))

        if not archives:
            self._tm_show_placeholder("empty", error)
            return

        for archive in archives:
            card = self._tm_build_archive_card(archive)
            self._tm_carousel.append(card)

        self._tm_spinner.set_spinning(False)
        self._tm_spinner.set_visible(False)
        self._tm_placeholder.set_visible(False)
        self._tm_carousel.set_visible(True)
        self._tm_carousel_row.set_visible(True)
        self._tm_dots.set_visible(True)
        self._tm_create_btn.set_visible(True)
        self._tm_delete_btn.set_visible(True)
        self._tm_update_nav_buttons()

    def _tm_build_archive_card(self, archive: dict) -> Gtk.Box:
        name = archive.get("name", "")
        raw = archive.get("start") or archive.get("time") or ""
        date_part = raw[:10] if len(raw) >= 10 else raw
        time_part = raw[11:16] if len(raw) >= 16 else ""

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        card.set_margin_start(16)
        card.set_margin_end(16)
        card.set_margin_top(8)
        card.set_margin_bottom(8)
        card.set_hexpand(True)

        icon = make_icon("document-revert-symbolic", 64)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_margin_top(16)
        card.append(icon)

        date_label = Gtk.Label(label=date_part)
        date_label.add_css_class("title-2")
        date_label.set_halign(Gtk.Align.CENTER)
        card.append(date_label)

        if time_part:
            time_label = Gtk.Label(label=time_part)
            time_label.add_css_class("dim-label")
            time_label.set_halign(Gtk.Align.CENTER)
            card.append(time_label)

        size_label = Gtk.Label(label="Размер: …")
        size_label.add_css_class("dim-label")
        size_label.set_halign(Gtk.Align.CENTER)
        card.append(size_label)

        repo_path = config.state_get("borg_repo_path", "") or ""

        def _on_info(stats):
            if stats and isinstance(stats.get("deduplicated_size"), (int, float)):
                size_label.set_label(f"Размер: {_fmt_size(int(stats['deduplicated_size']))}")
            else:
                size_label.set_label("")

        backend.borg_archive_info(repo_path, name, _on_info)

        btn = Gtk.Button(label="Восстановить")
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_bottom(16)
        display = f"{date_part} {time_part}".strip()
        btn.connect("clicked", lambda _, n=name, d=display: self._show_restore_dialog(n, d))
        card.append(btn)

        return card

    def _tm_show_placeholder(self, state: str, error: str = ""):
        self._tm_carousel.set_visible(False)
        self._tm_carousel_row.set_visible(False)
        self._tm_dots.set_visible(False)

        if state == "loading":
            self._tm_placeholder.set_visible(False)
            self._tm_spinner.set_spinning(True)
            self._tm_spinner.set_visible(True)
            self._tm_create_btn.set_visible(False)
            self._tm_delete_btn.set_visible(False)
            return

        self._tm_spinner.set_spinning(False)
        self._tm_spinner.set_visible(False)
        self._tm_placeholder.set_visible(True)

        expert = config.state_get("borg_expert_mode", False)
        if state == "not_configured":
            self._tm_create_btn.set_visible(False)
            self._tm_delete_btn.set_visible(False)
            self._tm_placeholder.set_icon_name("drive-harddisk-symbolic")
            if expert:
                self._tm_placeholder.set_title("Хранилище не настроено")
                self._tm_placeholder.set_description("Настройте Borg-хранилище на вкладке «Хранилище»")
                btn = Gtk.Button(label="Перейти к настройке")
                btn.add_css_class("pill")
                btn.connect("clicked", lambda _: self._stack.set_visible_child_name("info"))
                self._tm_placeholder.set_child(btn)
            else:
                self._tm_placeholder.set_title("Резервных копий пока нет")
                self._tm_placeholder.set_description("Создайте первый бэкап или откройте существующий архив")
                btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                btns.set_halign(Gtk.Align.CENTER)
                btn_create = Gtk.Button(label="Создать первый бэкап")
                btn_create.add_css_class("pill")
                btn_create.add_css_class("suggested-action")
                btn_create.connect("clicked", lambda _: self._tm_on_create())
                btn_open = Gtk.Button(label="У меня есть архив")
                btn_open.add_css_class("pill")
                btn_open.add_css_class("flat")
                btn_open.connect("clicked", lambda _: self._tm_open_existing_dialog())
                btns.append(btn_create)
                btns.append(btn_open)
                self._tm_placeholder.set_child(btns)
        else:
            self._tm_placeholder.set_icon_name("document-revert-symbolic")
            self._tm_placeholder.set_title("Архивов пока нет")
            if expert:
                self._tm_create_btn.set_visible(False)
                self._tm_delete_btn.set_visible(False)
                self._tm_placeholder.set_description("Создайте первый архив на вкладке «Хранилище»")
                btn = Gtk.Button(label="Создать архив")
                btn.add_css_class("pill")
                btn.connect("clicked", lambda _: self._stack.set_visible_child_name("info"))
                self._tm_placeholder.set_child(btn)
            else:
                self._tm_create_btn.set_visible(True)
                self._tm_delete_btn.set_visible(False)
                if error:
                    self._tm_placeholder.set_description(error[:200])
                    self._log(f"borg list: {error}\n")
                else:
                    self._tm_placeholder.set_description("Нажмите «Создать резервную копию» ниже")
                self._tm_placeholder.set_child(None)

    def _tm_update_nav_buttons(self):
        n = self._tm_carousel.get_n_pages()
        self._tm_btn_prev.set_sensitive(n > 1)
        self._tm_btn_next.set_sensitive(n > 1)

    def _tm_carousel_prev(self, _btn):
        idx = round(self._tm_carousel.get_position())
        if idx > 0:
            self._tm_carousel.scroll_to(self._tm_carousel.get_nth_page(idx - 1), True)

    def _tm_carousel_next(self, _btn):
        idx = round(self._tm_carousel.get_position())
        n = self._tm_carousel.get_n_pages()
        if idx < n - 1:
            self._tm_carousel.scroll_to(self._tm_carousel.get_nth_page(idx + 1), True)

    def _apply_mode(self, expert: bool):
        if expert:
            for page in [self._page_info, self._page_settings, self._page_schedule]:
                page.set_visible(True)
            if self._page_btrfs:
                self._page_btrfs.set_visible(True)
            self._tm_clamp.set_visible(True)
            self._stack.set_visible_child_name("info")
            self._page_tm.set_visible(False)
        else:
            self._page_tm.set_visible(True)
            self._stack.set_visible_child_name("timemachine")
            self._tm_clamp.set_visible(False)
            for page in [self._page_info, self._page_settings, self._page_schedule]:
                page.set_visible(False)
            if self._page_btrfs:
                self._page_btrfs.set_visible(False)
        self._tm_expert_btn.set_label("Расширенные настройки")
        self._btn_simple_mode.set_visible(expert)
        self._btn_init_repo.set_visible(expert)

    def _tm_toggle_expert(self, _btn):
        expert = not config.state_get("borg_expert_mode", False)
        config.state_set("borg_expert_mode", expert)
        self._apply_mode(expert)

    def _tm_open_existing_dialog(self):
        dialog = Adw.AlertDialog(
            heading="Открыть существующий архив",
            body="Укажите путь к borg-репозиторию и пароль шифрования (если задан).",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)

        path_row = Adw.EntryRow()
        path_row.set_title("Путь к архиву")
        path_row.set_text(config.state_get("borg_repo_path", "") or "")

        pick_btn = Gtk.Button()
        pick_btn.set_icon_name("folder-open-symbolic")
        pick_btn.add_css_class("flat")
        pick_btn.set_valign(Gtk.Align.CENTER)

        def _on_folder_selected(fd, result, _):
            try:
                folder = fd.select_folder_finish(result)
                if folder:
                    path_row.set_text(folder.get_path())
            except GLib.Error:
                pass

        def _pick(_btn):
            try:
                fd = Gtk.FileDialog()
                fd.set_title("Выберите папку архива")
                fd.select_folder(dialog.get_root(), None, _on_folder_selected, None)
            except AttributeError:
                fc = Gtk.FileChooserNative(
                    title="Выберите папку архива",
                    action=Gtk.FileChooserAction.SELECT_FOLDER,
                    transient_for=dialog.get_root(),
                    accept_label="Выбрать",
                    cancel_label="Отмена",
                )
                def _resp(d, r):
                    if r == Gtk.ResponseType.ACCEPT:
                        path_row.set_text(d.get_file().get_path())
                    d.unref()
                fc.connect("response", _resp)
                fc.show()

        pick_btn.connect("clicked", _pick)
        path_row.add_suffix(pick_btn)

        pw_row = Adw.PasswordEntryRow()
        pw_row.set_title("Пароль архива")
        pw_row.set_text(config.state_get("borg_passphrase", "") or "")

        box.append(path_row)
        box.append(pw_row)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("open", "Открыть")
        dialog.set_default_response("open")
        dialog.set_close_response("cancel")

        def _on_response(d, response):
            if response != "open":
                return
            path = path_row.get_text().strip()
            if not path:
                return
            config.state_set("borg_repo_path", path)
            config.state_set("borg_passphrase", pw_row.get_text())
            self._row_repo_path.set_text(path)
            self._row_passphrase.set_text(pw_row.get_text())
            self._tm_refresh_archives()

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _tm_on_create(self):
        repo_path = self._row_repo_path.get_text().strip() or config.state_get("borg_repo_path", "") or ""
        if not repo_path:
            repo_path = str(Path.home() / ".local" / "share" / "altbooster" / "backup")
        config.state_set("borg_repo_path", repo_path)
        if not backend.is_repo_initialized(repo_path):
            self._tm_ask_password_and_init(repo_path)
        else:
            self._tm_do_backup()

    def _tm_ask_password_and_init(self, repo_path: str):
        dialog = Adw.AlertDialog(
            heading="Защитите резервную копию",
            body="Придумайте пароль для шифрования архива. Запомните его — без него восстановление невозможно.",
        )
        pw_row = Adw.PasswordEntryRow()
        pw_row.set_title("Пароль")
        pw_row.set_margin_top(8)
        dialog.set_extra_child(pw_row)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("ok", "Создать")
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def _on_response(d, response):
            if response != "ok":
                return
            password = pw_row.get_text()
            config.state_set("borg_passphrase", password)
            win = self.get_root()
            if hasattr(win, "start_progress"):
                win.start_progress("Инициализация хранилища...")
            self._log("\n▶  Инициализация Borg-хранилища...\n")

            def _on_init_done(ok):
                if hasattr(win, "stop_progress"):
                    win.stop_progress(ok)
                if ok:
                    self._log("✔  Хранилище готово\n")
                    self._tm_do_backup()
                else:
                    self._log("✘  Ошибка инициализации хранилища\n")

            backend.borg_init(repo_path, self._log, _on_init_done)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _tm_do_backup(self):
        repo_path = config.state_get("borg_repo_path", "") or ""
        if not repo_path:
            return

        home = Path.home()
        paths = [str(home), str(config.CONFIG_DIR)]

        excludes = list(backend.DEFAULT_EXCLUDES)
        if repo_path.startswith(str(home)):
            excludes.append(repo_path)

        meta_dir = Path("/tmp/altbooster-backup-meta")
        backend.generate_flatpak_meta(meta_dir, 0)
        backend.generate_extensions_meta(meta_dir)
        backend.generate_system_meta(meta_dir)
        if meta_dir.exists():
            paths.append(str(meta_dir))

        archive_name = socket.gethostname() + "-" + GLib.DateTime.new_now_local().format("%Y-%m-%dT%H-%M")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Создание архива {archive_name}...")
        self._log(f"\n▶  Создание резервной копии {archive_name}...\n")

        def _done(ok):
            self._log("✔  Резервная копия создана\n" if ok else "✘  Ошибка при создании резервной копии\n")
            if ok:
                config.state_set("borg_last_backup", GLib.DateTime.new_now_local().format("%d.%m.%Y %H:%M"))
                export_enabled = config.state_get("borg_tar_export_enabled", False)
                export_path = (config.state_get("borg_tar_export_path", "") or "").strip()
                if export_enabled and export_path:
                    GLib.idle_add(win.start_progress, "Экспорт в .tar...")
                    self._log("▶  Экспорт borg-репозитория в .tar...\n")

                    def _on_export_done(ok_exp):
                        if hasattr(win, "stop_progress"):
                            win.stop_progress(ok_exp)
                        self._log("✔  Экспорт завершён\n" if ok_exp else "✘  Ошибка экспорта\n")
                        GLib.idle_add(self._tm_refresh_archives)

                    backend.borg_export_tar(repo_path, export_path, self._log, _on_export_done)
                    return
            if hasattr(win, "stop_progress"):
                win.stop_progress(ok)
            if ok:
                GLib.idle_add(self._tm_refresh_archives)

        backend.borg_create(repo_path, archive_name, paths, excludes, self._log, _done)

    def _build_status_group(self):
        group = Adw.PreferencesGroup()
        group.set_title("Статус")
        self._btn_simple_mode = Gtk.Button(label="Простой режим")
        self._btn_simple_mode.add_css_class("flat")
        self._btn_simple_mode.connect("clicked", self._tm_toggle_expert)
        group.set_header_suffix(self._btn_simple_mode)
        self._body.append(group)

        self._row_borg = Adw.ActionRow()
        self._row_borg.set_title("BorgBackup")
        self._icon_borg = make_status_icon()
        self._btn_install = Gtk.Button(label="Установить")
        self._btn_install.add_css_class("suggested-action")
        self._btn_install.add_css_class("pill")
        self._btn_install.set_valign(Gtk.Align.CENTER)
        self._btn_install.connect("clicked", self._on_install_borg)
        self._row_borg.add_suffix(self._btn_install)
        self._row_borg.add_suffix(self._icon_borg)
        self._row_borg.add_prefix(make_icon("package-x-generic-symbolic"))
        group.add(self._row_borg)

        self._row_repo_status = Adw.ActionRow()
        self._row_repo_status.set_title("Хранилище")
        self._icon_repo = make_status_icon()
        self._row_repo_status.add_suffix(self._icon_repo)
        self._row_repo_status.add_prefix(make_icon("drive-harddisk-symbolic"))
        group.add(self._row_repo_status)

        self._row_last = Adw.ActionRow()
        self._row_last.set_title("Последняя копия")
        self._row_last.set_subtitle("—")
        self._row_last.add_prefix(make_icon("appointment-symbolic"))
        group.add(self._row_last)

        self._row_next = Adw.ActionRow()
        self._row_next.set_title("Следующий запуск")
        self._row_next.set_subtitle("расписание не задано")
        self._row_next.add_prefix(make_icon("alarm-symbolic"))
        group.add(self._row_next)

    def _build_repo_group(self):
        self._repo_group = Adw.PreferencesGroup()
        self._repo_group.set_title("Расположение")

        self._row_dest_type = Adw.ActionRow()
        self._row_dest_type.set_title("Тип назначения")
        _dest_model = Gtk.StringList.new([
            "Локальная папка / NFS / SMB",
            "SSH / SFTP",
            "Google Drive",
        ])
        self._dd_dest_type = Gtk.DropDown(model=_dest_model, valign=Gtk.Align.CENTER)
        self._dd_dest_type.set_selected(config.state_get("borg_dest_type", 0))
        self._dd_dest_type.set_size_request(210, -1)
        self._dd_dest_type.connect("notify::selected", self._on_dest_type_changed)
        self._row_dest_type.add_suffix(self._dd_dest_type)
        self._repo_group.add(self._row_dest_type)

        self._row_repo_path = Adw.EntryRow()
        self._row_repo_path.set_title("Путь к хранилищу")
        self._row_repo_path.set_text(config.state_get("borg_repo_path", "") or "")
        self._row_repo_path.set_show_apply_button(True)
        self._row_repo_path.connect("apply", lambda _: self._save_repo_settings())

        pick_folder_btn = Gtk.Button()
        pick_folder_btn.set_icon_name("folder-open-symbolic")
        pick_folder_btn.add_css_class("flat")
        pick_folder_btn.set_valign(Gtk.Align.CENTER)
        pick_folder_btn.set_tooltip_text("Выбрать папку")
        pick_folder_btn.connect("clicked", self._on_pick_repo_folder)
        self._row_repo_path.add_suffix(pick_folder_btn)
        self._repo_group.add(self._row_repo_path)

        self._row_passphrase = Adw.PasswordEntryRow()
        self._row_passphrase.set_title("Пароль шифрования (опционально)")
        self._row_passphrase.set_text(config.state_get("borg_passphrase", "") or "")
        self._row_passphrase.set_show_apply_button(True)
        self._row_passphrase.connect("apply", lambda _: self._save_repo_settings())
        self._repo_group.add(self._row_passphrase)

        self._ssh_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._row_pubkey = Adw.ActionRow()
        self._row_pubkey.set_title("Публичный SSH-ключ")
        self._row_pubkey.set_subtitle("Ключ не создан")
        self._btn_copy_key = Gtk.Button()
        self._btn_copy_key.set_icon_name("edit-copy-symbolic")
        self._btn_copy_key.add_css_class("flat")
        self._btn_copy_key.set_valign(Gtk.Align.CENTER)
        self._btn_copy_key.set_sensitive(False)
        self._btn_copy_key.set_tooltip_text("Скопировать публичный ключ")
        self._btn_copy_key.connect("clicked", self._on_copy_pubkey)
        self._row_pubkey.add_suffix(self._btn_copy_key)

        self._btn_gen_key = Gtk.Button(label="Создать SSH-ключ")
        self._btn_gen_key.add_css_class("pill")
        self._btn_gen_key.set_valign(Gtk.Align.CENTER)
        self._btn_gen_key.connect("clicked", self._on_gen_key)
        self._row_pubkey.add_suffix(self._btn_gen_key)
        self._ssh_section.append(self._row_pubkey)

        row_hint = Adw.ActionRow()
        row_hint.set_title("Добавьте ключ на сервер")
        row_hint.set_subtitle("~/.ssh/authorized_keys на удалённом хосте")
        row_hint.add_prefix(make_icon("dialog-information-symbolic"))
        self._ssh_section.append(row_hint)

        self._gd_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        gd_row = Adw.ActionRow()
        gd_row.set_title("Google Drive через GNOME Аккаунты")
        gd_row.set_subtitle("Подключите аккаунт в настройках GNOME Online Accounts")
        gd_row.add_prefix(make_icon("user-home-symbolic"))
        self._btn_detect_gd = Gtk.Button(label="Определить")
        self._btn_detect_gd.add_css_class("pill")
        self._btn_detect_gd.set_valign(Gtk.Align.CENTER)
        self._btn_detect_gd.connect("clicked", self._on_detect_gd)
        self._btn_open_goa = Gtk.Button(label="Открыть настройки")
        self._btn_open_goa.add_css_class("flat")
        self._btn_open_goa.add_css_class("pill")
        self._btn_open_goa.set_valign(Gtk.Align.CENTER)
        self._btn_open_goa.connect("clicked", lambda _: self._open_goa())
        gd_row.add_suffix(self._btn_open_goa)
        gd_row.add_suffix(self._btn_detect_gd)
        self._gd_section.append(gd_row)

        self._btn_init_repo = make_button("Инициализировать хранилище", width=200)
        self._btn_init_repo.set_halign(Gtk.Align.CENTER)
        self._btn_init_repo.set_margin_top(16)
        self._btn_init_repo.connect("clicked", self._on_init_repo)

        self._repo_group.add(self._ssh_section)
        self._repo_group.add(self._gd_section)
        self._repo_group.add(self._btn_init_repo)

        pubkey = backend.borg_get_pubkey()
        if pubkey:
            self._row_pubkey.set_subtitle(pubkey[:64] + "…")
            self._btn_copy_key.set_sensitive(True)
            self._btn_gen_key.set_label("Пересоздать ключ")

        self._on_dest_type_changed(self._row_dest_type, None)

    def _build_sources_group(self):
        grp_altbooster = Adw.PreferencesGroup()
        grp_altbooster.set_title("Настройки ALT Booster")
        grp_altbooster.set_description("~/.config/altbooster/ — пресеты и конфигурация")
        self._body.append(grp_altbooster)

        self._sw_altbooster = Adw.SwitchRow()
        self._sw_altbooster.set_title("Включить в резервную копию")
        self._sw_altbooster.add_prefix(make_icon("emblem-system-symbolic"))
        self._sw_altbooster.set_active(config.state_get("borg_src_altbooster", True))
        self._sw_altbooster.connect("notify::active", lambda s, _: config.state_set("borg_src_altbooster", s.get_active()))
        grp_altbooster.add(self._sw_altbooster)


        grp_home = Adw.PreferencesGroup()
        grp_home.set_title("Домашняя папка")
        self._body.append(grp_home)

        self._row_home = Adw.ActionRow()
        self._row_home.set_title("Домашняя папка")
        self._row_home.add_prefix(make_icon("user-home-symbolic"))
        self._switch_home = Gtk.Switch()
        self._switch_home.set_valign(Gtk.Align.CENTER)
        self._switch_home.set_active(config.state_get("borg_src_home", False))
        self._switch_home.connect("notify::active", self._on_home_toggled)
        btn_home = Gtk.Button(label="Настроить")
        btn_home.add_css_class("flat")
        btn_home.set_valign(Gtk.Align.CENTER)
        btn_home.connect("clicked", self._on_pick_home_dirs)
        self._row_home.add_suffix(self._switch_home)
        self._row_home.add_suffix(btn_home)
        self._row_home.set_activatable_widget(self._switch_home)
        grp_home.add(self._row_home)
        self._update_home_subtitle()

        grp_system = Adw.PreferencesGroup()
        grp_system.set_title("Система")
        self._body.append(grp_system)

        self._row_config = Adw.ActionRow()
        self._row_config.set_title("Конфигурационные файлы")
        self._row_config.add_prefix(make_icon("preferences-system-symbolic"))
        btn_config = Gtk.Button(label="Выбрать папки")
        btn_config.add_css_class("flat")
        btn_config.set_valign(Gtk.Align.CENTER)
        btn_config.connect("clicked", self._on_pick_config_dirs)
        self._row_config.add_suffix(btn_config)
        grp_system.add(self._row_config)
        self._update_config_subtitle()

        self._sw_extensions = Adw.SwitchRow()
        self._sw_extensions.set_title("Расширения GNOME Shell")
        self._sw_extensions.set_subtitle("Список включённых расширений + настройки dconf")
        self._sw_extensions.set_active(config.state_get("borg_src_extensions", True))
        self._sw_extensions.add_prefix(make_icon("application-x-addon-symbolic"))
        self._sw_extensions.connect("notify::active", lambda s, _: config.state_set("borg_src_extensions", s.get_active()))
        grp_system.add(self._sw_extensions)

        grp_flatpak = Adw.PreferencesGroup()
        grp_flatpak.set_title("Flatpak")
        self._body.append(grp_flatpak)

        self._row_flatpak_apps = Adw.ActionRow()
        self._row_flatpak_apps.set_title("Список приложений")
        self._row_flatpak_apps.set_subtitle("Для автоматической переустановки после восстановления")

        self._dd_flatpak_apps_src = Gtk.DropDown(
            model=Gtk.StringList.new(["Установленные в системе", "Список ALT Booster"]),
            valign=Gtk.Align.CENTER,
        )
        self._dd_flatpak_apps_src.set_selected(config.state_get("borg_src_flatpak_apps_source", 0))
        self._dd_flatpak_apps_src.connect("notify::selected", lambda s, _: config.state_set("borg_src_flatpak_apps_source", s.get_selected()))

        self._sw_flatpak_apps = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._sw_flatpak_apps.set_active(config.state_get("borg_src_flatpak_apps", True))
        self._sw_flatpak_apps.connect("notify::active", lambda s, _: config.state_set("borg_src_flatpak_apps", s.get_active()))

        self._row_flatpak_apps.add_suffix(self._dd_flatpak_apps_src)
        self._row_flatpak_apps.add_suffix(self._sw_flatpak_apps)
        self._row_flatpak_apps.set_activatable_widget(self._sw_flatpak_apps)
        grp_flatpak.add(self._row_flatpak_apps)

        self._sw_flatpak_remotes = Adw.SwitchRow()
        self._sw_flatpak_remotes.set_title("Репозитории (remotes)")
        self._sw_flatpak_remotes.set_subtitle("Flathub и другие подключённые источники")
        self._sw_flatpak_remotes.set_active(config.state_get("borg_src_flatpak_remotes", True))
        self._sw_flatpak_remotes.connect("notify::active", lambda s, _: config.state_set("borg_src_flatpak_remotes", s.get_active()))
        grp_flatpak.add(self._sw_flatpak_remotes)

        self._row_flatpak_data = Adw.ActionRow()
        self._row_flatpak_data.set_title("Данные приложений")
        self._sw_flatpak_data = Gtk.Switch()
        self._sw_flatpak_data.set_valign(Gtk.Align.CENTER)
        self._sw_flatpak_data.set_active(config.state_get("borg_src_flatpak_data", True))
        self._sw_flatpak_data.connect("notify::active", self._on_flatpak_data_toggled)
        btn_flatpak_data = Gtk.Button(label="Настроить")
        btn_flatpak_data.add_css_class("flat")
        btn_flatpak_data.set_valign(Gtk.Align.CENTER)
        btn_flatpak_data.connect("clicked", self._on_pick_flatpak_data_dirs)
        self._row_flatpak_data.add_suffix(self._sw_flatpak_data)
        self._row_flatpak_data.add_suffix(btn_flatpak_data)
        self._row_flatpak_data.set_activatable_widget(self._sw_flatpak_data)
        grp_flatpak.add(self._row_flatpak_data)
        self._update_flatpak_data_subtitle()

        self._grp_custom = Adw.PreferencesGroup()
        self._grp_custom.set_title("Дополнительные пути")
        self._body.append(self._grp_custom)

        add_row = Adw.EntryRow()
        add_row.set_title("Добавить путь")
        add_row.set_show_apply_button(True)
        add_row.connect("apply", self._on_add_custom_path)

        folder_btn = Gtk.Button()
        folder_btn.set_icon_name("folder-open-symbolic")
        folder_btn.add_css_class("flat")
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.set_tooltip_text("Выбрать папку")
        folder_btn.connect("clicked", self._on_pick_custom_path)
        add_row.add_suffix(folder_btn)

        self._grp_custom.add(add_row)
        self._add_entry_row = add_row

        for path in config.state_get("borg_custom_paths", []):
            self._add_custom_path_row(path)

    def _build_schedule_group(self):
        self._schedule_group = Adw.PreferencesGroup()
        self._schedule_group.set_title("Расписание")
        self._body.append(self._schedule_group)

        self._sw_schedule = Adw.SwitchRow()
        self._sw_schedule.set_title("Автоматическое резервное копирование")
        self._sw_schedule.set_active(backend.is_timer_active())
        self._sw_schedule.add_prefix(make_icon("alarm-symbolic"))
        self._sw_schedule.connect("notify::active", self._on_schedule_toggled)
        self._schedule_group.add(self._sw_schedule)

        self._row_mode = Adw.ComboRow()
        self._row_mode.set_title("Режим")
        self._row_mode.set_model(Gtk.StringList.new(["По дням недели", "По числам месяца"]))
        self._row_mode.set_selected(config.state_get("borg_schedule_mode", 0))
        self._row_mode.connect("notify::selected", self._on_schedule_mode_changed)
        self._schedule_group.add(self._row_mode)

        self._row_weekdays = Adw.ActionRow()
        self._row_weekdays.set_title("Дни недели")
        wdays_box = Gtk.Box(spacing=6)
        wdays_box.set_valign(Gtk.Align.CENTER)
        wdays_box.set_margin_top(8)
        wdays_box.set_margin_bottom(8)
        saved_wdays = set(config.state_get("borg_schedule_weekdays", [0, 1, 2, 3, 4]))
        self._weekday_btns: list[Gtk.ToggleButton] = []
        for i, name in enumerate(["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]):
            btn = Gtk.ToggleButton(label=name)
            btn.set_active(i in saved_wdays)
            btn.add_css_class("flat")
            btn.connect("toggled", lambda b, idx=i: self._on_weekday_toggled(b, idx))
            wdays_box.append(btn)
            self._weekday_btns.append(btn)
        self._row_weekdays.add_suffix(wdays_box)
        self._schedule_group.add(self._row_weekdays)

        self._expander_monthdays = Adw.ExpanderRow()
        self._expander_monthdays.set_title("Числа месяца")
        self._expander_monthdays.set_expanded(True)
        self._schedule_group.add(self._expander_monthdays)
        saved_mdays = set(config.state_get("borg_schedule_monthdays", [1, 15]))
        self._monthday_btns: list[Gtk.ToggleButton] = []
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(7)
        grid.set_min_children_per_line(7)
        grid.set_column_spacing(4)
        grid.set_row_spacing(4)
        grid.set_homogeneous(True)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        for day in range(1, 32):
            btn = Gtk.ToggleButton(label=str(day))
            btn.set_active(day in saved_mdays)
            btn.add_css_class("flat")
            btn.set_size_request(40, 36)
            btn.connect("toggled", lambda b, d=day: self._on_monthday_toggled(b, d))
            self._monthday_btns.append(btn)
            grid.append(btn)
        self._expander_monthdays.add_row(grid)
        self._update_monthdays_subtitle()

        self._row_time = Adw.ActionRow()
        self._row_time.set_title("Время запуска")
        time_box = Gtk.Box(spacing=4)
        time_box.set_valign(Gtk.Align.CENTER)
        self._spin_hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        self._spin_hour.set_value(config.state_get("borg_schedule_hour", 3))
        self._spin_hour.set_width_chars(2)
        self._spin_hour.connect("value-changed", lambda s: config.state_set("borg_schedule_hour", int(s.get_value())))
        colon = Gtk.Label(label=":")
        colon.add_css_class("heading")
        self._spin_minute = Gtk.SpinButton.new_with_range(0, 59, 5)
        self._spin_minute.set_value(config.state_get("borg_schedule_minute", 0))
        self._spin_minute.set_width_chars(2)
        self._spin_minute.connect("value-changed", lambda s: config.state_set("borg_schedule_minute", int(s.get_value())))
        time_box.append(self._spin_hour)
        time_box.append(colon)
        time_box.append(self._spin_minute)
        self._row_time.add_suffix(time_box)
        self._schedule_group.add(self._row_time)

        self._update_schedule_mode_ui()

    def _build_prune_group(self):
        group = Adw.PreferencesGroup()
        group.set_title("Хранение архивов")
        group.set_description("Сколько архивов хранить")
        self._body.append(group)
        self._spin_daily = self._make_spin_row("Ежедневных", "borg_keep_daily", 7)
        self._spin_weekly = self._make_spin_row("Еженедельных", "borg_keep_weekly", 4)
        self._spin_monthly = self._make_spin_row("Ежемесячных", "borg_keep_monthly", 6)
        group.add(self._spin_daily)
        group.add(self._spin_weekly)
        group.add(self._spin_monthly)

    def _make_spin_row(self, title: str, state_key: str, default: int) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        spin = Gtk.SpinButton.new_with_range(0, 365, 1)
        spin.set_value(config.state_get(state_key, default))
        spin.set_valign(Gtk.Align.CENTER)
        spin.connect("value-changed", lambda s: config.state_set(state_key, int(s.get_value())))
        row.add_suffix(spin)
        return row

    def _update_schedule_mode_ui(self):
        mode = self._row_mode.get_selected()
        self._row_weekdays.set_visible(mode == 0)
        self._expander_monthdays.set_visible(mode == 1)

    def _on_schedule_mode_changed(self, row, _):
        config.state_set("borg_schedule_mode", row.get_selected())
        self._update_schedule_mode_ui()

    def _on_weekday_toggled(self, btn, idx: int):
        days = set(config.state_get("borg_schedule_weekdays", [0, 1, 2, 3, 4]))
        if btn.get_active():
            days.add(idx)
        else:
            days.discard(idx)
        config.state_set("borg_schedule_weekdays", sorted(days))

    def _on_monthday_toggled(self, btn, day: int):
        days = set(config.state_get("borg_schedule_monthdays", [1, 15]))
        if btn.get_active():
            days.add(day)
        else:
            days.discard(day)
        config.state_set("borg_schedule_monthdays", sorted(days))
        self._update_monthdays_subtitle()

    def _update_monthdays_subtitle(self):
        days = config.state_get("borg_schedule_monthdays", [])
        if days:
            self._expander_monthdays.set_subtitle(
                "Числа: " + ", ".join(str(d) for d in sorted(days))
            )
        else:
            self._expander_monthdays.set_subtitle("Не выбрано")

    def _build_calendar_expr(self) -> str:
        hour = int(self._spin_hour.get_value())
        minute = int(self._spin_minute.get_value())
        mode = self._row_mode.get_selected()
        if mode == 0:
            _sys_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            days = sorted(config.state_get("borg_schedule_weekdays", [0, 1, 2, 3, 4]))
            days_str = ",".join(_sys_days[i] for i in days) if days else "Mon"
            return f"{days_str} *-*-* {hour:02d}:{minute:02d}:00"
        else:
            days = sorted(config.state_get("borg_schedule_monthdays", [1, 15]))
            days_str = ",".join(str(d) for d in days) if days else "1"
            return f"*-*-{days_str} {hour:02d}:{minute:02d}:00"


    def _build_archives_group(self):
        self._archives_group = Adw.PreferencesGroup()
        self._archives_group.set_title("Архивы")
        self._body.append(self._archives_group)

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.set_tooltip_text("Обновить список архивов")
        refresh_btn.connect("clicked", lambda _: self._refresh_archives())
        self._archives_group.set_header_suffix(refresh_btn)

        self._archives_placeholder = Adw.ActionRow()
        self._archives_placeholder.set_title("Загрузка архивов...")
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_valign(Gtk.Align.CENTER)
        self._archives_placeholder.add_suffix(spinner)
        self._archives_group.add(self._archives_placeholder)

    def _build_actions_group(self):
        self._actions_group = Adw.PreferencesGroup()
        self._actions_group.set_title("Действия")
        self._body.append(self._actions_group)

        self._btn_create = make_button("Создать сейчас", width=150)
        self._btn_create.set_valign(Gtk.Align.CENTER)
        self._btn_create.connect("clicked", self._on_create_archive)
        row_create = Adw.ActionRow()
        row_create.set_title("Создать резервную копию")
        row_create.set_subtitle("Архивирует все выбранные источники")
        row_create.add_prefix(make_icon("document-save-symbolic"))
        row_create.add_suffix(self._btn_create)
        self._actions_group.add(row_create)

        btn_check = make_button("Проверить", width=120, style="flat")
        btn_check.set_valign(Gtk.Align.CENTER)
        btn_check.connect("clicked", self._on_check)
        row_check = Adw.ActionRow()
        row_check.set_title("Проверить целостность")
        row_check.set_subtitle("Верифицирует данные в хранилище")
        row_check.add_prefix(make_icon("security-high-symbolic"))
        row_check.add_suffix(btn_check)
        self._actions_group.add(row_check)

        btn_prune = make_button("Очистить", width=120, style="flat")
        btn_prune.set_valign(Gtk.Align.CENTER)
        btn_prune.connect("clicked", self._on_prune)
        row_prune = Adw.ActionRow()
        row_prune.set_title("Удалить устаревшие архивы")
        row_prune.set_subtitle("Согласно настройкам хранения")
        row_prune.add_prefix(make_icon("user-trash-symbolic"))
        row_prune.add_suffix(btn_prune)
        self._actions_group.add(row_prune)

        btn_compact = make_button("Сжать", width=140, style="flat")
        btn_compact.set_valign(Gtk.Align.CENTER)
        btn_compact.connect("clicked", self._on_compact)
        self._compact_row = Adw.ActionRow()
        self._compact_row.set_title("Сжатие хранилища")
        self._compact_row.set_subtitle("Освобождает место после удаления архивов (borg ≥ 1.2)")
        self._compact_row.add_prefix(make_icon("emblem-system-symbolic"))
        self._compact_row.add_suffix(btn_compact)
        self._actions_group.add(self._compact_row)

    def _update_sections_visibility(self):
        repo = config.state_get("borg_repo_path", "") or ""
        initialized = bool(repo)
        self._archives_group.set_visible(initialized)
        self._actions_group.set_sensitive(initialized)

    def _refresh_status_thread(self):
        installed = backend.is_borg_installed()
        version = backend.borg_version() if installed else None
        repo_path = config.state_get("borg_repo_path", "") or ""
        initialized = backend.is_repo_initialized(repo_path) if (installed and repo_path) else False
        last_backup = config.state_get("borg_last_backup", "") or ""
        next_run = backend.get_timer_next_run() if backend.is_timer_active() else None
        GLib.idle_add(self._update_status_ui, installed, version, repo_path, initialized, last_backup, next_run)

    def _update_status_ui(self, installed, version, repo_path, initialized, last_backup, next_run):
        if installed:
            self._row_borg.set_visible(False)
        else:
            self._row_borg.set_visible(True)
            self._row_borg.set_subtitle("не установлен")
            clear_status(self._icon_borg)

        if repo_path:
            self._row_repo_status.set_subtitle(repo_path)
            if initialized:
                set_status_ok(self._icon_repo)
                self._row_repo_status.set_subtitle(repo_path + " ✔")
            else:
                set_status_error(self._icon_repo)
                self._row_repo_status.set_subtitle(repo_path + " — не инициализировано")
        else:
            self._row_repo_status.set_subtitle("не настроено")
            clear_status(self._icon_repo)

        self._row_last.set_subtitle(last_backup or "никогда")
        self._row_next.set_subtitle(next_run or "расписание не задано")

        if initialized:
            self._update_sections_visibility()
            threading.Thread(target=self._load_archives_thread, daemon=True).start()
    
    def _btrfs_refresh_list(self):
        self._btrfs_loading_spinner.set_spinning(True)
        self._btrfs_loading_spinner.set_visible(True)
        backend.btrfs_snapshot_list(self._btrfs_populate_snapshots)

    def _btrfs_populate_snapshots(self, snapshots: list[dict]):
        self._btrfs_snapshots = snapshots
        self._btrfs_loading_spinner.set_spinning(False)
        self._btrfs_loading_spinner.set_visible(False)

        while self._btrfs_carousel.get_n_pages() > 0:
            self._btrfs_carousel.remove(self._btrfs_carousel.get_nth_page(0))

        if not snapshots:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("card")
            card.set_margin_start(16)
            card.set_margin_end(16)
            card.set_margin_top(8)
            card.set_margin_bottom(8)
            card.set_hexpand(True)
            lbl = Gtk.Label(label="Снимков нет")
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(24)
            lbl.set_margin_bottom(24)
            card.append(lbl)
            self._btrfs_carousel.append(card)
        else:
            for snap in snapshots:
                self._btrfs_carousel.append(self._btrfs_build_snapshot_card(snap))

        self._btrfs_update_nav_buttons()

    def _btrfs_build_snapshot_card(self, snap: dict) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        card.set_margin_start(16)
        card.set_margin_end(16)
        card.set_margin_top(8)
        card.set_margin_bottom(8)
        card.set_hexpand(True)

        icon = make_icon("camera-photo-symbolic", 32)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_margin_top(16)
        card.append(icon)

        date_lbl = Gtk.Label(label=snap["date_str"])
        date_lbl.add_css_class("title-2")
        date_lbl.set_halign(Gtk.Align.CENTER)
        card.append(date_lbl)

        size_lbl = Gtk.Label(label="…")
        size_lbl.add_css_class("dim-label")
        size_lbl.set_halign(Gtk.Align.CENTER)
        card.append(size_lbl)

        def _on_size(s):
            size_lbl.set_text(f"эксклюзивно: {_fmt_size(s)}" if s else "")
        backend.btrfs_snapshot_size(snap["path"], _on_size)

        btn_restore = Gtk.Button(label="Восстановить")
        btn_restore.add_css_class("suggested-action")
        btn_restore.add_css_class("pill")
        btn_restore.set_halign(Gtk.Align.CENTER)
        btn_restore.set_margin_bottom(16)
        btn_restore.connect("clicked", lambda _, s=snap: self._btrfs_on_restore(s))
        card.append(btn_restore)

        return card

    def _btrfs_carousel_prev(self, _btn):
        idx = round(self._btrfs_carousel.get_position())
        if idx > 0:
            self._btrfs_carousel.scroll_to(self._btrfs_carousel.get_nth_page(idx - 1), True)

    def _btrfs_carousel_next(self, _btn):
        idx = round(self._btrfs_carousel.get_position())
        n = self._btrfs_carousel.get_n_pages()
        if idx < n - 1:
            self._btrfs_carousel.scroll_to(self._btrfs_carousel.get_nth_page(idx + 1), True)

    def _btrfs_update_nav_buttons(self):
        n = self._btrfs_carousel.get_n_pages()
        self._btrfs_btn_prev.set_sensitive(n > 1)
        self._btrfs_btn_next.set_sensitive(n > 1)

    def _refresh_archives(self):
        self._archives_placeholder.set_title("Загрузка архивов...")
        threading.Thread(target=self._load_archives_thread, daemon=True).start()

    def _load_archives_thread(self):
        repo_path = config.state_get("borg_repo_path", "") or ""
        if not repo_path:
            GLib.idle_add(self._populate_archives, [])
            return
        archives, _ = backend.borg_list(repo_path)
        GLib.idle_add(self._populate_archives, list(reversed(archives)))

    def _populate_archives(self, archives: list[dict]):
        try:
            self._archives_group.remove(self._archives_placeholder)
        except Exception:
            pass

        for row in self._archive_rows:
            try:
                self._archives_group.remove(row)
            except Exception:
                pass
        self._archive_rows.clear()

        if not archives:
            self._archives_placeholder.set_title("Архивов не найдено")
            spinner_suffix = self._archives_placeholder.get_last_child()
            if spinner_suffix:
                self._archives_placeholder.remove(spinner_suffix)
            self._archives_group.add(self._archives_placeholder)
            return

        for archive in archives[:20]:
            row = self._build_archive_row(archive)
            self._archives_group.add(row)
            self._archive_rows.append(row)

        if len(archives) > 20:
            more_row = Adw.ActionRow()
            more_row.set_title(f"... и ещё {len(archives) - 20} архивов")
            more_row.add_css_class("dim-label")
            self._archives_group.add(more_row)
            self._archive_rows.append(more_row)

    def _build_archive_row(self, archive: dict) -> Adw.ExpanderRow:
        name = archive.get("name", "")
        start = (archive.get("start") or archive.get("time") or "")[:16].replace("T", " ")
        hostname = archive.get("hostname", "")
        username = archive.get("username", "")
        subtitle = hostname or username or name

        row = Adw.ExpanderRow()
        row.set_title(start or name)
        row.set_subtitle(subtitle)

        repo_path = config.state_get("borg_repo_path", "") or ""

        btn_browse = Gtk.Button(label="Просмотреть")
        btn_browse.add_css_class("flat")
        btn_browse.add_css_class("pill")
        btn_browse.set_valign(Gtk.Align.CENTER)
        btn_browse.connect("clicked", lambda _, n=name: self._show_archive_browser(n))

        btn_restore = make_button("Восстановить", width=130, style="flat")
        btn_restore.connect("clicked", lambda _, n=name, d=start: self._show_restore_dialog(n, d))

        btn_delete = Gtk.Button()
        btn_delete.set_icon_name("user-trash-symbolic")
        btn_delete.add_css_class("flat")
        btn_delete.add_css_class("destructive-action")
        btn_delete.set_valign(Gtk.Align.CENTER)
        btn_delete.set_tooltip_text("Удалить архив")
        btn_delete.connect("clicked", lambda _, n=name, d=start: self._confirm_delete_archive(n, d))

        suffix_box = make_suffix_box(btn_browse, btn_restore, btn_delete)
        row.add_suffix(suffix_box)

        info_row = Adw.ActionRow()
        info_row.set_title("Имя архива")
        info_row.set_subtitle(name)
        row.add_row(info_row)

        return row

    def _show_archive_browser(self, archive_name: str):
        repo_path = config.state_get("borg_repo_path", "") or ""
        dialog = BorgArchiveBrowserDialog(self.get_root(), repo_path, archive_name)
        dialog.present()

    def _show_restore_dialog(self, archive_name: str, archive_date: str = ""):
        repo_path = config.state_get("borg_repo_path", "") or ""
        dialog = BorgRestoreDialog(repo_path, archive_name, self._log, archive_date)
        dialog.present(self.get_root())

    def _confirm_delete_archive(self, archive_name: str, archive_date: str = ""):
        label = archive_date or archive_name
        dialog = Adw.AlertDialog(
            heading="Удалить архив?",
            body=f"Архив «{label}» будет удалён безвозвратно. Восстановить его будет невозможно.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r, n=archive_name: self._on_delete_archive_response(r, n))
        dialog.present(self.get_root())

    def _on_delete_archive_response(self, response: str, archive_name: str):
        if response != "delete":
            return
        repo_path = config.state_get("borg_repo_path", "") or ""
        self._log(f"▶  Удаление архива {archive_name}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Удаление {archive_name}...")

        def _on_done(ok):
            if hasattr(win, "stop_progress"):
                win.stop_progress(ok)
            self._log(f"{'✔  Архив удалён' if ok else '✘  Ошибка при удалении'}\n")
            if ok:
                self._refresh_archives()

        backend.borg_delete_archive(repo_path, archive_name, self._log, _on_done)

    def _on_dest_type_changed(self, widget, _):
        idx = self._dd_dest_type.get_selected()
        config.state_set("borg_dest_type", idx)
        self._ssh_section.set_visible(idx == 1)
        self._gd_section.set_visible(idx == 2)
        pick_folder_visible = idx in (0, 2)
        for child in self._row_repo_path.observe_children():
            if hasattr(child, "get_icon_name") and child.get_icon_name() == "folder-open-symbolic":
                child.set_visible(pick_folder_visible)
                break

    def _on_pick_repo_folder(self, _btn):
        try:
            fd = Gtk.FileDialog()
            fd.set_title("Выберите папку для хранилища")
            fd.select_folder(self.get_root(), None, self._on_repo_folder_selected, None)
        except AttributeError:
            fc = Gtk.FileChooserNative(
                title="Выберите папку для хранилища",
                action=Gtk.FileChooserAction.SELECT_FOLDER,
                transient_for=self.get_root(),
                accept_label="Выбрать",
                cancel_label="Отмена",
            )
            def _resp(d, r):
                if r == Gtk.ResponseType.ACCEPT:
                    self._row_repo_path.set_text(d.get_file().get_path())
                d.unref()
            fc.connect("response", _resp)
            fc.show()

    def _on_repo_folder_selected(self, dialog, result, _):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._row_repo_path.set_text(folder.get_path())
        except GLib.Error:
            pass

    def _on_tar_export_toggled(self, sw, _):
        active = sw.get_active()
        config.state_set("borg_tar_export_enabled", active)
        self._row_tar_path.set_visible(active)

    def _on_pick_tar_folder(self, _btn):
        try:
            fd = Gtk.FileDialog()
            fd.set_title("Папка для .tar-файла")
            fd.select_folder(self.get_root(), None, self._on_tar_folder_selected, None)
        except AttributeError:
            fc = Gtk.FileChooserNative(
                title="Папка для .tar-файла",
                action=Gtk.FileChooserAction.SELECT_FOLDER,
                transient_for=self.get_root(),
                accept_label="Выбрать",
                cancel_label="Отмена",
            )
            def _resp(d, r):
                if r == Gtk.ResponseType.ACCEPT:
                    p = d.get_file().get_path()
                    self._row_tar_path.set_text(p)
                    config.state_set("borg_tar_export_path", p)
                d.unref()
            fc.connect("response", _resp)
            fc.show()

    def _on_tar_folder_selected(self, dialog, result, _):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                p = folder.get_path()
                self._row_tar_path.set_text(p)
                config.state_set("borg_tar_export_path", p)
        except GLib.Error:
            pass

    def _save_repo_settings(self):
        config.state_set("borg_repo_path", self._row_repo_path.get_text().strip())
        config.state_set("borg_passphrase", self._row_passphrase.get_text())
        if self._stack.get_visible_child_name() == "timemachine":
            GLib.idle_add(self._tm_refresh_archives)

    def _on_install_borg(self, _btn):
        self._btn_install.set_sensitive(False)
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Установка borg...")
        self._log("\n▶  Установка borg...\n")
        backend.run_epm(
            ["epm", "install", "-y", "borg"],
            self._log,
            lambda ok: (
                self._log("✔ Borg установлен\n" if ok else "✘ Ошибка установки\n"),
                self._btn_install.set_sensitive(True),
                hasattr(win, "stop_progress") and win.stop_progress(ok),
                self._refresh_status_thread(),
            )
        )

    def _on_init_repo(self, _):
        repo_path = self._row_repo_path.get_text().strip()
        if not repo_path:
            return
        self._save_repo_settings()
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Инициализация хранилища...")
        self._log(f"\n▶  Инициализация хранилища в {repo_path}...\n")
        backend.borg_init(
            repo_path, self._log,
            lambda ok: (
                self._log("✔ Готово\n" if ok else "✘ Ошибка\n"),
                hasattr(win, "stop_progress") and win.stop_progress(ok),
                self._refresh_status_thread() if ok else None,
            )
        )

    def _get_backup_opts(self) -> dict:
        opts = {}
        paths = []
        if config.state_get("borg_src_altbooster", True):
            paths.append(str(config.CONFIG_DIR))
        if config.state_get("borg_src_home", False):
            opts["home_dirs"] = config.state_get("borg_home_dirs", [])
        if config.state_get("borg_src_extensions", True):
            opts["extensions"] = True
        if config.state_get("borg_src_flatpak_apps", True):
            opts["flatpak_apps"] = True
            opts["flatpak_apps_source"] = config.state_get("borg_src_flatpak_apps_source", 0)
        if config.state_get("borg_src_flatpak_remotes", True):
            opts["flatpak_remotes"] = True
        if config.state_get("borg_src_flatpak_data", True):
            opts["flatpak_data"] = True
            opts["flatpak_data_filter"] = config.state_get("borg_flatpak_data_filter", None)
        opts["custom_paths"] = config.state_get("borg_custom_paths", [])
        opts["paths"] = paths
        return opts

    def _on_create_archive(self, _):
        repo_path = config.state_get("borg_repo_path", "")
        if not repo_path: return
        opts = self._get_backup_opts()
        dialog = BorgBackupSummaryDialog(self.get_root(), repo_path, opts, self._do_create_archive)
        dialog.present()

    def _do_create_archive(self):
        repo_path = config.state_get("borg_repo_path", "")
        if not repo_path: return

        opts = self._get_backup_opts()
        paths: list[str] = list(opts.get("paths", []))
        home = Path.home()
        if opts.get("home_dirs"):
            paths.extend(str(home / d) for d in opts["home_dirs"])
        if opts.get("custom_paths"):
            paths.extend(opts["custom_paths"])

        meta_dir = Path("/tmp/altbooster-backup-meta")
        if opts.get("flatpak_apps") or opts.get("flatpak_remotes"):
            backend.generate_flatpak_meta(meta_dir, opts.get("flatpak_apps_source"))
        if opts.get("extensions"):
            backend.generate_extensions_meta(meta_dir)
        backend.generate_system_meta(meta_dir)
        if meta_dir.exists():
            paths.append(str(meta_dir))

        if not paths:
            self._log("\n⚠ Нечего сохранять. Выберите источники на вкладке 'Настройки'.\n")
            return

        archive_name = socket.gethostname() + "-" + GLib.DateTime.new_now_local().format("%Y-%m-%dT%H-%M")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Создание архива {archive_name}...")
        self._log(f"\n▶  Создание архива {archive_name}...\n")

        def _done(ok):
            if hasattr(win, "stop_progress"):
                win.stop_progress(ok)
            self._log(f"{'✔  Архив создан' if ok else '✘  Ошибка при создании архива'}\n")
            if ok:
                config.state_set("borg_last_backup", GLib.DateTime.new_now_local().format("%d.%m.%Y %H:%M"))
                self._refresh_status_thread()
                GLib.idle_add(self._tm_refresh_archives)

        backend.borg_create(repo_path, archive_name, paths, backend.DEFAULT_EXCLUDES, self._log, _done)


    def _on_check(self, _):
        repo_path = config.state_get("borg_repo_path", "")
        if not repo_path: return
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Проверка хранилища...")
        self._log("\n▶  Проверка целостности хранилища...\n")
        backend.borg_check(
            repo_path, self._log,
            lambda ok: (
                self._log("✔ Проверка завершена\n" if ok else "✘ Ошибка при проверке\n"),
                hasattr(win, "stop_progress") and win.stop_progress(ok),
            )
        )

    def _on_prune(self, _):
        repo_path = config.state_get("borg_repo_path", "")
        if not repo_path: return
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Удаление старых архивов...")
        self._log("\n▶  Удаление старых архивов...\n")
        backend.borg_prune(
            repo_path,
            config.state_get("borg_keep_daily", 7),
            config.state_get("borg_keep_weekly", 4),
            config.state_get("borg_keep_monthly", 6),
            self._log,
            lambda ok: (
                self._log("✔ Очистка завершена\n" if ok else "✘ Ошибка при очистке\n"),
                hasattr(win, "stop_progress") and win.stop_progress(ok),
                self._refresh_archives() if ok else None,
            )
        )

    def _on_compact(self, _):
        repo_path = config.state_get("borg_repo_path", "")
        if not repo_path: return
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Сжатие хранилища...")
        self._log("\n▶  Сжатие хранилища...\n")
        backend.borg_compact(
            repo_path, self._log,
            lambda ok: (
                self._log("✔ Сжатие завершено\n" if ok else "✘ Ошибка при сжатии\n"),
                hasattr(win, "stop_progress") and win.stop_progress(ok),
            )
        )

    def _on_gen_key(self, btn):
        btn.set_sensitive(False)
        ok = backend.borg_generate_ssh_key()
        if ok:
            pubkey = backend.borg_get_pubkey()
            if pubkey:
                self._row_pubkey.set_subtitle(pubkey[:64] + "…")
                self._btn_copy_key.set_sensitive(True)
                self._btn_gen_key.set_label("Пересоздать ключ")
        btn.set_sensitive(True)

    def _on_copy_pubkey(self, _):
        pubkey = backend.borg_get_pubkey()
        if pubkey:
            Gdk.Display.get_default().get_clipboard().set_text(pubkey)

    def _on_detect_gd(self, btn):
        btn.set_sensitive(False)
        path = backend.find_gvfs_google_drive()
        if path:
            self._row_repo_path.set_text(path + "/ALTBoosterBackup")
        btn.set_sensitive(True)

    def _open_goa(self):
        try:
            subprocess.Popen(["gnome-control-center", "online-accounts"])
        except Exception as e:
            self._log(f"✘ Ошибка открытия настроек: {e}\n")

    def _on_home_toggled(self, sw, _):
        config.state_set("borg_src_home", sw.get_active())
        self._update_home_subtitle()

    def _update_home_subtitle(self):
        active = config.state_get("borg_src_home", False)
        if not active:
            self._row_home.set_subtitle("")
            return
        dirs = config.state_get("borg_home_dirs", [])
        if not dirs:
            self._row_home.set_subtitle("Ничего не выбрано")
        else:
            self._row_home.set_subtitle(", ".join(dirs))

    def _on_pick_home_dirs(self, _):
        home = Path.home()
        try:
            all_dirs = [d.name for d in home.iterdir() if d.is_dir() and not d.name.startswith(".")]
        except Exception:
            all_dirs = _XDG_HOME_DEFAULTS
        selected = config.state_get("borg_home_dirs", [])
        dialog = HomeDirPickerDialog(self.get_root(), all_dirs, selected, self._on_home_dirs_picked)
        dialog.present()

    def _on_home_dirs_picked(self, dirs):
        config.state_set("borg_home_dirs", dirs)
        self._update_home_subtitle()

    def _on_pick_config_dirs(self, _):
        home = Path.home()
        config_dir = home / ".config"
        try:
            all_dirs = [d.name for d in config_dir.iterdir() if d.is_dir()]
        except Exception:
            all_dirs = []
        selected = config.state_get("borg_config_dirs", [])
        dialog = FolderPickerDialog(self.get_root(), "Папки в ~/.config", all_dirs, selected, self._on_config_dirs_picked)
        dialog.present()

    def _update_config_subtitle(self):
        dirs = config.state_get("borg_config_dirs", [])
        self._row_config.set_subtitle(f"Выбрано {len(dirs)} папок" if dirs else "Выбрать для бэкапа")

    def _on_config_dirs_picked(self, dirs):
        config.state_set("borg_config_dirs", dirs)
        self._update_config_subtitle()
    
    def _on_flatpak_data_toggled(self, sw, _):
        config.state_set("borg_src_flatpak_data", sw.get_active())
        self._update_flatpak_data_subtitle()

    def _update_flatpak_data_subtitle(self):
        active = config.state_get("borg_src_flatpak_data", False)
        if not active:
            self._row_flatpak_data.set_subtitle("Данные приложений не будут сохранены")
            return
        flt = config.state_get("borg_flatpak_data_filter", None)
        if flt is None:
            self._row_flatpak_data.set_subtitle("Все данные")
        else:
            self._row_flatpak_data.set_subtitle(f"Выбрано {len(flt)} приложений")

    def _on_pick_flatpak_data_dirs(self, _):
        var_app = Path.home() / ".var" / "app"
        try:
            all_dirs = [p.name for p in var_app.iterdir() if p.is_dir()] if var_app.exists() else []
        except Exception:
            all_dirs = []
        selected = config.state_get("borg_flatpak_data_filter", all_dirs)
        
        icons_thread = threading.Thread(target=self._load_flatpak_icons, args=(all_dirs, selected), daemon=True)
        icons_thread.start()

    def _load_flatpak_icons(self, all_dirs, selected):
        icons = _build_icon_index()
        GLib.idle_add(self._show_flatpak_data_dialog, all_dirs, selected, icons)

    def _show_flatpak_data_dialog(self, all_dirs, selected, icons):
        dialog = FlatpakDataPickerDialog(self.get_root(), all_dirs, selected, self._on_flatpak_data_picked, icons)
        dialog.present()

    def _on_flatpak_data_picked(self, dirs):
        var_app = Path.home() / ".var" / "app"
        try:
            all_dirs = [p.name for p in var_app.iterdir() if p.is_dir()] if var_app.exists() else []
        except Exception:
            all_dirs = []
        
        if sorted(dirs) == sorted(all_dirs):
            config.state_set("borg_flatpak_data_filter", None)
        else:
            config.state_set("borg_flatpak_data_filter", dirs)
        self._update_flatpak_data_subtitle()

    def _on_add_custom_path(self, row):
        path = row.get_text().strip()
        if not path:
            return
        row.set_text("")
        current = config.state_get("borg_custom_paths", [])
        if path not in current:
            current.append(path)
            config.state_set("borg_custom_paths", current)
            self._add_custom_path_row(path)
    
    def _on_pick_custom_path(self, _btn):
        try:
            fd = Gtk.FileDialog()
            fd.set_title("Выберите папку или файл")
            fd.open(self.get_root(), None, self._on_custom_path_selected, None)
        except AttributeError:
            fc = Gtk.FileChooserNative(
                title="Выберите папку или файл",
                action=Gtk.FileChooserAction.OPEN,
                transient_for=self.get_root(),
                accept_label="Выбрать",
                cancel_label="Отмена",
            )
            def _resp(d, r):
                if r == Gtk.ResponseType.ACCEPT:
                    self._add_entry_row.set_text(d.get_file().get_path())
                d.unref()
            fc.connect("response", _resp)
            fc.show()

    def _on_custom_path_selected(self, dialog, result, _):
        try:
            f = dialog.open_finish(result)
            if f:
                self._add_entry_row.set_text(f.get_path())
        except GLib.Error:
            pass

    def _add_custom_path_row(self, path: str):
        row = Adw.ActionRow(title=path)
        del_btn = Gtk.Button(icon_name="user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.connect("clicked", self._on_del_custom_path, path, row)
        row.add_suffix(del_btn)
        self._grp_custom.add(row)

    def _on_del_custom_path(self, _, path: str, row: Adw.ActionRow):
        current = config.state_get("borg_custom_paths", [])
        if path in current:
            current.remove(path)
            config.state_set("borg_custom_paths", current)
        self._grp_custom.remove(row)

    def _on_schedule_toggled(self, sw, _):
        active = sw.get_active()
        self._log(f"\n▶  {'Включение' if active else 'Отключение'} расписания...\n")
        if active:
            expr = self._build_calendar_expr()
            backend.write_systemd_units(
                config.state_get("borg_repo_path", ""),
                self._get_backup_opts().get("paths", []),
                expr,
            )
            ok = backend.enable_systemd_timer()
        else:
            ok = backend.disable_systemd_timer()
        sw.set_active(ok if active else not ok)
        self._log(f"✔  Готово\n" if ok else "✘  Ошибка\n")
        self._refresh_status_thread()
        
    def _build_btrfs_tab(self):
        scroll, body = make_scrolled_page()

        status_group = Adw.PreferencesGroup(title="Статус")
        body.append(status_group)

        row_btrfs_ok = Adw.ActionRow(title="$HOME находится на Btrfs", subtitle=backend.get_btrfs_mount_for_home())
        row_btrfs_ok.add_prefix(make_icon("emblem-ok-symbolic"))
        status_group.add(row_btrfs_ok)

        self._btrfs_snapshots_dir_row = Adw.ActionRow(title="Папка для снимков", subtitle=str(backend.get_snapshots_dir()))
        self._btrfs_snapshots_dir_row.add_prefix(make_icon("folder-symbolic"))
        status_group.add(self._btrfs_snapshots_dir_row)

        snapshots_label = Gtk.Label(label="Снимки")
        snapshots_label.add_css_class("heading")
        snapshots_label.set_halign(Gtk.Align.START)
        snapshots_label.set_margin_top(16)
        snapshots_label.set_margin_start(4)
        body.append(snapshots_label)

        self._btrfs_loading_spinner = Gtk.Spinner()
        self._btrfs_loading_spinner.set_halign(Gtk.Align.CENTER)
        self._btrfs_loading_spinner.set_margin_top(8)
        self._btrfs_loading_spinner.set_visible(False)
        body.append(self._btrfs_loading_spinner)

        self._btrfs_snapshots = []
        self._btrfs_carousel = Adw.Carousel()
        self._btrfs_carousel.set_hexpand(True)
        self._btrfs_carousel.set_spacing(8)
        self._btrfs_carousel.set_allow_scroll_wheel(True)
        self._btrfs_carousel.connect("page-changed", lambda _c, _i: self._btrfs_update_nav_buttons())

        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(self._btrfs_carousel)
        dots.set_halign(Gtk.Align.CENTER)

        self._btrfs_btn_prev = Gtk.Button(icon_name="go-previous-symbolic")
        self._btrfs_btn_prev.add_css_class("circular")
        self._btrfs_btn_prev.set_valign(Gtk.Align.CENTER)
        self._btrfs_btn_prev.connect("clicked", self._btrfs_carousel_prev)

        self._btrfs_btn_next = Gtk.Button(icon_name="go-next-symbolic")
        self._btrfs_btn_next.add_css_class("circular")
        self._btrfs_btn_next.set_valign(Gtk.Align.CENTER)
        self._btrfs_btn_next.connect("clicked", self._btrfs_carousel_next)

        carousel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        carousel_row.append(self._btrfs_btn_prev)
        carousel_row.append(self._btrfs_carousel)
        carousel_row.append(self._btrfs_btn_next)
        body.append(carousel_row)
        body.append(dots)

        btns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns_row.set_halign(Gtk.Align.CENTER)
        btns_row.set_margin_top(8)

        create_btn = make_button("Создать снимок сейчас")
        create_btn.connect("clicked", self._btrfs_on_create)
        btns_row.append(create_btn)

        self._btrfs_delete_btn = Gtk.Button(label="Удалить снимки")
        self._btrfs_delete_btn.add_css_class("destructive-action")
        self._btrfs_delete_btn.add_css_class("pill")
        self._btrfs_delete_btn.connect("clicked", self._btrfs_on_delete_multi)
        btns_row.append(self._btrfs_delete_btn)

        body.append(btns_row)

        schedule_group = Adw.PreferencesGroup(title="Расписание")
        schedule_group.set_margin_top(16)
        body.append(schedule_group)
        
        self._btrfs_sw_auto = Adw.SwitchRow(title="Автоматические снимки")
        self._btrfs_sw_auto.set_active(config.state_get("btrfs_auto_enabled", False))
        self._btrfs_sw_auto.connect("notify::active", self._btrfs_on_auto_toggled)
        schedule_group.add(self._btrfs_sw_auto)
        
        interval_model = Gtk.StringList.new([label for _, label in _BTRFS_INTERVALS])
        self._btrfs_interval_row = Adw.ComboRow(title="Интервал", model=interval_model)
        saved_interval = config.state_get("btrfs_auto_interval_hours", 1)
        try:
            idx = [val for val, _ in _BTRFS_INTERVALS].index(saved_interval)
            self._btrfs_interval_row.set_selected(idx)
        except ValueError:
            self._btrfs_interval_row.set_selected(0)
        self._btrfs_interval_row.connect("notify::selected", self._btrfs_on_interval_changed)
        schedule_group.add(self._btrfs_interval_row)
        
        self._btrfs_keep_row = Adw.SpinRow.new_with_range(1, 1000, 1)
        self._btrfs_keep_row.set_title("Хранить снимков")
        self._btrfs_keep_row.set_value(config.state_get("btrfs_keep_count", 24))
        self._btrfs_keep_row.connect("notify::value", self._btrfs_on_keep_count_changed)
        schedule_group.add(self._btrfs_keep_row)
        
        self._btrfs_update_schedule_ui(self._btrfs_sw_auto.get_active())
        scroll.connect("map", lambda _: self._btrfs_refresh_list())

        return scroll, body
        
    def _btrfs_update_schedule_ui(self, active: bool):
        self._btrfs_interval_row.set_sensitive(active)
        self._btrfs_keep_row.set_sensitive(active)
        
    def _btrfs_on_create(self, _btn):
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Создание Btrfs снимка...")
        self._log("\n▶  Создание Btrfs снимка...\n")
        
        def on_done(ok):
            if hasattr(win, "stop_progress"):
                win.stop_progress(ok)
            self._log("✔  Снимок создан\n" if ok else "✘  Ошибка при создании снимка\n")
            if ok:
                self._btrfs_prune_old()
                self._btrfs_refresh_list()
                
        backend.btrfs_snapshot_create(self._log, on_done)
        
    def _btrfs_on_delete_multi(self, _btn):
        snapshots = getattr(self, "_btrfs_snapshots", [])
        if not snapshots:
            return

        dialog = Adw.AlertDialog(heading="Удалить снимки")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        list_box.add_css_class("boxed-list")
        list_box.set_margin_top(8)

        checks = []
        for snap in snapshots:
            row = Adw.ActionRow(title=snap["date_str"])
            cb = Gtk.CheckButton()
            cb.set_valign(Gtk.Align.CENTER)
            row.add_prefix(cb)
            row.set_activatable_widget(cb)
            list_box.append(row)
            checks.append((cb, snap))

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(300)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(list_box)

        dialog.set_extra_child(scroll)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить выбранные")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def _on_response(_d, response):
            if response != "delete":
                return
            to_delete = [snap for cb, snap in checks if cb.get_active()]
            if not to_delete:
                return
            self._btrfs_delete_batch(to_delete)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _btrfs_delete_batch(self, snapshots: list[dict]):
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Удаление {len(snapshots)} снимков...")
        remaining = list(snapshots)

        def _delete_next():
            if not remaining:
                if hasattr(win, "stop_progress"):
                    win.stop_progress(True)
                self._btrfs_refresh_list()
                return
            snap = remaining.pop(0)
            self._log(f"▶  Удаление {snap['name']}...\n")

            def _on_done(ok):
                self._log("✔  Удалён\n" if ok else "✘  Ошибка\n")
                _delete_next()

            backend.btrfs_snapshot_delete(snap["path"], self._log, _on_done)

        _delete_next()

    def _btrfs_on_restore(self, snapshot: dict):
        dialog = BtrfsRestoreDialog(self.get_root(), snapshot, self._log)
        dialog.present()
        
    def _btrfs_on_auto_toggled(self, sw, _):
        active = sw.get_active()
        config.state_set("btrfs_auto_enabled", active)
        self._btrfs_update_schedule_ui(active)
        self._btrfs_apply_schedule_changes()

    def _btrfs_on_interval_changed(self, row, _):
        idx = row.get_selected()
        interval = _BTRFS_INTERVALS[idx][0]
        config.state_set("btrfs_auto_interval_hours", interval)
        self._btrfs_apply_schedule_changes()
        
    def _btrfs_on_keep_count_changed(self, row, _):
        config.state_set("btrfs_keep_count", row.get_value())
        self._btrfs_apply_schedule_changes()
        
    def _btrfs_apply_schedule_changes(self):
        active = config.state_get("btrfs_auto_enabled", False)
        if active:
            self._log("\n▶  Применение расписания для Btrfs снимков...\n")
            interval = config.state_get("btrfs_auto_interval_hours", 1)
            keep = config.state_get("btrfs_keep_count", 24)
            backend.write_btrfs_systemd_units(interval, keep)
            ok = backend.enable_btrfs_timer()
            self._log("✔  Расписание включено\n" if ok else "✘  Ошибка включения расписания\n")
        else:
            self._log("\n▶  Отключение расписания для Btrfs снимков...\n")
            ok = backend.disable_btrfs_timer()
            self._log("✔  Расписание выключено\n" if ok else "✘  Ошибка выключения расписания\n")

    def _btrfs_prune_old(self):
        # This is a best-effort, fire-and-forget prune
        def on_done(snapshots: list[dict]):
            keep_count = config.state_get("btrfs_keep_count", 24)
            if len(snapshots) > keep_count:
                to_delete = snapshots[keep_count:]
                self._log(f"\nℹ️  Удаление {len(to_delete)} старых снимков...\n")
                for snap in to_delete:
                    backend.btrfs_snapshot_delete(snap['path'], lambda l: None, lambda ok: None)

        backend.btrfs_snapshot_list(on_done)
