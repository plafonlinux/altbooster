from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend, config
from ui.widgets import make_button, make_scrolled_page


def build_terminal_page(log_fn) -> Gtk.Widget:
    scroll, body = make_scrolled_page()

    cmd_group = Adw.PreferencesGroup()
    cmd_group.set_title("Команда")

    entry = Adw.EntryRow()
    entry.set_title("Введите команду")
    cmd_group.add(entry)
    body.append(cmd_group)

    quick_group = Adw.PreferencesGroup()
    quick_group.set_title("Быстрые команды")
    body.append(quick_group)

    repo = config.state_get("borg_repo_path", "") or "[репозиторий]"

    quick_cmds = [
        ("borg list", f"borg list {repo}"),
        ("borg info", f"borg info {repo}"),
        ("borg check", f"borg check --verify-data {repo}"),
        ("btrfs subvolumes", "btrfs subvolume list /"),
        ("rsync dry-run", "rsync -aAXn --info=progress2 / [назначение]/rootfs/"),
        ("lsblk", "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,MODEL"),
    ]

    for label, cmd in quick_cmds:
        row = Adw.ActionRow()
        row.set_title(label)
        row.set_subtitle(cmd)
        copy_btn = Gtk.Button()
        copy_btn.set_icon_name("edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.set_tooltip_text("Скопировать в поле ввода")
        copy_btn.connect("clicked", lambda _, c=cmd: entry.set_text(c))
        row.add_suffix(copy_btn)
        quick_group.add(row)

    log_group = Adw.PreferencesGroup()
    log_group.set_title("Вывод")
    body.append(log_group)

    tv = Gtk.TextView()
    tv.set_editable(False)
    tv.set_cursor_visible(False)
    tv.add_css_class("monospace")
    tv.set_margin_top(8)
    tv.set_margin_bottom(8)
    tv.set_margin_start(8)
    tv.set_margin_end(8)
    buf = tv.get_buffer()

    sw = Gtk.ScrolledWindow()
    sw.set_min_content_height(220)
    sw.set_vexpand(False)
    sw.set_child(tv)
    sw.add_css_class("card")

    log_group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    log_group_box.append(sw)
    body.append(log_group_box)

    def _append_log(text: str):
        end = buf.get_end_iter()
        buf.insert(end, text)
        adj = sw.get_vadjustment()
        adj.set_value(adj.get_upper())

    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_box.set_halign(Gtk.Align.END)
    btn_box.set_margin_top(4)

    btn_clear = Gtk.Button(label="Очистить")
    btn_clear.add_css_class("flat")
    btn_clear.add_css_class("pill")
    btn_clear.connect("clicked", lambda _: buf.set_text(""))

    btn_run = make_button("Выполнить")

    def _on_run(_btn):
        cmd_text = entry.get_text().strip()
        if not cmd_text:
            return
        _append_log(f"$ {cmd_text}\n")
        backend.run_privileged(
            ["bash", "-c", cmd_text],
            lambda line: GLib.idle_add(_append_log, line),
            lambda ok: GLib.idle_add(_append_log, f"\n[{'OK' if ok else 'ОШИБКА'}]\n"),
        )

    btn_run.connect("clicked", _on_run)
    entry.connect("apply", _on_run)

    btn_box.append(btn_clear)
    btn_box.append(btn_run)
    body.append(btn_box)

    return scroll
