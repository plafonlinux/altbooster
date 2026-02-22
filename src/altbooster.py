#!/usr/bin/env python3
"""
ALT Booster — GTK4 GUI
Запускается от обычного пользователя. Команды выполняются через sudo.

Зависимости (ALT Linux):
  sudo apt-get install python3-module-pygobject3 libgtk4-gir libadwaita-gir

Запуск: python3 altbooster.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio
import subprocess
import threading
import time
import os
import sys
import json

CONFIG_DIR  = os.path.join(os.path.expanduser("~"), ".config", "altbooster")
CONFIG_FILE = os.path.join(CONFIG_DIR, "window.json")

DV_CACHE = "/mnt/datassd/DaVinci Resolve/Work Folders/CacheClip"
DV_PROXY  = "/mnt/datassd/DaVinci Resolve/Work Folders/ProxyMedia"

TASKS = [
    {
        "id":    "apt",
        "icon":  "user-trash-symbolic",
        "label": "Очистка APT",
        "desc":  "apt-get clean — удаляет кэш пакетов",
        "cmd":   ["apt-get", "clean"],
    },
    {
        "id":    "flatpak",
        "icon":  "application-x-addon-symbolic",
        "label": "Уборка Flatpak",
        "desc":  "Удаляет неиспользуемые runtime-библиотеки",
        "cmd":   ["flatpak", "uninstall", "--unused", "-y"],
    },
    {
        "id":    "journal",
        "icon":  "document-open-recent-symbolic",
        "label": "Сжатие журналов",
        "desc":  "journalctl --vacuum-time=14d",
        "cmd":   ["journalctl", "--vacuum-time=14d"],
    },
    {
        "id":    "davinci",
        "icon":  "drive-harddisk-symbolic",
        "label": "Кэш DaVinci",
        "desc":  "Очищает CacheClip и ProxyMedia на ADATA",
        "cmd":   ["find", DV_CACHE, DV_PROXY, "-mindepth", "1", "-delete"],
    },
    {
        "id":    "btrfs_bal",
        "icon":  "drive-multidisk-symbolic",
        "label": "Баланс Btrfs",
        "desc":  "btrfs balance -dusage=50 -musage=50 /",
        "cmd":   ["btrfs", "balance", "start", "-dusage=50", "-musage=50", "/"],
    },
    {
        "id":    "btrfs_defrag",
        "icon":  "emblem-synchronizing-symbolic",
        "label": "Дефрагментация",
        "desc":  "btrfs filesystem defragment -r -czstd /",
        "cmd":   ["btrfs", "filesystem", "defragment", "-r", "-czstd", "/"],
    },
    {
        "id":    "trim",
        "icon":  "media-flash-symbolic",
        "label": "SSD TRIM",
        "desc":  "fstrim -av — оптимизация блоков SSD",
        "cmd":   ["fstrim", "-av"],
    },
]

# Кэш пароля sudo на время сессии
_sudo_password: str | None = None


def sudo_check(password: str) -> bool:
    """Проверяет пароль через sudo -S -v (обновляет timestamp без выполнения команды)."""
    proc = subprocess.run(
        ["sudo", "-S", "-v"],
        input=password + "\n",
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def run_privileged(cmd: list[str], on_line, on_done):
    """Запускает команду через sudo -S в фоновом потоке."""
    def _worker():
        global _sudo_password
        password = _sudo_password or ""

        proc = subprocess.Popen(
            ["sudo", "-S"] + cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            proc.stdin.write(password + "\n")
            proc.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()

        # Читаем stdout и stderr параллельно — иначе буфер забивается и процесс зависает
        def _drain_stderr():
            for line in proc.stderr:
                low = line.lower()
                if "[sudo]" not in low and "password" not in low:
                    GLib.idle_add(on_line, line)

        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

        for line in proc.stdout:
            GLib.idle_add(on_line, line)

        t.join()
        proc.wait()
        GLib.idle_add(on_done, proc.returncode == 0)

    threading.Thread(target=_worker, daemon=True).start()


class PasswordDialog(Adw.AlertDialog):
    """Запрашивает пароль sudo при старте. Использует актуальный Adw.AlertDialog API."""

    def __init__(self, parent, on_success, on_cancel):
        super().__init__(
            heading="Требуется пароль sudo",
            body="ALT Booster выполняет системные команды от имени root.\nПароль сохраняется только на время сессии.",
        )
        self._on_success = on_success
        self._on_cancel  = on_cancel
        self._parent     = parent
        self._attempts   = 0
        self._done      = False

        self._entry = Gtk.PasswordEntry()
        self._entry.set_show_peek_icon(True)
        self._entry.set_property("placeholder-text", "Пароль пользователя")
        self._entry.connect("activate", lambda _: self._submit())
        self.set_extra_child(self._entry)

        self.add_response("cancel", "Отмена")
        self.add_response("ok", "Войти")
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("ok")
        self.set_close_response("cancel")
        self.connect("response", self._on_response)
        self.present(parent)

    def _on_response(self, _, response):
        # _done=True означает что авторизация уже прошла — игнорируем повторный сигнал
        if self._done:
            return
        if response == "ok":
            self._submit()
        else:
            self._on_cancel()

    def _submit(self):
        password = self._entry.get_text()
        if not password:
            return
        self.set_response_enabled("ok", False)
        self._entry.set_sensitive(False)
        threading.Thread(
            target=lambda: GLib.idle_add(self._check_done, password, sudo_check(password)),
            daemon=True,
        ).start()

    def _check_done(self, password, ok):
        global _sudo_password
        if ok:
            _sudo_password = password
            self._done = True   # блокируем повторный response от close()
            self.close()
            self._on_success()
        else:
            self._attempts += 1
            self.set_body(f"❌ Неверный пароль (попытка {self._attempts}). Попробуйте снова.")
            self._entry.set_text("")
            self._entry.set_sensitive(True)
            self.set_response_enabled("ok", True)
            self._entry.grab_focus()

# ── Строка одной задачи ────────────────────────────────────────────────────
class TaskRow(Adw.ActionRow):
    def __init__(self, task: dict, on_log, on_progress_changed):
        super().__init__()
        self._task            = task
        self._on_log          = on_log
        self._on_prog_changed = on_progress_changed
        self._running         = False
        self.result           = None

        self.set_title(task["label"])
        self.set_subtitle(task["desc"])

        icon = Gtk.Image.new_from_icon_name(task["icon"])
        icon.set_pixel_size(22)
        self.add_prefix(icon)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        right.set_valign(Gtk.Align.CENTER)
        right.set_size_request(320, -1)

        self._prog = Gtk.ProgressBar()
        self._prog.set_hexpand(True)
        self._prog.set_valign(Gtk.Align.CENTER)

        self._status = Gtk.Label(label="   ")
        self._status.set_width_chars(2)
        self._status.set_xalign(0.5)

        self._btn = Gtk.Button(label="Запустить")
        self._btn.set_size_request(110, -1)
        self._btn.add_css_class("suggested-action")
        self._btn.add_css_class("pill")
        self._btn.connect("clicked", lambda _: self.start())

        right.append(self._prog)
        right.append(self._status)
        right.append(self._btn)
        self.add_suffix(right)

    def start(self):
        if self._running:
            return
        self._running = True
        self.result   = None
        self._btn.set_sensitive(False)
        self._btn.set_label("…")
        self._status.set_label("⏳")
        self._prog.set_fraction(0.0)
        self._on_log(f"\n▶  {self._task['label']}...\n")
        GLib.timeout_add(110, self._pulse)
        run_privileged(self._task["cmd"], self._on_log, self._finish)

    def _pulse(self):
        if self._running:
            self._prog.pulse()
            return True
        return False

    def _finish(self, ok: bool):
        self._running = False
        self.result   = ok
        self._prog.set_fraction(1.0 if ok else 0.0)
        self._status.set_label("✅" if ok else "❌")
        self._btn.set_label("Повтор")
        self._btn.set_sensitive(True)
        if ok:
            self._btn.remove_css_class("suggested-action")
            self._btn.add_css_class("flat")
        self._on_log(f"{'✔  Готово' if ok else '✘  Ошибка'}: {self._task['label']}\n")
        self._on_prog_changed()


# ── Главное окно ───────────────────────────────────────────────────────────
class PlafonWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("ALT Booster")
        w, h = self._load_window_size()
        self.set_default_size(w, h)
        self._rows: list[TaskRow] = []
        self._run_all_active = False
        self.connect("close-request", self._on_close)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        self.set_content(scroll)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(root)

        # Шапка
        header = Adw.HeaderBar()
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tb.set_halign(Gtk.Align.CENTER)
        t1 = Gtk.Label(label="ALT Booster")
        t1.add_css_class("title-1")
        tb.append(t1)
        header.set_title_widget(tb)
        root.append(header)

        # Тело
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(20); body.set_margin_bottom(20)
        body.set_margin_start(20); body.set_margin_end(20)
        body.set_hexpand(True)
        root.append(body)

        # Общий прогресс
        ov_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        body.append(ov_box)
        ov_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        lbl_ov = Gtk.Label(label="Общий прогресс")
        lbl_ov.set_halign(Gtk.Align.START)
        lbl_ov.add_css_class("caption")
        lbl_ov.set_hexpand(True)
        self._ov_count = Gtk.Label(label=f"0 / {len(TASKS)} задач")
        self._ov_count.add_css_class("caption")
        self._ov_count.add_css_class("dim-label")
        ov_head.append(lbl_ov); ov_head.append(self._ov_count)
        ov_box.append(ov_head)
        self._ov_bar = Gtk.ProgressBar()
        self._ov_bar.set_hexpand(True)
        ov_box.append(self._ov_bar)

        # Кнопки управления — зелёная и красная рядом
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_row.set_halign(Gtk.Align.CENTER)

        self._btn_all = Gtk.Button(label="Запустить все задачи")
        self._btn_all.add_css_class("suggested-action")
        self._btn_all.add_css_class("pill")
        self._btn_all.connect("clicked", self._on_run_all)

        btn_epm = Gtk.Button(label="Обновить систему через EPM")
        btn_epm.add_css_class("destructive-action")
        btn_epm.add_css_class("pill")
        btn_epm.connect("clicked", self._on_epm_now)

        btn_row.append(self._btn_all)
        btn_row.append(btn_epm)
        body.append(btn_row)

        # Задачи
        tasks_group = Adw.PreferencesGroup()
        tasks_group.set_title("Задачи обслуживания")
        tasks_group.set_description("Запускайте каждую задачу отдельно или используйте кнопку выше")
        body.append(tasks_group)
        for task in TASKS:
            row = TaskRow(task, self._log, self._update_overall)
            self._rows.append(row)
            tasks_group.add(row)

        # Лог
        log_group = Adw.PreferencesGroup()
        log_group.set_title("Лог выполнения")
        body.append(log_group)
        log_frame = Gtk.Frame()
        log_frame.add_css_class("card")
        log_group.add(log_frame)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(175)
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_frame.set_child(log_scroll)
        self._tv = Gtk.TextView()
        self._tv.set_editable(False)
        self._tv.set_cursor_visible(False)
        self._tv.set_monospace(True)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.set_margin_start(10); self._tv.set_margin_end(10)
        self._tv.set_margin_top(8);   self._tv.set_margin_bottom(8)
        self._buf = self._tv.get_buffer()
        log_scroll.set_child(self._tv)

        # Опции — под спойлером
        post_group = Adw.PreferencesGroup()
        body.append(post_group)

        expander = Adw.ExpanderRow()
        expander.set_title("Дополнительно")
        expander.set_subtitle("Обновление системы и прочие настройки")
        post_group.add(expander)

        self._sw_epm = Adw.SwitchRow()
        self._sw_epm.set_title("Обновить систему через EPM после всех задач")
        self._sw_epm.set_subtitle("epm update &amp;&amp; epm full-upgrade &amp;&amp; apt-get clean")
        expander.add_row(self._sw_epm)



    # ── Авторизация ────────────────────────────────────────────────────────
    def ask_password(self):
        """Показывает диалог пароля при старте. Кнопки задач заблокированы до авторизации."""
        self._set_tasks_sensitive(False)
        PasswordDialog(
            parent=self,
            on_success=self._on_auth_success,
            on_cancel=self.close,
        )

    def _on_auth_success(self):
        self._set_tasks_sensitive(True)
        self._log("🔓 Авторизация успешна\n")

    def _set_tasks_sensitive(self, sensitive: bool):
        self._btn_all.set_sensitive(sensitive)
        for row in self._rows:
            row._btn.set_sensitive(sensitive)

    # ── Размер окна ────────────────────────────────────────────────────────
    def _load_window_size(self):
        try:
            with open(CONFIG_FILE) as f:
                d = json.load(f)
                return d.get("width", 660), d.get("height", 820)
        except Exception:
            return 660, 820

    def _save_window_size(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump({"width": self.get_width(), "height": self.get_height()}, f)
        except OSError:
            pass

    def _on_close(self, _):
        self._save_window_size()
        return False

    # ── «Запустить всё» ────────────────────────────────────────────────────
    def _on_run_all(self, _):
        if self._run_all_active:
            return
        self._run_all_active = True
        self._btn_all.set_sensitive(False)
        self._btn_all.set_label("⏳  Выполняется...")
        threading.Thread(target=self._run_all_worker, daemon=True).start()

    def _run_all_worker(self):
        for row in self._rows:
            GLib.idle_add(row.start)
            while row._running or row.result is None:
                time.sleep(0.2)
        GLib.idle_add(self._run_all_done)

    def _run_all_done(self):
        self._run_all_active = False
        self._btn_all.set_sensitive(True)
        self._btn_all.set_label("Запустить все задачи последовательно")
        ok_count = sum(1 for r in self._rows if r.result is True)
        notif = Gio.Notification.new("ALT Booster: обслуживание завершено")
        notif.set_body(f"Успешно: {ok_count} из {len(self._rows)} задач")
        self.get_application().send_notification("altbooster-done", notif)
        if self._sw_epm.get_active():
            self._run_epm()


    # ── EPM ────────────────────────────────────────────────────────────────
    def _on_epm_now(self, _):
        self._run_epm()

    def _run_epm(self):
        self._log("\n▶  EPM: обновление системы...\n")
        cmds = [["epm", "update"], ["epm", "full-upgrade"], ["apt-get", "clean"]]
        def _chain(idx):
            if idx >= len(cmds):
                GLib.idle_add(self._log, "✔  EPM завершён\n")
                return
            run_privileged(cmds[idx], self._log, lambda ok: _chain(idx + 1))
        _chain(0)

    # ── Общий прогресс ─────────────────────────────────────────────────────
    def _update_overall(self):
        done  = sum(1 for r in self._rows if r.result is not None)
        total = len(self._rows)
        self._ov_bar.set_fraction(done / total if total else 0.0)
        self._ov_count.set_label(f"{done} / {total} задач")

    # ── Лог ────────────────────────────────────────────────────────────────
    def _log(self, text: str):
        end  = self._buf.get_end_iter()
        self._buf.insert(end, text)
        end  = self._buf.get_end_iter()
        mark = self._buf.create_mark(None, end, False)
        self._tv.scroll_mark_onscreen(mark)


# ── Приложение ─────────────────────────────────────────────────────────────
class AltBoosterApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="ru.altbooster.app",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = PlafonWindow(application=app)
        win.present()
        # Всегда запрашиваем пароль при старте — так он гарантированно закэшируется
        GLib.idle_add(win.ask_password)


if __name__ == "__main__":
    if os.geteuid() == 0:
        print("⚠  Не запускайте GUI от root. Используйте обычного пользователя.")
        sys.exit(1)
    AltBoosterApp().run(sys.argv)
