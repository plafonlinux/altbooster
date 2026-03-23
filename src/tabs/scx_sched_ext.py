"""Общий блок «ядро + sched_ext» для LAVD и SCX Meteor."""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from core.sched_ext import KERNEL_IMAGE_SCHED_EXT, has_sched_ext
from ui.widgets import make_button, make_icon, make_status_icon, set_status_ok, set_status_error, clear_status


class SchedExtSupportSection:
    def __init__(self, log_fn, host: Gtk.Widget):
        self._log = log_fn
        self._host = host
        self._pending_reboot = False
        self._listeners: list[Callable[[], None]] = []
        self._row: Adw.ActionRow | None = None
        self._btn: Gtk.Button | None = None
        self._status: Gtk.Image | None = None

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in self._listeners:
            try:
                cb()
            except Exception:
                pass

    def sched_ext_ready(self) -> bool:
        return has_sched_ext()

    def append_to(self, body: Gtk.Box) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        group.set_title("Поддержка sched_ext в ядре")

        intro = Adw.ActionRow()
        intro.set_title("sched_ext")
        intro.set_subtitle(
            "Общая основа для планировщиков LAVD (scx-scheds) и SCX Meteor: в ядре должен "
            "появиться каталог /sys/kernel/sched_ext (CONFIG_SCHED_CLASS_EXT).\n\n"
            "Кнопка ниже запускает установку предлагаемого в ваших репозиториях пакета образа ядра "
            "с ориентиром на sched_ext (виртуальный пакет kernel-image-* для актуальной линии). "
            "Конкретное имя и версию смотрите в выводе apt или в каталоге пакетов ALT. "
            "После установки при необходимости выберите ядро в загрузчике и перезагрузите систему."
        )
        intro.set_activatable(False)
        intro.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))

        row = Adw.ActionRow()
        row.set_title("Ядро с поддержкой sched_ext")
        row.set_subtitle("Проверка /sys/kernel/sched_ext…")
        row.add_prefix(make_icon("cpu-symbolic"))
        status = make_status_icon()
        btn = make_button("Установить ядро")
        btn.connect("clicked", self._on_install_clicked)

        suffix = Gtk.Box(spacing=6)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(status)
        suffix.append(btn)
        row.add_suffix(suffix)

        group.add(intro)
        group.add(row)
        body.append(group)

        self._row = row
        self._btn = btn
        self._status = status
        threading.Thread(target=self._bg_refresh, daemon=True).start()
        return intro

    def _bg_refresh(self) -> None:
        GLib.idle_add(self.refresh_ui)

    def refresh_ui(self) -> None:
        if self._row is None or self._btn is None or self._status is None:
            return

        if has_sched_ext():
            self._pending_reboot = False
            set_status_ok(self._status)
            self._status.set_visible(True)
            self._row.set_subtitle(
                "В текущем ядре sched_ext доступен — можно включать LAVD или SCX Meteor."
            )
            self._btn.set_label("Готово")
            self._btn.set_sensitive(False)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        elif self._pending_reboot:
            clear_status(self._status)
            self._status.set_from_icon_name("dialog-warning-symbolic")
            self._status.set_visible(True)
            self._row.set_subtitle(
                "Пакеты установлены. Перезагрузите компьютер и загрузитесь с новым ядром — "
                "затем проверим /sys/kernel/sched_ext снова."
            )
            self._btn.set_label("Ожидается перезагрузка")
            self._btn.set_sensitive(False)
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        else:
            set_status_error(self._status)
            self._status.set_visible(True)
            self._row.set_subtitle(
                "В сейчас загруженном ядре нет sched_ext — LAVD и Meteor не запустятся "
                "до смены ядра."
            )
            self._btn.set_label("Установить ядро")
            self._btn.set_sensitive(True)
            self._btn.remove_css_class("flat")
            self._btn.add_css_class("suggested-action")

        self._notify()

    def _root(self):
        return self._host.get_root()

    def _on_install_clicked(self, _btn: Gtk.Button) -> None:
        if has_sched_ext():
            return
        root = self._root()
        if root is None:
            return

        dialog = Adw.AlertDialog(
            heading="Установить пакет ядра?",
            body=(
                "Будет запущено обновление индекса apt и установка пакета ядра, который в ALT Booster "
                "задан для поддержки sched_ext.\n\n"
                "На стабильных ветках (p10, p11) установка другой линии ядра может затронуть "
                "зависимости, модули (в том числе проприетарные драйверы) и порядок пунктов в "
                "загрузчике. В редких случаях система может не загрузиться без отката или "
                "восстановления с носителя.\n\n"
                "Продолжайте только если согласны с риском и при необходимости имеете резервную копию "
                "или снимок системы."
            ),
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("install", "Установить")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

        def _on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response != "install":
                return
            self._start_kernel_install()

        dialog.connect("response", _on_response)
        dialog.present(root)

    def _start_kernel_install(self) -> None:
        if self._btn is None or has_sched_ext():
            return
        self._btn.set_sensitive(False)
        self._btn.set_label("…")
        self._log("\n▶  Установка ядра для sched_ext (" + KERNEL_IMAGE_SCHED_EXT + ")…\n")
        win = self._root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Установка ядра…")

        def _thread() -> None:
            ok_u = backend.run_privileged_sync(["apt-get", "update"], self._log)
            ok_i = False
            if ok_u:
                ok_i = backend.run_privileged_sync(
                    ["apt-get", "install", "-y", KERNEL_IMAGE_SCHED_EXT],
                    self._log,
                )

            def _done() -> None:
                if ok_i:
                    if has_sched_ext():
                        self._log("✔  sched_ext уже доступен в текущем ядре.\n")
                        self._pending_reboot = False
                    else:
                        self._pending_reboot = True
                        self._log(
                            "✔  Ядро установлено. Перезагрузите систему и выберите новое ядро "
                            "в загрузчике при необходимости.\n"
                        )
                else:
                    self._log(
                        "✘  Не удалось установить "
                        + KERNEL_IMAGE_SCHED_EXT
                        + " (репозиторий или сеть). Повторите позже.\n"
                    )
                self.refresh_ui()
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok_i)

            GLib.idle_add(_done)

        threading.Thread(target=_thread, daemon=True).start()
