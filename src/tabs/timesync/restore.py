from __future__ import annotations

import shutil
import tempfile
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

        loading_label = Gtk.Label(label="Дайте минутку! Согласую выше, что данный архив точно ваш.")
        loading_label.add_css_class("dim-label")
        loading_label.set_halign(Gtk.Align.CENTER)
        loading_label.set_wrap(True)
        loading_label.set_justify(Gtk.Justification.CENTER)
        loading_label.set_max_width_chars(52)

        loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        loading_box.set_valign(Gtk.Align.CENTER)
        loading_box.set_halign(Gtk.Align.CENTER)
        loading_box.set_vexpand(True)
        loading_box.set_margin_start(20)
        loading_box.set_margin_end(20)
        loading_box.append(self._spinner)
        loading_box.append(loading_label)

        self._stack = Gtk.Stack()
        self._stack.add_named(loading_box, "loading")
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
        initial_path = self._guess_initial_path()
        if initial_path:
            parts = initial_path.split("/")
            self._nav_stack = ["/".join(parts[:i]) for i in range(len(parts) - 1)]
        self._navigate_to(initial_path)

    def _guess_initial_path(self) -> str:
        root = self._children.get("", {"dirs": [], "files": []})
        if root.get("files"):
            return ""

        root_dirs = root.get("dirs", [])
        if not root_dirs:
            return ""

        # Типичный Borg-путь: /home/<user>/... + служебный /tmp/altbooster-backup-meta.
        if "home" in root_dirs:
            home_node = self._children.get("home", {"dirs": [], "files": []})
            home_dirs = home_node.get("dirs", [])
            if len(home_dirs) == 1 and not home_node.get("files"):
                return home_dirs[0]

        # Если есть один явный контейнер без файлов в корне — открываем его.
        if len(root_dirs) == 1:
            only = root_dirs[0]
            only_node = self._children.get(only, {"dirs": [], "files": []})
            if not only_node.get("files"):
                return only

        return ""

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
        self._cleanup_dir: Path | None = None
        self._repo_path = repo_path
        self._archive_name = archive_name
        self._log = log_fn

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(8)
        box.set_size_request(560, -1)

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
        self._cb_packages_only_missing = Gtk.CheckButton(label="Только отсутствующие RPM-пакеты")
        self._cb_packages_only_missing.set_active(True)
        self._cb_packages_only_missing.set_sensitive(self._cb_packages.get_active())
        self._cb_packages.connect(
            "toggled",
            lambda cb: self._cb_packages_only_missing.set_sensitive(cb.get_active()),
        )
        self._cb_dconf = Gtk.CheckButton(label="Восстановить настройки GNOME (dconf)")
        self._cb_dconf.set_active(True)
        box.append(self._cb_altbooster)
        box.append(self._cb_flatpak)
        box.append(self._cb_files)
        box.append(self._cb_packages)
        box.append(self._cb_packages_only_missing)
        box.append(self._cb_dconf)
        self._rpm_meta_hint = Gtk.Label(label="Проверка наличия списка RPM-пакетов в архиве...")
        self._rpm_meta_hint.set_halign(Gtk.Align.START)
        self._rpm_meta_hint.add_css_class("caption")
        self._rpm_meta_hint.add_css_class("dim-label")
        box.append(self._rpm_meta_hint)

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

        warn = Gtk.Label(label="⚠ TimeSync извлечёт файлы относительно выбранной папки")
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
        self._check_rpm_meta_async()

    def _check_rpm_meta_async(self):
        def _worker():
            has_packages_meta = False
            try:
                items = backend.borg_list_archive(self._repo_path, self._archive_name)
                for item in items:
                    p = (item.get("path") or "").rstrip("/")
                    if p.endswith("tmp/altbooster-backup-meta/packages.txt") or p.endswith("altbooster-backup-meta/packages.txt"):
                        has_packages_meta = True
                        break
            except Exception:
                has_packages_meta = False
            GLib.idle_add(self._apply_rpm_meta_state, has_packages_meta)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_rpm_meta_state(self, has_packages_meta: bool):
        if has_packages_meta:
            self._rpm_meta_hint.set_text("Список RPM-пакетов найден: можно включить переустановку.")
            self._cb_packages.set_visible(True)
            self._cb_packages_only_missing.set_visible(True)
            self._cb_packages.set_sensitive(True)
            self._cb_packages_only_missing.set_sensitive(self._cb_packages.get_active())
            return

        self._rpm_meta_hint.set_text("Список RPM-пакетов в архиве не найден: пункт переустановки скрыт.")
        self._cb_packages.set_active(False)
        self._cb_packages.set_visible(False)
        self._cb_packages_only_missing.set_active(False)
        self._cb_packages_only_missing.set_visible(False)

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
        self._ask_user_rename_and_restore(target_dir)

    def _ask_user_rename_and_restore(self, target_dir: str):
        current_user = Path.home().name
        detected_target_user = self._detect_home_user_from_path(target_dir)
        if detected_target_user and detected_target_user != current_user:
            self._prompt_archive_user_dialog(
                target_dir,
                detected_target_user=detected_target_user,
                suggested_archive_user=current_user,
            )
            return

        ask = Adw.AlertDialog(
            heading="Проверка пользователя",
            body=(
                "Имя пользователя в архиве совпадает с текущим?\n\n"
                "Если нет, ALT Booster выполнит безопасную временную распаковку "
                "и перенесёт данные в текущий HOME без изменения исходного архива."
            ),
        )
        ask.add_response("cancel", "Отмена")
        ask.add_response("same", "Да, совпадает")
        ask.add_response("other", "Нет, другое имя")
        ask.set_default_response("same")
        ask.set_close_response("cancel")

        def _on_ask_response(_d, resp):
            if resp == "cancel":
                return
            if resp == "same":
                self._start_restore(target_dir, archive_user=None)
                return
            self._prompt_archive_user_dialog(target_dir)

        ask.connect("response", _on_ask_response)
        ask.present(self.get_root())

    def _detect_home_user_from_path(self, target_dir: str) -> str | None:
        try:
            parts = Path(target_dir).expanduser().parts
            if len(parts) >= 3 and parts[0] == "/" and parts[1] == "home":
                return parts[2]
        except Exception:
            pass
        return None

    def _prompt_archive_user_dialog(
        self,
        target_dir: str,
        detected_target_user: str | None = None,
        suggested_archive_user: str | None = None,
    ):
        if detected_target_user:
            body = (
                f"Вы выбрали путь «/home/{detected_target_user}», он отличается от текущего пользователя.\n\n"
                "Укажите имя пользователя, под которым был создан архив."
            )
        else:
            body = "Укажите имя пользователя, под которым был создан архив (например: olduser)."

        prompt = Adw.AlertDialog(
            heading="Введите старое имя пользователя",
            body=body,
        )
        entry = Gtk.Entry()
        entry.set_placeholder_text("olduser")
        if suggested_archive_user:
            entry.set_text(suggested_archive_user)
            entry.select_region(0, -1)
        prompt.set_extra_child(entry)
        prompt.add_response("cancel", "Отмена")
        prompt.add_response("continue", "Продолжить")
        prompt.set_default_response("continue")
        prompt.set_close_response("cancel")

        def _on_prompt_response(_p, r2):
            if r2 != "continue":
                return
            archive_user = entry.get_text().strip()
            if not archive_user:
                self._log("✘  Имя пользователя не указано. Восстановление отменено.\n")
                return
            self._start_restore(target_dir, archive_user=archive_user)

        prompt.connect("response", _on_prompt_response)
        prompt.present(self.get_root())

    def _start_restore(self, target_dir: str, archive_user: str | None = None):
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Восстановление архива...")
        self._log(f"\n▶  Восстановление {self._archive_name} → {target_dir}...\n")

        restore_flatpak = self._cb_flatpak.get_active()
        restore_packages = self._cb_packages.get_active()
        restore_packages_only_missing = self._cb_packages_only_missing.get_active()
        restore_dconf = self._cb_dconf.get_active()
        use_user_remap = bool(archive_user)

        def _run_post_steps(meta_dir: Path):
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
                    backend.restore_packages_meta(
                        meta_dir,
                        self._log,
                        _step_dconf,
                        only_missing=restore_packages_only_missing,
                    )
                else:
                    _step_dconf(True)

            if restore_flatpak and meta_dir.exists():
                backend.restore_flatpak_meta(meta_dir, self._log, _step_packages)
            else:
                _step_packages(True)

        def _done(ok, extracted_root: Path):
            if not ok:
                GLib.idle_add(self._finish, False, win)
                return

            meta_dir = extracted_root / "tmp" / "altbooster-backup-meta"
            if not meta_dir.exists():
                meta_dir = extracted_root / "altbooster-backup-meta"

            _run_post_steps(meta_dir)

        def _copy_tree_merge(src: Path, dst: Path):
            for item in src.iterdir():
                target = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, target, symlinks=True, dirs_exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target, follow_symlinks=False)

        def _run_user_remap_flow(tmp_root: Path):
            def _worker():
                try:
                    home_root = tmp_root / "home"
                    src_home = home_root / archive_user if archive_user else None
                    if (not src_home) or (not src_home.exists()):
                        candidates = [d for d in home_root.iterdir() if d.is_dir()] if home_root.exists() else []
                        if len(candidates) == 1:
                            src_home = candidates[0]
                    if not src_home or not src_home.exists():
                        GLib.idle_add(self._log, "✘  Не удалось найти домашний каталог в архиве для переноса.\n")
                        GLib.idle_add(self._finish, False, win)
                        return
                    GLib.idle_add(self._log, f"▶  Перенос данных пользователя из {src_home} в {target_dir}...\n")
                    _copy_tree_merge(src_home, Path(target_dir))
                    GLib.idle_add(self._log, "   ✔ Данные пользователя перенесены\n")
                    self._cleanup_dir = tmp_root
                    GLib.idle_add(_done, True, tmp_root)
                except Exception as e:
                    GLib.idle_add(self._log, f"✘  Ошибка переноса данных пользователя: {e}\n")
                    GLib.idle_add(self._finish, False, win)

            threading.Thread(target=_worker, daemon=True).start()

        if use_user_remap:
            tmp_root = Path(tempfile.mkdtemp(prefix="altbooster-restore-"))
            self._log(f"▶  Временная распаковка архива для переноса пользователя: {tmp_root}\n")

            def _after_extract_tmp(ok):
                if not ok:
                    GLib.idle_add(self._finish, False, win)
                    try:
                        shutil.rmtree(tmp_root, ignore_errors=True)
                    except Exception:
                        pass
                    return
                _run_user_remap_flow(tmp_root)

            backend.borg_extract(
                self._repo_path, self._archive_name, str(tmp_root), [],
                self._log, _after_extract_tmp,
            )
        else:
            backend.borg_extract(
                self._repo_path, self._archive_name, target_dir, [],
                self._log, lambda ok: _done(ok, Path(target_dir)),
            )

    def _finish(self, ok, win):
        msg = "✔  Восстановление завершено!\n" if ok else "✘  Ошибка при восстановлении\n"
        self._log(msg)
        if self._cleanup_dir:
            try:
                shutil.rmtree(self._cleanup_dir, ignore_errors=True)
            except Exception:
                pass
            self._cleanup_dir = None
        if hasattr(win, "stop_progress"):
            win.stop_progress(ok)
