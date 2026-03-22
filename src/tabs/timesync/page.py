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

from core import backend
from core import config
from core.borg import _write_borg_env_file
from ui.widgets import (
    make_icon, make_scrolled_page, make_button,
    make_status_icon, set_status_ok, set_status_error, clear_status,
    make_suffix_box,
)
from .restore import BtrfsRestoreDialog, BorgArchiveBrowserDialog, BorgRestoreDialog
from .pickers import HomeDirPickerDialog, FlatpakDataPickerDialog, FolderPickerDialog
from .summary import BorgBackupSummaryDialog
from .mirror import MirrorPage
from .manual import build_terminal_page

_BTRFS_INTERVALS = [
    (1, "Каждый час"),
    (6, "Каждые 6 часов"),
    (24, "Ежедневно"),
]


class BorgPage(Gtk.Box):

    def __init__(self, log_fn, start_progress_fn=None, stop_progress_fn=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._start_progress = start_progress_fn or (lambda msg, **kw: None)
        self._stop_progress = stop_progress_fn or (lambda ok=True: None)
        self._archives_group: Adw.PreferencesGroup | None = None
        self._archive_rows: list = []
        self._compact_row = None
        self._passphrase_dialog_open = False

        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_halign(Gtk.Align.CENTER)

        top_header = Adw.HeaderBar()
        top_header.set_title_widget(switcher)
        top_header.set_show_start_title_buttons(False)
        top_header.set_show_end_title_buttons(False)
        top_header.set_decoration_layout("")

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(top_header)
        toolbar_view.set_content(self._stack)
        toolbar_view.set_vexpand(True)
        self.append(toolbar_view)

        tm_tab = self._build_timesync_tab()
        self._stack.add_titled_with_icon(tm_tab, "timesync", "TimeSync", "document-revert-symbolic")

        mirror_page = MirrorPage(self._log, self._start_progress, self._stop_progress)
        self._stack.add_titled_with_icon(mirror_page, "mirror", "Зеркало", "edit-copy-symbolic")

        manual_container, self._manual_stack = self._build_manual_container()
        self._stack.add_titled_with_icon(manual_container, "manual", "Ручной режим", "utilities-terminal-symbolic")

        self._build_info_page()
        self._build_settings_page()
        self._build_schedule_page()

        if backend.is_home_on_btrfs():
            btrfs_tab, self._btrfs_body = self._build_btrfs_tab()
            self._manual_stack.add_titled_with_icon(btrfs_tab, "btrfs", "Снимки", "camera-photo-symbolic")

        term_page = build_terminal_page(self._log)
        self._manual_stack.add_titled_with_icon(term_page, "terminal", "Терминал", "utilities-terminal-symbolic")

        self._page_info = self._manual_stack.get_page(self._manual_stack.get_child_by_name("info"))
        self._page_settings = self._manual_stack.get_page(self._manual_stack.get_child_by_name("settings"))
        self._page_schedule = self._manual_stack.get_page(self._manual_stack.get_child_by_name("schedule"))
        btrfs_w = self._manual_stack.get_child_by_name("btrfs")
        self._page_btrfs = self._manual_stack.get_page(btrfs_w) if btrfs_w else None

        self._fastfetch_offer_shown = False
        self._update_sections_visibility()
        threading.Thread(target=self._refresh_status_thread, daemon=True).start()

    @property
    def view_stack(self) -> Adw.ViewStack:
        return self._stack

    def _build_manual_container(self) -> tuple[Gtk.Box, Adw.ViewStack]:
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner_stack = Adw.ViewStack()
        inner_stack.set_vexpand(True)

        inner_switcher = Adw.ViewSwitcher()
        inner_switcher.set_stack(inner_stack)
        inner_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        inner_switcher.set_halign(Gtk.Align.CENTER)
        inner_switcher.set_margin_top(12)
        inner_switcher.set_margin_bottom(12)

        container.append(inner_switcher)
        container.append(inner_stack)
        return container, inner_stack

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
        self._manual_stack.add_titled_with_icon(scroll, "info", "Хранилище", "drive-harddisk-symbolic")

    def _build_settings_page(self):
        scroll, self._body = make_scrolled_page()
        self._build_sources_group()
        self._manual_stack.add_titled_with_icon(scroll, "settings", "Источники", "preferences-system-symbolic")

    def _build_schedule_page(self):
        scroll, self._body = make_scrolled_page()
        self._build_schedule_group()
        self._build_prune_group()
        self._manual_stack.add_titled_with_icon(scroll, "schedule", "График", "alarm-symbolic")

    def _build_timesync_tab(self) -> Gtk.Widget:
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

        # ── заголовок ────────────────────────────────────────────────────
        page_title = Gtk.Label(label="TimeSync")
        page_title.add_css_class("heading")
        page_title.set_halign(Gtk.Align.START)
        self._tm_box.append(page_title)

        # ── блок «Этот компьютер» (fastfetch) ────────────────────────────
        sysinfo_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sysinfo_title = Gtk.Label(label="Этот компьютер")
        sysinfo_title.add_css_class("heading")
        sysinfo_title.set_halign(Gtk.Align.START)
        sysinfo_title.set_hexpand(True)
        self._tm_sysinfo_toggle = Gtk.ToggleButton()
        self._tm_sysinfo_toggle.set_icon_name("pan-end-symbolic")
        self._tm_sysinfo_toggle.add_css_class("flat")
        self._tm_sysinfo_toggle.add_css_class("circular")
        self._tm_sysinfo_toggle.set_valign(Gtk.Align.CENTER)
        sysinfo_header.append(sysinfo_title)
        sysinfo_header.append(self._tm_sysinfo_toggle)
        self._tm_box.append(sysinfo_header)

        self._tm_sysinfo_revealer = Gtk.Revealer()
        self._tm_sysinfo_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._tm_sysinfo_revealer.set_reveal_child(False)

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

        self._tm_sysinfo_revealer.set_child(sysinfo_card)
        self._tm_box.append(self._tm_sysinfo_revealer)

        def _on_sysinfo_toggle(btn):
            expanded = btn.get_active()
            self._tm_sysinfo_revealer.set_reveal_child(expanded)
            btn.set_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")

        self._tm_sysinfo_toggle.connect("toggled", _on_sysinfo_toggle)
        threading.Thread(target=self._tm_load_sysinfo, daemon=True).start()

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
        GLib.idle_add(self._offer_fastfetch_install)

    def _offer_fastfetch_install(self):
        if self._fastfetch_offer_shown:
            return
        self._fastfetch_offer_shown = True
        dialog = Adw.AlertDialog(
            heading="fastfetch не установлен",
            body="fastfetch используется для отображения информации о системе.\n\nУстановить его сейчас?",
        )
        dialog.add_response("later", "Не сейчас")
        dialog.add_response("install", "Установить")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.connect("response", self._on_fastfetch_install_response)
        dialog.present(self.get_root())

    def _on_fastfetch_install_response(self, _dialog, response):
        if response != "install":
            return
        win = self.get_root()
        self._log("\n▶  Установка fastfetch...\n")
        if hasattr(win, "start_progress"):
            win.start_progress("Установка fastfetch...")

        def _done(ok):
            self._log("✔  fastfetch установлен!\n" if ok else "✘  Ошибка установки fastfetch\n")
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            if ok:
                threading.Thread(target=self._tm_load_sysinfo, daemon=True).start()

        backend.run_epm(["epm", "-i", "fastfetch"], self._log, _done)

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
        slot.append(self._repo_group)
        # SSH / Google Drive — только по типу назначения, не «всегда на вкладке Borg»
        self._on_dest_type_changed(self._dd_dest_type, None)

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

            _all_ok = [True]

            def _delete_next(names):
                if not names:
                    if hasattr(win, "stop_progress"):
                        GLib.idle_add(win.stop_progress, _all_ok[0])
                    GLib.idle_add(self._tm_refresh_archives)
                    return
                n = names[0]
                self._log(f"▶  Удаление {n}...\n")

                def _on_done(ok):
                    self._log("✔\n" if ok else "✘\n")
                    if not ok:
                        _all_ok[0] = False
                    _delete_next(names[1:])

                backend.borg_delete_archive(repo, n, self._log, _on_done)

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
            if error and "incorrect" in error.lower():
                GLib.idle_add(self._ask_correct_passphrase, self._tm_refresh_archives)
                return
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

        btns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns_row.set_halign(Gtk.Align.CENTER)
        btns_row.set_margin_bottom(16)

        btn_browse = Gtk.Button(label="Содержимое")
        btn_browse.add_css_class("pill")
        btn_browse.connect("clicked", lambda _, n=name: BorgArchiveBrowserDialog(self.get_root(), repo_path, n).present())
        btns_row.append(btn_browse)

        btn_restore = Gtk.Button(label="Восстановить")
        btn_restore.add_css_class("suggested-action")
        btn_restore.add_css_class("pill")
        display = f"{date_part} {time_part}".strip()
        btn_restore.connect("clicked", lambda _, n=name, d=display: self._show_restore_dialog(n, d))
        btns_row.append(btn_restore)

        card.append(btns_row)

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

        if state == "not_configured":
            self._tm_create_btn.set_visible(False)
            self._tm_delete_btn.set_visible(False)
            self._tm_placeholder.set_icon_name("drive-harddisk-symbolic")
            self._tm_placeholder.set_title("Хранилище не настроено")
            self._tm_placeholder.set_description("Настройте Borg-хранилище в разделе «Ручной режим»")
            btn = Gtk.Button(label="Перейти к настройке")
            btn.add_css_class("pill")
            btn.connect("clicked", lambda _: self._go_to_storage())
            self._tm_placeholder.set_child(btn)
        else:
            self._tm_placeholder.set_icon_name("document-revert-symbolic")
            self._tm_placeholder.set_title("Архивов пока нет")
            self._tm_placeholder.set_visible(False)
            self._tm_create_btn.set_visible(True)
            self._tm_delete_btn.set_visible(False)
            if error:
                self._log(f"borg list: {error}\n")

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

    def _go_to_storage(self):
        self._stack.set_visible_child_name("manual")
        self._manual_stack.set_visible_child_name("info")

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
        self._tm_show_exclude_dialog(repo_path)

    def _tm_show_exclude_dialog(self, repo_path: str):
        dialog = Adw.AlertDialog(heading="Что включить в бэкап?")
        dialog.set_body("Мы исключаем крупные папки, которые можно перекачать. Отметьте, что хотите сохранить:")
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("start", "Создать")
        dialog.set_default_response("start")
        dialog.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        checks = []
        for group in backend.OPTIONAL_EXCLUDES:
            row = Adw.ActionRow()
            row.set_title(group["title"])
            row.set_subtitle(group["description"])
            cb = Gtk.CheckButton()
            cb.set_valign(Gtk.Align.CENTER)
            row.add_prefix(cb)
            row.set_activatable_widget(cb)
            box.append(row)
            checks.append((cb, group["paths"]))

        dialog.set_extra_child(box)

        def _on_response(d, response):
            if response != "start":
                return
            extra_includes = {p for cb, paths in checks if cb.get_active() for p in paths}
            home = Path.home()
            opts = {
                "paths": [str(home), str(config.CONFIG_DIR)],
                "flatpak_apps": True,
                "flatpak_apps_source": 0,
                "flatpak_remotes": True,
                "extensions": True,
            }

            def _after_summary():
                if not backend.is_repo_initialized(repo_path):
                    self._tm_ask_password_and_init(repo_path, extra_includes)
                else:
                    self._tm_do_backup(extra_includes)

            summary = BorgBackupSummaryDialog(self.get_root(), repo_path, opts, _after_summary)
            summary.present()

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _ask_correct_passphrase(self, retry_fn):
        if self._passphrase_dialog_open:
            return
        self._passphrase_dialog_open = True
        dialog = Adw.AlertDialog(
            heading="Неверный пароль хранилища",
            body="Введите пароль для доступа к Borg-хранилищу.",
        )
        pw_row = Adw.PasswordEntryRow()
        pw_row.set_title("Пароль")
        pw_row.set_margin_top(8)
        dialog.set_extra_child(pw_row)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("ok", "Применить")
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def _on_response(d, response):
            self._passphrase_dialog_open = False
            if response != "ok":
                return
            config.state_set("borg_passphrase", pw_row.get_text())
            self._row_passphrase.set_text(pw_row.get_text())
            retry_fn()

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _tm_ask_password_and_init(self, repo_path: str, extra_includes: set = None):
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
                    self._tm_do_backup(extra_includes)
                else:
                    self._log("✘  Ошибка инициализации хранилища\n")

            backend.borg_init(repo_path, self._log, _on_init_done)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _tm_do_backup(self, extra_includes: set = None):
        repo_path = config.state_get("borg_repo_path", "") or ""
        if not repo_path:
            return

        home = Path.home()
        paths = [str(home), str(config.CONFIG_DIR)]

        build_artifact_paths = {p for g in backend.OPTIONAL_EXCLUDES if g["key"] == "build_artifacts" for p in g["paths"]}
        include_build = bool(extra_includes and extra_includes & build_artifact_paths)
        excludes = [p for p in backend.DEFAULT_EXCLUDES if not (extra_includes and p in extra_includes)]
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

        backend.borg_create(repo_path, archive_name, paths, excludes, self._log, _done, exclude_caches=not include_build)

    def _build_status_group(self):
        group = Adw.PreferencesGroup()
        group.set_title("Статус")
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
        self._repo_group.add(self._row_pubkey)

        self._row_ssh_hint = Adw.ActionRow()
        self._row_ssh_hint.set_title("Добавьте ключ на сервер")
        self._row_ssh_hint.set_subtitle("~/.ssh/authorized_keys на удалённом хосте")
        self._row_ssh_hint.add_prefix(make_icon("dialog-information-symbolic"))
        self._repo_group.add(self._row_ssh_hint)

        self._gd_row = Adw.ActionRow()
        self._gd_row.set_title("Google Drive через GNOME Аккаунты")
        self._gd_row.set_subtitle("Подключите аккаунт в настройках GNOME Online Accounts")
        self._gd_row.add_prefix(make_icon("user-home-symbolic"))
        self._btn_detect_gd = Gtk.Button(label="Определить")
        self._btn_detect_gd.add_css_class("pill")
        self._btn_detect_gd.set_valign(Gtk.Align.CENTER)
        self._btn_detect_gd.set_tooltip_text(
            "Подставить путь к смонтированному Google Drive (GVFS). "
            "Если пусто — один раз откройте диск в приложении «Файлы» и нажмите снова."
        )
        self._btn_detect_gd.connect("clicked", self._on_detect_gd)
        self._btn_open_goa = Gtk.Button(label="Открыть настройки")
        self._btn_open_goa.add_css_class("flat")
        self._btn_open_goa.add_css_class("pill")
        self._btn_open_goa.set_valign(Gtk.Align.CENTER)
        self._btn_open_goa.connect("clicked", lambda _: self._open_goa())
        self._gd_row.add_suffix(self._btn_open_goa)
        self._gd_row.add_suffix(self._btn_detect_gd)
        self._repo_group.add(self._gd_row)

        self._btn_init_repo = make_button("Инициализировать хранилище", width=200)
        self._btn_init_repo.set_halign(Gtk.Align.CENTER)
        self._btn_init_repo.set_margin_top(16)
        self._btn_init_repo.connect("clicked", self._on_init_repo)

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
            GLib.idle_add(self._populate_archives, [], "")
            return
        archives, error = backend.borg_list(repo_path)
        GLib.idle_add(self._populate_archives, list(reversed(archives)), error)

    def _populate_archives(self, archives: list[dict], error: str = ""):
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
            if error and "incorrect" in error.lower():
                self._ask_correct_passphrase(self._refresh_archives)
                return
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
        self._row_pubkey.set_visible(idx == 1)
        self._row_ssh_hint.set_visible(idx == 1)
        self._gd_row.set_visible(idx == 2)
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
        _write_borg_env_file()
        if self._stack.get_visible_child_name() == "timesync":
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
        if backend.is_repo_initialized(repo_path):
            self._log(f"✔ Хранилище уже инициализировано: {repo_path}\n")
            GLib.idle_add(self._refresh_status_thread)
            return
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Инициализация хранилища...")
        self._log(f"\n▶  Инициализация хранилища в {repo_path}...\n")
        def _on_init_done(ok):
            self._log("✔ Готово\n" if ok else "✘ Ошибка инициализации\n")
            if hasattr(win, "stop_progress"):
                win.stop_progress(ok)
            self._refresh_status_thread()

        backend.borg_init(repo_path, self._log, _on_init_done)

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
        win = self.get_root()
        if path:
            dest = path.rstrip("/") + "/ALTBoosterBackup"
            self._row_repo_path.set_text(dest)
            self._save_repo_settings()
            self._log(f"✔  Путь к Google Drive: {path}\n")
            if hasattr(win, "add_toast"):
                win.add_toast(Adw.Toast(title="Папка для бэкапа подставлена", timeout=5))
        else:
            msg = (
                "Точка монтирования Google Drive не найдена в GVFS. "
                "Откройте в «Файлах» раздел Google Drive (подождите список файлов), "
                "затем снова нажмите «Определить»."
            )
            self._log(f"⚠  {msg}\n")
            if hasattr(win, "add_toast"):
                win.add_toast(Adw.Toast(title=msg, timeout=8))
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
        from tabs.flatpak import _build_icon_index
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
