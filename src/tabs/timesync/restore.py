from __future__ import annotations

import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from core import backend
from ui.widgets import make_icon
from .summary import _fmt_size


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
        self._dir_sizes = self._compute_dir_sizes(items)
        self._navigate_to("")

    def _compute_dir_sizes(self, items: list[dict]) -> dict:
        sizes = {}
        for item in items:
            if item.get("type", "-") != "-":
                continue
            path = item.get("path", "").rstrip("/")
            size = item.get("size") or 0
            parts = path.split("/")
            for i in range(len(parts)):
                key = "/".join(parts[:i])
                sizes[key] = sizes.get(key, 0) + size
        return sizes

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
            dir_size = self._dir_sizes.get(d, 0)
            subtitle = f"{count} элем."
            if dir_size:
                subtitle += f"  ·  {_fmt_size(dir_size)}"
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(subtitle)
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
