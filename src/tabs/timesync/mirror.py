from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from core import backend, config
from core.mirror import (
    OPTIONAL_ITEMS,
    detect_mirror_type,
    get_dest_filesystem, get_root_device, get_root_filesystem, get_root_partition_disk,
    is_uefi,
    mirror_btrfs_send, mirror_btrfs_stream, mirror_ext4_rsync, mirror_ext4_tar,
    restore_to_disk, save_partition_table,
)
from ui.widgets import make_button, make_icon, make_scrolled_page


_mirror_warn_css = Gtk.CssProvider()
_mirror_warn_css.load_from_data(b"""
    banner.ab-mirror-floating-warning {
        border-radius: 14px;
        background-color: alpha(@error_color, 0.80);
        color: @window_fg_color;
        margin: 10px 16px 6px 16px;
        padding: 2px 8px;
    }
    banner.ab-mirror-floating-warning label {
        font-weight: 600;
    }
""")
_mirror_warn_css_registered = False


def ensure_mirror_warning_styles() -> None:
    global _mirror_warn_css_registered
    if _mirror_warn_css_registered:
        return
    display = Gdk.Display.get_default()
    if not display:
        return
    Gtk.StyleContext.add_provider_for_display(
        display,
        _mirror_warn_css,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _mirror_warn_css_registered = True


def _load_optional() -> list[str]:
    raw = config.state_get("mirror_optional_includes", None)
    if raw is None:
        return [item["key"] for item in OPTIONAL_ITEMS if item["default"]]
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_optional(keys: list[str]):
    config.state_set("mirror_optional_includes", json.dumps(keys))


def _translate_btrfs_log_line(line: str) -> str:
    # Переводим наиболее частые англоязычные строки из btrfs-progs.
    raw = line.rstrip("\n")

    m_create = re.match(r"^Create readonly snapshot of '(.+)' in '(.+)'$", raw)
    if m_create:
        src, dst = m_create.group(1), m_create.group(2)
        return f"Создан snapshot (только чтение): '{src}' -> '{dst}'\n"

    m_delete = re.match(r"^Delete subvolume\s+(\d+)\s+\(([^)]+)\):\s+'(.+)'$", raw)
    if m_delete:
        subvol_id, mode, path = m_delete.group(1), m_delete.group(2), m_delete.group(3)
        mode_ru = "без commit" if mode == "no-commit" else mode
        return f"Удаление субволюма {subvol_id} ({mode_ru}): '{path}'\n"

    return line


def _fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(n)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


class MirrorPage(Gtk.Box):

    def __init__(self, log_fn, start_progress_fn=None, stop_progress_fn=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._start_progress = start_progress_fn or (lambda msg, **kw: None)
        self._stop_progress = stop_progress_fn or (lambda ok=True: None)
        self._busy = False

        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_valign(Gtk.Align.CENTER)
        switcher.add_css_class("ab-borg-viewswitcher")
        switcher.set_margin_top(12)
        switcher.set_margin_bottom(12)

        self.append(switcher)
        self.append(self._stack)

        fs = get_root_filesystem()

        ext4_page = self._build_ext4_page(fs)
        self._stack.add_titled_with_icon(ext4_page, "ext4", "EXT4", "drive-harddisk-symbolic")

        btrfs_page = self._build_btrfs_page(fs)
        self._stack.add_titled_with_icon(btrfs_page, "btrfs", "Btrfs", "drive-harddisk-symbolic")

        if fs == "btrfs":
            self._stack.set_visible_child_name("btrfs")

    def _make_dest_row(self, state_key: str) -> tuple[Adw.EntryRow, Gtk.Button]:
        row = Adw.EntryRow()
        row.set_title("Папка назначения")
        row.set_text(config.state_get(state_key, "") or "")
        row.set_show_apply_button(False)
        row.connect("changed", lambda r: config.state_set(state_key, r.get_text()))

        btn = Gtk.Button()
        btn.set_icon_name("folder-open-symbolic")
        btn.add_css_class("flat")
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_tooltip_text("Выбрать папку")
        row.add_suffix(btn)
        return row, btn

    def _pick_folder(self, btn, entry_row):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите папку назначения")
        dialog.select_folder(
            self.get_root(),
            None,
            lambda d, res, _: self._on_folder_selected(d, res, entry_row),
            None,
        )

    def _on_folder_selected(self, dialog, result, entry_row):
        try:
            f = dialog.select_folder_finish(result)
            if f:
                entry_row.set_text(f.get_path())
        except Exception:
            pass

    def _make_content_expander(self) -> Adw.ExpanderRow:
        expander = Adw.ExpanderRow()
        expander.set_title("Содержимое зеркала")
        expander.set_subtitle("Системные файлы, /home, /boot — всегда включены")

        selected = _load_optional()

        for item in OPTIONAL_ITEMS:
            row = Adw.ActionRow()
            row.set_title(item["label"])
            check = Gtk.CheckButton()
            check.set_active(item["key"] in selected)
            check.set_valign(Gtk.Align.CENTER)
            row.add_suffix(check)
            row.set_activatable_widget(check)

            def _on_toggled(btn, key=item["key"]):
                cur = _load_optional()
                if btn.get_active():
                    if key not in cur:
                        cur.append(key)
                else:
                    cur = [k for k in cur if k != key]
                _save_optional(cur)

            check.connect("toggled", _on_toggled)
            expander.add_row(row)

        return expander

    def _build_ext4_page(self, fs: str | None = None) -> Gtk.Widget:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ensure_mirror_warning_styles()

        if fs and fs != "ext4":
            banner = Adw.Banner()
            banner.set_title(f"Недоступно: корневая файловая система — {fs.upper()}, а не EXT4")
            banner.add_css_class("ab-mirror-floating-warning")
            banner.set_revealed(True)
            wrapper.append(banner)

        scroll, body = make_scrolled_page()
        wrapper.append(scroll)

        help_group = Adw.PreferencesGroup()
        help_group.set_title("О режимах")

        row_rsync = Adw.ActionRow()
        row_rsync.set_title("Папка (rsync)")
        row_rsync.set_subtitle(
            "Файлы системы копируются в папку на внешнем носителе. "
            "При повторном запуске обновляются только изменившиеся файлы — "
            "это занимает значительно меньше времени. "
            "Работает с любым носителем, отформатированным в EXT4 или другую ФС."
        )
        row_rsync.set_activatable(False)
        icon_rsync = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon_rsync.set_valign(Gtk.Align.CENTER)
        row_rsync.add_prefix(icon_rsync)
        help_group.add(row_rsync)

        row_tar = Adw.ActionRow()
        row_tar.set_title("Архив .tar.gz")
        row_tar.set_subtitle(
            "Вся система упаковывается в один файл-архив. "
            "Удобно для хранения, переноса и долгосрочного резервирования. "
            "Каждый раз архивируется полностью — время выполнения одинаково при любом запуске."
        )
        row_tar.set_activatable(False)
        icon_tar = Gtk.Image.new_from_icon_name("package-x-generic-symbolic")
        icon_tar.set_valign(Gtk.Align.CENTER)
        row_tar.add_prefix(icon_tar)
        help_group.add(row_tar)

        body.append(help_group)

        dest_group = Adw.PreferencesGroup()
        dest_group.set_title("Назначение")
        dest_row, dest_btn = self._make_dest_row("mirror_ext4_dest")
        dest_btn.connect("clicked", self._pick_folder, dest_row)

        fmt_model = Gtk.StringList.new(["Папка (rsync)", "Архив .tar.gz"])
        fmt_row = Adw.ComboRow()
        fmt_row.set_title("Тип зеркала")
        fmt_row.set_model(fmt_model)
        saved_fmt = config.state_get("mirror_ext4_format", "rsync")
        fmt_row.set_selected(0 if saved_fmt == "rsync" else 1)
        fmt_row.connect("notify::selected", lambda r, _: config.state_set(
            "mirror_ext4_format", "rsync" if r.get_selected() == 0 else "tar"
        ))

        dest_group.add(dest_row)
        dest_group.add(fmt_row)
        body.append(dest_group)
        self._ext4_restore_source = ""

        def _update_ext4_restore_state():
            info = detect_mirror_type(dest_row.get_text().strip())
            if info and info.get("type") in ("rsync", "tar"):
                self._ext4_restore_source = dest_row.get_text().strip()
                self._ext4_restore_btn.set_visible(True)
            else:
                self._ext4_restore_source = ""
                self._ext4_restore_btn.set_visible(False)

        dest_row.connect("changed", lambda _r: _update_ext4_restore_state())

        body.append(self._make_content_expander())

        params_group = Adw.PreferencesGroup()
        params_group.set_title("Дополнительно")

        sw_pt = Adw.SwitchRow()
        sw_pt.set_title("Сохранить таблицу разделов")
        sw_pt.set_subtitle("Схема разделов диска — нужна для восстановления")
        sw_pt.set_active(config.state_get("mirror_ext4_save_pt", True))
        sw_pt.connect("notify::active", lambda r, _: config.state_set("mirror_ext4_save_pt", r.get_active()))
        params_group.add(sw_pt)

        if is_uefi():
            sw_efi = Adw.SwitchRow()
            sw_efi.set_title("Сохранить загрузочный раздел EFI")
            sw_efi.set_subtitle("Содержимое /boot/efi — нужно для восстановления на UEFI-системах")
            sw_efi.set_active(config.state_get("mirror_ext4_save_efi", True))
            sw_efi.connect("notify::active", lambda r, _: config.state_set("mirror_ext4_save_efi", r.get_active()))
            params_group.add(sw_efi)

        body.append(params_group)

        btns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns_row.set_halign(Gtk.Align.CENTER)
        btns_row.set_margin_top(16)
        btns_row.set_margin_bottom(16)

        btn_create = Gtk.Button(label="Создать зеркало EXT4")
        btn_create.add_css_class("suggested-action")
        btn_create.add_css_class("pill")
        btn_create.set_size_request(240, 48)
        btn_create.connect("clicked", lambda _: self._on_create_ext4(dest_row, fmt_row, sw_pt))
        btns_row.append(btn_create)

        self._ext4_restore_btn = Gtk.Button(label="Восстановить")
        self._ext4_restore_btn.add_css_class("success")
        self._ext4_restore_btn.add_css_class("pill")
        self._ext4_restore_btn.set_size_request(190, 48)
        self._ext4_restore_btn.set_visible(False)
        self._ext4_restore_btn.connect("clicked", self._on_ext4_restore_clicked)
        btns_row.append(self._ext4_restore_btn)
        body.append(btns_row)

        _update_ext4_restore_state()

        if fs and fs != "ext4":
            wrapper.set_sensitive(False)
        return wrapper

    def _build_btrfs_page(self, fs: str | None = None) -> Gtk.Widget:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ensure_mirror_warning_styles()

        if fs and fs != "btrfs":
            banner = Adw.Banner()
            banner.set_title(f"Недоступно: корневая файловая система — {fs.upper()}, а не Btrfs")
            banner.add_css_class("ab-mirror-floating-warning")
            banner.set_revealed(True)
            wrapper.append(banner)

        scroll, body = make_scrolled_page()
        wrapper.append(scroll)

        help_group = Adw.PreferencesGroup()
        help_group.set_title("О режимах")

        row_live = Adw.ActionRow()
        row_live.set_title("Живое зеркало")
        row_live.set_subtitle(
            "Точная копия системы на внешнем диске. После первого запуска каждое следующее "
            "обновление занимает считанные минуты — копируются только файлы, которые изменились. "
            "С такого диска можно загрузиться и сразу работать, если основной диск выйдет из строя. "
            "Требует внешний диск, отформатированный в Btrfs (можно сделать прямо в этой утилите)."
        )
        row_live.set_activatable(False)
        icon_live = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        icon_live.set_valign(Gtk.Align.CENTER)
        row_live.add_prefix(icon_live)
        help_group.add(row_live)

        row_arc = Adw.ActionRow()
        row_arc.set_title("Файловый архив")
        row_arc.set_subtitle(
            "Система упаковывается в один или несколько файлов-образов. Их можно положить "
            "на обычную флешку, жёсткий диск, сетевую папку или даже облако. "
            "Подходит для долгосрочного хранения и переноса системы на другой компьютер. "
            "Каждый раз копируется всё — обновление занимает столько же времени, что и первый запуск."
        )
        row_arc.set_activatable(False)
        icon_arc = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon_arc.set_valign(Gtk.Align.CENTER)
        row_arc.add_prefix(icon_arc)
        help_group.add(row_arc)

        body.append(help_group)

        dest_group = Adw.PreferencesGroup()
        dest_group.set_title("Назначение")
        dest_row, dest_btn = self._make_dest_row("mirror_btrfs_dest")
        self._btrfs_dest_info_row = Adw.ActionRow()
        self._btrfs_dest_info_row.set_title("Зеркало системы")
        self._btrfs_dest_info_row.set_subtitle("Выберите папку")
        self._btrfs_dest_info_icon = make_icon("dialog-information-symbolic")
        self._btrfs_dest_info_icon.set_valign(Gtk.Align.CENTER)
        self._btrfs_dest_info_row.add_suffix(self._btrfs_dest_info_icon)

        fmt_model = Gtk.StringList.new([
            "Живое зеркало",
            "Файловый архив",
        ])
        fmt_row = Adw.ComboRow()
        fmt_row.set_title("Тип зеркала")
        fmt_row.set_model(fmt_model)
        saved_fmt = config.state_get("mirror_btrfs_format", "recv")
        fmt_row.set_selected(0 if saved_fmt == "recv" else 1)

        def _check_dest_fs(path: str):
            if fmt_row.get_selected() != 0:
                return
            fs = get_dest_filesystem(path)
            if fs and fs != "btrfs":
                dlg = Adw.AlertDialog(
                    heading="Невозможно создать Живое зеркало",
                    body=f"Носитель отформатирован в {fs.upper()}, а Живое зеркало работает только с Btrfs.\n\nВыберите другой диск или переключитесь на Файловый архив — он работает с любым носителем.",
                )
                dlg.add_response("archive", "Переключить на Файловый архив")
                dlg.add_response("ok", "Понятно")
                dlg.set_default_response("archive")
                def _on_dlg_response(d, resp):
                    if resp == "archive":
                        fmt_row.set_selected(1)
                dlg.connect("response", _on_dlg_response)
                dlg.present(self.get_root())

        def _on_folder_selected_btrfs(dialog, result, entry_row):
            try:
                f = dialog.select_folder_finish(result)
                if f:
                    entry_row.set_text(f.get_path())
                    _check_dest_fs(f.get_path())
            except Exception:
                pass

        def _pick_btrfs_folder(btn, entry_row):
            dialog = Gtk.FileDialog()
            dialog.set_title("Выберите папку назначения")
            dialog.select_folder(
                self.get_root(), None,
                lambda d, res, _: _on_folder_selected_btrfs(d, res, entry_row),
                None,
            )

        dest_btn.connect("clicked", _pick_btrfs_folder, dest_row)

        def _on_fmt_changed(r, _):
            fmt = "recv" if r.get_selected() == 0 else "stream"
            config.state_set("mirror_btrfs_format", fmt)
            if fmt == "recv":
                dest = dest_row.get_text().strip()
                if dest:
                    _check_dest_fs(dest)

        fmt_row.connect("notify::selected", _on_fmt_changed)
        dest_row.connect("changed", lambda r: self._update_btrfs_dest_info(r.get_text().strip()))

        dest_group.add(dest_row)
        dest_group.add(fmt_row)
        dest_group.add(self._btrfs_dest_info_row)
        body.append(dest_group)
        self._update_btrfs_dest_info(dest_row.get_text().strip())

        self._btrfs_subvol_checks: list[tuple[str, Gtk.CheckButton]] = []
        self._sv_rows: list[Adw.ActionRow] = []
        self._sv_group = Adw.PreferencesGroup()
        self._sv_group.set_title("Субволюмы")
        self._sv_group.set_description(
            "Снимки TimeSync (каталог .snapshots/altbooster) не показываются: они уже внутри выбранного @home."
        )
        body.append(self._sv_group)

        self._sv_loading_row = Adw.ActionRow()
        self._sv_loading_row.set_title("Загрузка субволюмов…")
        spinner = Gtk.Spinner()
        spinner.set_spinning(True)
        spinner.set_valign(Gtk.Align.CENTER)
        self._sv_loading_row.add_suffix(spinner)
        self._sv_loading_row.set_visible(False)
        self._sv_group.add(self._sv_loading_row)

        self._sv_snap_group = Adw.PreferencesGroup()
        self._sv_snap_group.set_title("Сохранённые копии")
        self._sv_snap_group.set_description("Снимки созданные ALT Booster — не копируются")
        self._sv_snap_group.set_visible(False)
        self._sv_snap_rows: list[Adw.ActionRow] = []

        GLib.idle_add(self._load_btrfs_subvols)

        params_group = Adw.PreferencesGroup()
        params_group.set_title("Дополнительно")

        sw_pt = Adw.SwitchRow()
        sw_pt.set_title("Сохранить таблицу разделов")
        sw_pt.set_subtitle("Схема разделов диска — нужна для восстановления")
        sw_pt.set_active(config.state_get("mirror_btrfs_save_pt", True))
        sw_pt.connect("notify::active", lambda r, _: config.state_set("mirror_btrfs_save_pt", r.get_active()))
        params_group.add(sw_pt)

        if is_uefi():
            sw_efi = Adw.SwitchRow()
            sw_efi.set_title("Сохранить загрузочный раздел EFI")
            sw_efi.set_subtitle("Содержимое /boot/efi — нужно для восстановления на UEFI-системах")
            sw_efi.set_active(config.state_get("mirror_btrfs_save_efi", True))
            sw_efi.connect("notify::active", lambda r, _: config.state_set("mirror_btrfs_save_efi", r.get_active()))
            params_group.add(sw_efi)

        body.append(params_group)

        btns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns_row.set_halign(Gtk.Align.CENTER)
        btns_row.set_margin_top(16)
        btns_row.set_margin_bottom(16)

        btn_create = Gtk.Button(label="Создать зеркало Btrfs")
        btn_create.add_css_class("suggested-action")
        btn_create.add_css_class("pill")
        btn_create.set_size_request(240, 48)
        btn_create.connect("clicked", lambda _: self._on_create_btrfs(dest_row, sw_pt, None, fmt_row))
        btns_row.append(btn_create)

        self._btrfs_restore_btn = Gtk.Button(label="Восстановить")
        self._btrfs_restore_btn.add_css_class("success")
        self._btrfs_restore_btn.add_css_class("pill")
        self._btrfs_restore_btn.set_size_request(190, 48)
        self._btrfs_restore_btn.set_visible(False)
        self._btrfs_restore_btn.connect("clicked", self._on_inline_restore_clicked)
        btns_row.append(self._btrfs_restore_btn)

        # Re-run state sync now that restore button exists.
        self._update_btrfs_dest_info(dest_row.get_text().strip())

        body.append(btns_row)

        body.append(self._sv_snap_group)

        if fs and fs != "btrfs":
            wrapper.set_sensitive(False)
        return wrapper

    def _load_btrfs_subvols(self):
        self._sv_loading_row.set_visible(True)
        for row in self._sv_rows:
            self._sv_group.remove(row)
        self._sv_rows.clear()
        self._btrfs_subvol_checks.clear()
        for row in self._sv_snap_rows:
            self._sv_snap_group.remove(row)
        self._sv_snap_rows.clear()

        _lines: list[str] = []

        def _on_line(line: str):
            _lines.append(line)

        def _on_done(ok: bool):
            import re

            def _populate():
                self._sv_loading_row.set_visible(False)
                saved_sv = json.loads(config.state_get("mirror_btrfs_subvols", "[]") or "[]")
                found = []
                for line in _lines:
                    m = re.match(r"ID\s+(\d+).*path\s+(.+)", line)
                    if m:
                        found.append(m.group(2).strip())

                _check_if_empty = {"var/lib/machines", "var/lib/portables"}
                def _has_content(subvol_path: str) -> bool:
                    try:
                        return bool(os.listdir(f"/{subvol_path}"))
                    except Exception:
                        return False

                def _has_snap_part(path: str) -> bool:
                    return any(part.startswith(".snap_") for part in path.split("/"))

                def _is_root_snap(path: str) -> bool:
                    return path.startswith(".snap_") and path.endswith("_prev")

                def _is_timesync_snapshot_subvol(path: str) -> bool:
                    # Снэпшоты вкладки «Снэпшоты»: вложенные субтомы под @home — дублируют зеркало
                    p = path.replace("\\", "/")
                    return ".snapshots/altbooster" in p

                regular = [
                    p
                    for p in found
                    if not _has_snap_part(p)
                    and not _is_timesync_snapshot_subvol(p)
                    and (p not in _check_if_empty or _has_content(p))
                ]
                snaps = [p for p in found if _is_root_snap(p)]

                if regular:
                    rows_by_subvol: dict[str, Adw.ActionRow] = {}
                    for path in regular:
                        row = Adw.ActionRow()
                        row.set_title(path)
                        row.set_subtitle("Подсчёт размера…")
                        check = Gtk.CheckButton()
                        check.set_active(not saved_sv or path in saved_sv)
                        check.set_valign(Gtk.Align.CENTER)
                        row.add_suffix(check)
                        row.set_activatable_widget(check)
                        self._sv_group.add(row)
                        self._sv_rows.append(row)
                        self._btrfs_subvol_checks.append((path, check))
                        rows_by_subvol[path] = row

                        def _on_sv_toggled(*_):
                            sel = [p for p, c in self._btrfs_subvol_checks if c.get_active()]
                            config.state_set("mirror_btrfs_subvols", json.dumps(sel))

                        check.connect("toggled", _on_sv_toggled)

                    threading.Thread(
                        target=self._fetch_subvol_sizes,
                        args=(rows_by_subvol,),
                        daemon=True,
                    ).start()
                else:
                    no_row = Adw.ActionRow()
                    no_row.set_title("Субволюмы не найдены")
                    self._sv_group.add(no_row)
                    self._sv_rows.append(no_row)

                for row in self._sv_snap_rows:
                    self._sv_snap_group.remove(row)
                self._sv_snap_rows.clear()

                if snaps:
                    self._sv_snap_group.set_visible(True)
                    for path in snaps:
                        row = Adw.ActionRow()
                        row.set_title(path.removeprefix(".snap_").removesuffix("_prev"))
                        row.set_subtitle(f"Btrfs-раздел: /{path}")

                        btn_del = Gtk.Button()
                        btn_del.set_icon_name("user-trash-symbolic")
                        btn_del.add_css_class("flat")
                        btn_del.add_css_class("destructive-action")
                        btn_del.set_valign(Gtk.Align.CENTER)
                        btn_del.connect("clicked", lambda _, p=path, r=row: self._confirm_delete_snap(p, r))
                        row.add_suffix(btn_del)

                        icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
                        icon.set_valign(Gtk.Align.CENTER)
                        icon.set_pixel_size(16)
                        icon.add_css_class("success")
                        row.add_suffix(icon)

                        self._sv_snap_group.add(row)
                        self._sv_snap_rows.append(row)
                else:
                    self._sv_snap_group.set_visible(False)

            GLib.idle_add(_populate)

        backend.run_privileged(["btrfs", "subvolume", "list", "/"], _on_line, _on_done)

    def _update_btrfs_dest_info(self, path: str):
        if not hasattr(self, "_btrfs_dest_info_row"):
            return
        has_restore_btn = hasattr(self, "_btrfs_restore_btn")
        if not path:
            self._btrfs_dest_info_icon.set_from_icon_name("dialog-information-symbolic")
            self._btrfs_dest_info_icon.remove_css_class("success")
            if has_restore_btn:
                self._btrfs_restore_btn.set_visible(False)
            self._btrfs_restore_source = ""
            self._btrfs_dest_info_row.set_subtitle("Выберите папку")
            return
        info = detect_mirror_type(path)
        if not info:
            self._btrfs_dest_info_icon.set_from_icon_name("dialog-warning-symbolic")
            self._btrfs_dest_info_icon.remove_css_class("success")
            if has_restore_btn:
                self._btrfs_restore_btn.set_visible(False)
            self._btrfs_restore_source = ""
            self._btrfs_dest_info_row.set_subtitle("Зеркало в папке не найдено")
            return
        t = info.get("type", "")
        subvols = info.get("subvols", []) or []

        p = Path(path)
        date_str = "дата неизвестна"
        size_str = "размер неизвестен"
        try:
            if t == "btrfs":
                files = list(p.glob("*.btrfs"))
                if files:
                    total_bytes = sum(f.stat().st_size for f in files if f.exists())
                    newest_ts = max(f.stat().st_mtime for f in files if f.exists())
                    date_str = datetime.fromtimestamp(newest_ts).strftime("%d.%m.%Y %H:%M")
                    size_str = _fmt_bytes(total_bytes)
            elif t == "btrfs_recv":
                snaps = [d for d in p.glob(".snap_*") if d.is_dir()]
                if snaps:
                    newest_ts = max(d.stat().st_mtime for d in snaps if d.exists())
                    date_str = datetime.fromtimestamp(newest_ts).strftime("%d.%m.%Y %H:%M")
                    size_str = "инкрементальный набор"
        except Exception:
            pass

        self._btrfs_dest_info_icon.set_from_icon_name("object-select-symbolic")
        self._btrfs_dest_info_icon.add_css_class("success")
        self._btrfs_restore_source = path
        if has_restore_btn:
            self._btrfs_restore_btn.set_visible(t in ("btrfs", "btrfs_recv"))
        if t == "btrfs":
            self._btrfs_dest_info_row.set_subtitle(
                f"Доступно для восстановления со следующими субволюмами: {', '.join(subvols)} - {size_str} - дата создания: {date_str}"
            )
        elif t == "btrfs_recv":
            self._btrfs_dest_info_row.set_subtitle(
                f"Доступно для восстановления со следующими субволюмами: {', '.join(subvols)} - {size_str} - дата создания: {date_str}"
            )
        else:
            self._btrfs_dest_info_icon.set_from_icon_name("dialog-information-symbolic")
            self._btrfs_dest_info_icon.remove_css_class("success")
            if has_restore_btn:
                self._btrfs_restore_btn.set_visible(False)
            self._btrfs_restore_source = ""
            self._btrfs_dest_info_row.set_subtitle("Папка содержит зеркало другого типа")

    def _on_inline_restore_clicked(self, _btn):
        src = (getattr(self, "_btrfs_restore_source", "") or "").strip()
        if not src:
            self._show_error("Не удалось определить папку с зеркалом")
            return
        self._start_restore_flow(src)

    def _on_ext4_restore_clicked(self, _btn):
        src = (getattr(self, "_ext4_restore_source", "") or "").strip()
        if not src:
            self._show_error("Не удалось определить папку с зеркалом")
            return
        self._start_restore_flow(src)

    def _start_restore_flow(self, src: str):
        self._open_restore_disk_picker(src)

    def _open_restore_disk_picker(self, src: str):
        disks = backend.list_available_disks(exclude_root=True)
        if not disks:
            self._show_no_disks_help_dialog(src)
            return
        self._inline_restore_disks = disks

        labels = []
        for d in disks:
            model = (d.get("model") or "").strip()
            device = d.get("device", "")
            size = d.get("size", "?")
            labels.append(f"{device} · {size}" + (f" · {model}" if model else ""))

        model = Gtk.StringList.new(labels)
        dd = Gtk.DropDown(model=model)
        dd.set_hexpand(True)

        warn = Gtk.Label(
            label=(
                "Выберите накопитель для восстановления.\n"
                "Внимание: все данные на выбранном накопителе будут удалены."
            )
        )
        warn.set_wrap(True)
        warn.set_halign(Gtk.Align.START)
        warn.add_css_class("caption")
        warn.add_css_class("dim-label")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(warn)
        box.append(dd)

        dlg = Adw.AlertDialog(
            heading="Восстановить из зеркала?",
            body="Выберите целевой накопитель.",
        )
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("prepare", "Подготовить диск")
        dlg.add_response("next", "Продолжить")
        dlg.set_default_response("next")
        dlg.set_close_response("cancel")
        dlg.connect("response", self._on_inline_restore_pick_disk_done, src, dd)
        dlg.present(self.get_root())

    def _show_no_disks_help_dialog(self, src: str):
        dlg = Adw.AlertDialog(
            heading="Не найдено доступных накопителей",
            body=(
                "Чтобы продолжить восстановление, подключите второй диск.\n\n"
                "Как подготовить диск:\n"
                "1) Подключите новый SSD/HDD/USB.\n"
                "2) Закройте программы, которые могут использовать диск.\n"
                "3) Нажмите «Повторить поиск».\n\n"
                "Форматировать вручную не нужно — при восстановлении диск "
                "будет подготовлен автоматически."
            ),
        )
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("retry", "Повторить поиск")
        dlg.set_default_response("retry")
        dlg.set_close_response("cancel")

        def _on_resp(_d, resp: str):
            if resp == "retry":
                self._open_restore_disk_picker(src)

        dlg.connect("response", _on_resp)
        dlg.present(self.get_root())

    def _on_inline_restore_pick_disk_done(self, _dialog, response: str, src: str, dd: Gtk.DropDown):
        if response == "prepare":
            idx = dd.get_selected()
            disks = getattr(self, "_inline_restore_disks", [])
            if idx >= len(disks):
                self._show_error("Не удалось определить выбранный диск")
                return
            device = disks[idx].get("device", "")
            self._confirm_prepare_disk(src, device)
            return
        if response != "next":
            return
        idx = dd.get_selected()
        disks = getattr(self, "_inline_restore_disks", [])
        if idx >= len(disks):
            self._show_error("Не удалось определить выбранный диск")
            return
        device = disks[idx].get("device", "")
        size = disks[idx].get("size", "?")

        confirm = Adw.AlertDialog(
            heading="Внимание: диск будет перезаписан",
            body=(
                f"Источник: {src}\n"
                f"Диск: {device} ({size})\n\n"
                "Все данные на выбранном диске будут уничтожены.\n"
                "Продолжить восстановление?"
            ),
        )
        confirm.add_response("cancel", "Отмена")
        confirm.add_response("restore", "Восстановить")
        confirm.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.set_default_response("cancel")
        confirm.connect("response", self._on_inline_restore_confirmed, src, device)
        confirm.present(self.get_root())

    def _on_inline_restore_confirmed(self, _dialog, response: str, src: str, target_device: str):
        if response != "restore":
            return
        if not target_device.startswith("/dev/"):
            self._show_error("Для восстановления нужно выбрать реальный диск (/dev/...), а не папку")
            return
        self._busy = True
        self._start_progress("Восстановление из зеркала...")
        self._log(f"Восстановление из {src} на {target_device}...\n")

        def _done(ok):
            self._busy = False
            self._stop_progress(ok)
            self._log(f"Восстановление {'завершено' if ok else 'завершено с ошибкой'}.\n")

        restore_to_disk(src, target_device, self._log, _done)

    def _confirm_prepare_disk(self, src: str, device: str):
        if not device.startswith("/dev/"):
            self._show_error("Можно подготовить только реальный диск (/dev/...)")
            return
        d1 = Adw.AlertDialog(
            heading="Подготовить диск?",
            body=(
                f"Будет подготовлен диск {device}.\n"
                "Все разделы и данные на нём будут удалены."
            ),
        )
        d1.add_response("cancel", "Отмена")
        d1.add_response("continue", "Продолжить")
        d1.set_response_appearance("continue", Adw.ResponseAppearance.DESTRUCTIVE)
        d1.set_default_response("cancel")
        d1.set_close_response("cancel")

        def _on_first(_d, resp):
            if resp != "continue":
                return
            d2 = Adw.AlertDialog(
                heading="Последнее подтверждение",
                body=(
                    f"Подтвердите подготовку диска {device}.\n\n"
                    "Операция необратима."
                ),
            )
            d2.add_response("cancel", "Отмена")
            d2.add_response("prepare", "Подготовить")
            d2.set_response_appearance("prepare", Adw.ResponseAppearance.DESTRUCTIVE)
            d2.set_default_response("cancel")
            d2.set_close_response("cancel")
            d2.connect("response", lambda _d2, r2: self._run_prepare_disk(src, device) if r2 == "prepare" else None)
            d2.present(self.get_root())

        d1.connect("response", _on_first)
        d1.present(self.get_root())

    def _run_prepare_disk(self, src: str, device: str):
        info = detect_mirror_type(src) or {}
        mirror_type = info.get("type", "")
        use_btrfs = mirror_type in ("btrfs", "btrfs_recv")
        uefi = backend.is_uefi()
        sep = "p" if device[-1].isdigit() else ""
        p1 = f"{device}{sep}1"
        p2 = f"{device}{sep}2"
        fs_cmd = "mkfs.btrfs -f" if use_btrfs else "mkfs.ext4 -F"

        part_script = (
            "label: gpt\n"
            ",512M,U,*\n"
            ",,L\n"
        ) if uefi else (
            "label: gpt\n"
            ",,L,*\n"
        )

        script = [
            "set -e",
            f'TARGET="{device}"',
            'echo "▶  Размонтирование разделов..."',
            'for p in $(lsblk -nr -o PATH "$TARGET" | tail -n +2); do umount "$p" 2>/dev/null || true; done',
            'echo "▶  Очистка сигнатур..."',
            'wipefs -a "$TARGET"',
            'echo "▶  Создание новой таблицы разделов GPT..."',
            f"cat <<'EOF' | sfdisk \"$TARGET\"",
            part_script.rstrip("\n"),
            "EOF",
            "partprobe \"$TARGET\" || true",
            "sleep 1",
        ]
        if uefi:
            script += [
                'echo "▶  Форматирование EFI..."',
                f"mkfs.fat -F32 {p1}",
                'echo "▶  Форматирование системного раздела..."',
                f"{fs_cmd} {p2}",
            ]
        else:
            script += [
                'echo "▶  Форматирование системного раздела..."',
                f"{fs_cmd} {p1}",
            ]
        script += ['echo "✔  Диск подготовлен."']
        cmd = ["bash", "-c", "\n".join(script)]

        self._start_progress("Подготовка диска...")
        self._log(f"Подготовка диска {device}...\n")

        def _done(ok):
            self._stop_progress(ok)
            if ok:
                self._log("✔  Подготовка диска завершена. Можно запускать восстановление.\n")
                self._open_restore_disk_picker(src)
            else:
                self._log("✘  Подготовка диска завершилась с ошибкой.\n")

        backend.run_privileged(cmd, self._log, _done)

    def _fetch_subvol_sizes(self, rows_by_subvol: dict):
        import subprocess
        try:
            out = subprocess.check_output(
                ["findmnt", "-n", "-r", "-o", "TARGET,OPTIONS", "--type", "btrfs"],
                text=True, stderr=subprocess.DEVNULL,
            )
        except Exception:
            for row in rows_by_subvol.values():
                GLib.idle_add(row.set_subtitle, "")
            return

        mount_map: dict[str, str] = {}
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            target, opts = parts
            for opt in opts.split(","):
                opt = opt.strip()
                if opt.startswith("subvol="):
                    sv = opt.removeprefix("subvol=/").removeprefix("subvol=")
                    mount_map[sv] = target

        for subvol, row in rows_by_subvol.items():
            mountpoint = mount_map.get(subvol)
            if not mountpoint:
                GLib.idle_add(row.set_subtitle, "")
                continue
            try:
                result = subprocess.run(
                    ["du", "-sh", "-x", mountpoint],
                    capture_output=True, text=True, timeout=120,
                )
                size = result.stdout.split()[0] if result.stdout.strip() else "?"
                subtitle = f"{size}  ·  {mountpoint}"
            except Exception:
                subtitle = mountpoint
            GLib.idle_add(row.set_subtitle, subtitle)

    def _confirm_delete_snap(self, snap_path: str, row: Adw.ActionRow):
        name = snap_path.removeprefix(".snap_").removesuffix("_prev")
        dlg = Adw.AlertDialog(
            heading=f"Удалить снимок «{name}»?",
            body="Снимок будет удалён с Btrfs-раздела. Следующее зеркалирование будет полным — займёт больше времени.",
        )
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("delete", "Удалить")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, resp: self._delete_snap(resp, snap_path, row))
        root = self.get_root()
        if root:
            dlg.present(root)
        else:
            dlg.present()

    def _delete_snap(self, response: str, snap_path: str, row: Adw.ActionRow):
        if response != "delete":
            return
        self._log(f"\n▶  Удаление снимка зеркала: {snap_path}\n")
        toplevel = "/tmp/.btrfs_mirror_toplevel"
        script = (
            "set -e\n"
            f'BTRFS_DEV=$(findmnt -n -o SOURCE / | sed \'s/\\[.*\\]//\')\n'
            f"mkdir -p {shlex.quote(toplevel)}\n"
            f"mount -t btrfs -o subvolid=5 \"$BTRFS_DEV\" {shlex.quote(toplevel)}\n"
            f"btrfs subvolume delete {shlex.quote(f'{toplevel}/{snap_path}')}\n"
            f"umount {shlex.quote(toplevel)}\n"
        )

        def _on_done(ok):
            if ok:
                self._log("✔  Снимок удалён\n")
                GLib.idle_add(self._load_btrfs_subvols)
            else:
                GLib.idle_add(self._show_error, f"Не удалось удалить снимок {snap_path}")

        backend.run_privileged(["bash", "-c", script], self._log, _on_done)

    def _build_restore_page(self) -> Gtk.Widget:
        scroll, body = make_scrolled_page()

        src_group = Adw.PreferencesGroup()
        src_group.set_title("Источник")
        self._restore_src_row = Adw.EntryRow()
        self._restore_src_row.set_title("Папка с зеркалом")
        self._restore_src_row.set_text(config.state_get("mirror_restore_src", "") or "")
        self._restore_src_row.connect("changed", self._on_restore_src_changed)

        src_btn = Gtk.Button()
        src_btn.set_icon_name("folder-open-symbolic")
        src_btn.add_css_class("flat")
        src_btn.set_valign(Gtk.Align.CENTER)
        src_btn.connect("clicked", self._on_pick_restore_src)
        self._restore_src_row.add_suffix(src_btn)
        src_group.add(self._restore_src_row)

        self._restore_info_row = Adw.ActionRow()
        self._restore_info_row.set_title("Тип зеркала")
        self._restore_info_row.set_subtitle("Выберите папку")
        self._restore_info_row.add_prefix(make_icon("dialog-information-symbolic"))
        self._restore_info_row.set_visible(False)
        src_group.add(self._restore_info_row)
        body.append(src_group)

        dest_group = Adw.PreferencesGroup()
        dest_group.set_title("Назначение")

        self._restore_dest_row = Adw.EntryRow()
        self._restore_dest_row.set_title("Целевая папка")
        self._restore_dest_row.set_text(config.state_get("mirror_restore_dest", "") or "")
        self._restore_dest_row.connect("changed", lambda r: config.state_set("mirror_restore_dest", r.get_text()))

        dest_btn = Gtk.Button()
        dest_btn.set_icon_name("folder-open-symbolic")
        dest_btn.add_css_class("flat")
        dest_btn.set_valign(Gtk.Align.CENTER)
        dest_btn.set_tooltip_text("Выбрать папку")
        dest_btn.connect("clicked", self._on_pick_restore_dest)
        self._restore_dest_row.add_suffix(dest_btn)
        dest_group.add(self._restore_dest_row)
        body.append(dest_group)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(4)

        btn_restore = make_button("Восстановить", style="destructive-action")
        btn_restore.connect("clicked", self._on_restore_clicked)

        btn_box.append(btn_restore)
        body.append(btn_box)

        src_saved = config.state_get("mirror_restore_src", "") or ""
        if src_saved:
            GLib.idle_add(self._update_restore_info, src_saved)

        return scroll

    def _on_pick_restore_dest(self, _btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите папку назначения")
        dialog.select_folder(
            self.get_root(), None,
            lambda d, res, _: self._on_restore_dest_picked(d, res), None,
        )

    def _on_restore_dest_picked(self, dialog, result):
        try:
            f = dialog.select_folder_finish(result)
            if f:
                self._restore_dest_row.set_text(f.get_path())
        except Exception:
            pass

    def _on_restore_src_changed(self, row):
        path = row.get_text().strip()
        config.state_set("mirror_restore_src", path)
        self._update_restore_info(path)

    def _update_restore_info(self, path: str):
        if not path:
            self._restore_info_row.set_visible(False)
            return
        info = detect_mirror_type(path)
        self._restore_info_row.set_visible(True)
        if not info:
            self._restore_info_row.set_subtitle("Зеркало не распознано")
            return
        t = info.get("type", "")
        fs = info.get("fs", "")
        subvols = info.get("subvols", [])
        if t == "rsync":
            self._restore_info_row.set_subtitle(f"Тип: rsync (EXT4)  ·  Раздел: {fs}")
        elif t == "tar":
            self._restore_info_row.set_subtitle(f"Тип: tar.gz (EXT4)")
        elif t == "btrfs":
            self._restore_info_row.set_subtitle(f"Тип: btrfs stream  ·  Субволюмы: {', '.join(subvols)}")
        elif t == "btrfs_recv":
            self._restore_info_row.set_subtitle(f"Тип: btrfs инкрементальный  ·  Субволюмы: {', '.join(subvols)}")
        else:
            self._restore_info_row.set_subtitle("Неизвестный формат")

    def _on_pick_restore_src(self, _btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите папку с зеркалом")
        dialog.select_folder(
            self.get_root(), None,
            lambda d, res, _: self._on_restore_src_picked(d, res), None,
        )

    def _on_restore_src_picked(self, dialog, result):
        try:
            f = dialog.select_folder_finish(result)
            if f:
                self._restore_src_row.set_text(f.get_path())
        except Exception:
            pass

    def _on_restore_disk_changed(self, row, _):
        idx = row.get_selected()
        if self._restore_disks and idx < len(self._restore_disks):
            d = self._restore_disks[idx]
            config.state_set("mirror_restore_target_disk", d["device"])
            self._restore_warn_label.set_text(
                f"⚠ Диск {d['device']} ({d['size']}) будет полностью перезаписан"
            )
            self._restore_warn_label.set_visible(True)

    def _show_error(self, msg: str):
        self._log(msg if msg.endswith("\n") else msg + "\n")
        dlg = Adw.AlertDialog(heading="Ошибка", body=msg.strip())
        dlg.add_response("ok", "ОК")
        dlg.present(self.get_root())

    def _selected_subvolumes(self) -> list[str]:
        return [p for p, c in self._btrfs_subvol_checks if c.get_active()]

    def _get_ext4_dest(self, dest_row: Adw.EntryRow) -> str | None:
        dest = dest_row.get_text().strip()
        if not dest:
            self._show_error("Ошибка: не указана папка назначения")
            return None
        try:
            Path(dest).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._show_error(f"Ошибка создания папки: {e}")
            return None
        return dest

    def _on_create_ext4(self, dest_row, fmt_row, sw_pt):
        try:
            self._on_create_ext4_inner(dest_row, fmt_row, sw_pt)
        except Exception as e:
            import traceback
            self._log(f"Ошибка: {e}\n{traceback.format_exc()}\n")
            self._show_error(f"Ошибка: {e}")

    def _on_create_ext4_inner(self, dest_row, fmt_row, sw_pt):
        if self._busy:
            return
        dest = self._get_ext4_dest(dest_row)
        if not dest:
            return
        fmt = "rsync" if fmt_row.get_selected() == 0 else "tar"
        save_pt = sw_pt.get_active()
        optional = _load_optional()
        self._busy = True
        self._start_progress(f"EXT4 зеркало → {dest}")
        self._log(f"Создание зеркала EXT4 ({fmt}) → {dest}\n")

        def _after_mirror(ok):
            if not ok:
                GLib.idle_add(self._show_error, "Ошибка при создании зеркала")
                GLib.idle_add(self._stop_progress, False)
                self._busy = False
                return

            def _step2():
                if save_pt:
                    dev = get_root_device() or ""
                    disk = get_root_partition_disk(dev)
                    if disk:
                        ok2 = save_partition_table(disk, dest)
                        GLib.idle_add(self._log, f"Таблица разделов: {'OK' if ok2 else 'ошибка'}\n")
                GLib.idle_add(self._log, "Зеркало готово.\n")
                GLib.idle_add(self._stop_progress, True)
                GLib.idle_add(setattr, self, "_busy", False)

            threading.Thread(target=_step2, daemon=True).start()

        if fmt == "rsync":
            mirror_ext4_rsync(dest, optional, self._log, _after_mirror, run_fn=backend.run_privileged)
        else:
            mirror_ext4_tar(dest, optional, self._log, _after_mirror, run_fn=backend.run_privileged)

    def _on_create_btrfs(self, dest_row, sw_pt, _unused, fmt_row):
        try:
            self._on_create_btrfs_inner(dest_row, sw_pt, fmt_row)
        except Exception as e:
            import traceback
            self._log(f"Ошибка: {e}\n{traceback.format_exc()}\n")
            self._show_error(f"Ошибка: {e}")

    def _on_create_btrfs_inner(self, dest_row, sw_pt, fmt_row):
        if self._busy:
            return
        dest = dest_row.get_text().strip()
        if not dest:
            self._show_error("Ошибка: не указана папка назначения")
            return
        try:
            Path(dest).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._show_error(f"Ошибка создания папки: {e}")
            return
        fmt = "recv" if fmt_row.get_selected() == 0 else "stream"
        if fmt == "recv":
            dest_fs = get_dest_filesystem(dest)
            if dest_fs != "btrfs":
                self._show_error(f"Ошибка: для инкрементального режима папка должна быть на Btrfs-разделе (обнаружен FS: {dest_fs or 'unknown'})")
                return
        subvols = self._selected_subvolumes()
        if not subvols:
            self._show_error("Ошибка: не выбрано ни одного субволюма")
            return
        self._busy = True
        save_pt = sw_pt.get_active()
        self._start_progress(f"Btrfs зеркало → {dest}")
        self._log(f"Создание Btrfs-зеркала ({fmt}) → {dest}\n")
        self._log(f"Субволюмы: {', '.join(subvols)}\n")

        _at_subvol_state = {"last": None, "count": 0}
        _progress_state = {
            "active": True,
            "phase": "Подготовка",
            "last_log_ts": time.monotonic(),
            "last_heartbeat_ts": 0.0,
            "pulse_idx": 0,
        }

        def _start_log_heartbeat():
            pulse = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

            def _tick():
                if not _progress_state["active"]:
                    return False
                now = time.monotonic()
                idle_for = now - _progress_state["last_log_ts"]
                since_hb = now - _progress_state["last_heartbeat_ts"]
                if idle_for >= 8.0 and since_hb >= 8.0:
                    symbol = pulse[_progress_state["pulse_idx"] % len(pulse)]
                    _progress_state["pulse_idx"] += 1
                    self._log(
                        f"⏳ [{symbol}] { _progress_state['phase'] }... "
                        f"{int(idle_for)}с без новых строк, процесс продолжается\n"
                    )
                    _progress_state["last_heartbeat_ts"] = now
                return True

            GLib.timeout_add(3000, _tick)

        _start_log_heartbeat()

        def _filtered_log(line):
            _progress_state["last_log_ts"] = time.monotonic()
            if line.startswith("At subvol "):
                _progress_state["phase"] = "Приём потока btrfs"
                name = line.strip()
                if name != _at_subvol_state["last"]:
                    if _at_subvol_state["last"] is not None:
                        self._log(f"  → получено объектов: {_at_subvol_state['count']}\n")
                    _at_subvol_state["last"] = name
                    _at_subvol_state["count"] = 1
                    self._log(f"Приём данных: {line.removeprefix('At subvol ')}")
                else:
                    _at_subvol_state["count"] += 1
            else:
                text = line.strip()
                if text.startswith("Субволюм "):
                    _progress_state["phase"] = "Обработка субволюма"
                elif text.startswith("Объём данных:"):
                    _progress_state["phase"] = "Сохранение потока в файл"
                elif text.startswith("✔  Субволюм"):
                    _progress_state["phase"] = "Переход к следующему субволюму"
                if _at_subvol_state["last"] is not None and _at_subvol_state["count"] > 1:
                    self._log(f"  → получено объектов: {_at_subvol_state['count']}\n")
                _at_subvol_state["last"] = None
                _at_subvol_state["count"] = 0
                self._log(_translate_btrfs_log_line(line))

        def _after_mirror(ok):
            _progress_state["active"] = False
            if not ok:
                GLib.idle_add(self._log, "Ошибка при btrfs send\n")
                GLib.idle_add(self._stop_progress, False)
                self._busy = False
                return

            def _step2():
                if save_pt:
                    dev = get_root_device() or ""
                    disk = get_root_partition_disk(dev)
                    if disk:
                        ok2 = save_partition_table(disk, dest)
                        GLib.idle_add(self._log, f"Таблица разделов: {'OK' if ok2 else 'ошибка'}\n")
                GLib.idle_add(self._log, "Зеркало готово.\n")
                # Сразу подставляем созданное зеркало в блок "Восстановить",
                # чтобы пользователь без перезапуска видел распознанный тип и субволюмы.
                GLib.idle_add(config.state_set, "mirror_restore_src", dest)
                if hasattr(self, "_restore_src_row"):
                    GLib.idle_add(self._restore_src_row.set_text, dest)
                if hasattr(self, "_update_restore_info"):
                    GLib.idle_add(self._update_restore_info, dest)
                if hasattr(self, "_update_btrfs_dest_info"):
                    GLib.idle_add(self._update_btrfs_dest_info, dest)
                GLib.idle_add(self._stop_progress, True)
                GLib.idle_add(setattr, self, "_busy", False)

            threading.Thread(target=_step2, daemon=True).start()

        if fmt == "recv":
            mirror_btrfs_send(subvols, dest, _filtered_log, _after_mirror, run_fn=backend.run_privileged)
        else:
            mirror_btrfs_stream(subvols, dest, _filtered_log, _after_mirror, run_fn=backend.run_privileged)

    def _on_restore_clicked(self, _btn):
        if self._busy:
            return
        src = self._restore_src_row.get_text().strip()
        if not src:
            self._show_error("Ошибка: не указана папка с зеркалом")
            return
        target = self._restore_dest_row.get_text().strip()
        if not target:
            self._show_error("Ошибка: не указана целевая папка")
            return

        dialog = Adw.AlertDialog(
            heading="Восстановить систему?",
            body=f"Папка {target} будет перезаписана содержимым зеркала.\nЭто действие необратимо.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("restore", "Восстановить")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_restore_confirmed, src, target)
        dialog.present(self.get_root())

    def _on_restore_confirmed(self, _dialog, response, src, target):
        if response != "restore":
            return
        self._busy = True
        self._log(f"Восстановление из {src} на {target}...\n")

        def _done(ok):
            self._busy = False
            self._log(f"Восстановление {'завершено' if ok else 'завершено с ошибкой'}.\n")

        restore_to_disk(src, target, self._log, _done)
