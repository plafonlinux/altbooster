import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio
import threading
import time
import os
import json
import tempfile
import urllib.request
import subprocess

import config
import backend

class PasswordDialog(Adw.AlertDialog):
    def __init__(self, parent, on_success, on_cancel):
        super().__init__(heading="Требуется пароль sudo",
            body="ALT Booster выполняет системные команды от имени root.\nПароль сохраняется только на время сессии.")
        self._on_success = on_success; self._on_cancel = on_cancel
        self._attempts = 0; self._done = False
        self._entry = Gtk.PasswordEntry()
        self._entry.set_show_peek_icon(True)
        self._entry.set_property("placeholder-text", "Пароль пользователя")
        self._entry.connect("activate", lambda _: self._submit())
        self.set_extra_child(self._entry)
        self.add_response("cancel","Отмена"); self.add_response("ok","Войти")
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("ok"); self.set_close_response("cancel")
        self.connect("response", self._on_response); self.present(parent)

    def _on_response(self, _, r):
        if self._done: return
        if r == "ok": self._submit()
        else: self._on_cancel()

    def _submit(self):
        pw = self._entry.get_text()
        if not pw: return
        self.set_response_enabled("ok", False); self._entry.set_sensitive(False)
        threading.Thread(target=lambda: GLib.idle_add(self._done_cb, pw, backend.sudo_check(pw)), daemon=True).start()

    def _done_cb(self, pw, ok):
        if ok:
            backend.set_sudo_password(pw); self._done = True; self.close(); self._on_success()
        else:
            self._attempts += 1
            self.set_body(f"❌ Неверный пароль (попытка {self._attempts}). Попробуйте снова.")
            self._entry.set_text(""); self._entry.set_sensitive(True)
            self.set_response_enabled("ok", True); self._entry.grab_focus()

class SettingRow(Adw.ActionRow):
    def __init__(self, icon, title, subtitle, btn_label, on_activate, check_fn, state_key):
        super().__init__()
        self.set_title(title); self.set_subtitle(subtitle)
        self._check_fn = check_fn; self._on_activate = on_activate; self._state_key = state_key
        ico = Gtk.Image.new_from_icon_name(icon); ico.set_pixel_size(22); self.add_prefix(ico)
        self._orig_label = btn_label
        self._status = Gtk.Image(); self._status.set_pixel_size(18)
        self._btn = Gtk.Button(label=btn_label); self._btn.set_size_request(130, -1)
        self._btn.add_css_class("suggested-action"); self._btn.add_css_class("pill")
        self._btn.connect("clicked", lambda _: self._on_activate(self))
        self._btn.set_sensitive(False)
        box = Gtk.Box(spacing=10); box.set_valign(Gtk.Align.CENTER)
        box.append(self._status); box.append(self._btn); self.add_suffix(box)
        # Показываем из кэша мгновенно
        cached = config.state_get(state_key)
        if cached is True:
            self._set_ui(True)
        elif "kbd" not in state_key and check_fn is not None:
            # Перепроверяем только если нет сохранённого результата
            threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self):
        try: enabled = self._check_fn()
        except: enabled = False
        config.state_set(self._state_key, enabled); GLib.idle_add(self._set_ui, enabled)

    def _set_ui(self, enabled: bool):
        if enabled:
            self._status.set_from_icon_name("object-select-symbolic")
            self._status.add_css_class("success")
            self._btn.set_label("Активировано"); self._btn.set_sensitive(False)
            self._btn.remove_css_class("suggested-action"); self._btn.add_css_class("flat")
        else:
            self._status.clear()
            self._status.remove_css_class("success")
            self._btn.set_label(self._orig_label); self._btn.set_sensitive(True)
            self._btn.remove_css_class("flat"); self._btn.add_css_class("suggested-action")

    def set_working(self): self._btn.set_sensitive(False); self._btn.set_label("…")
    def set_done(self, ok: bool): 
        if ok: config.state_set(self._state_key, True)
        self._set_ui(ok)
        if not ok: self._btn.set_label("Повторить"); self._btn.set_sensitive(True)

class AppRow(Adw.ActionRow):
    def __init__(self, app, log_fn, on_change_cb):
        super().__init__()
        self._app = app; self._log = log_fn; self._on_change = on_change_cb; self._installing = False
        self.set_title(app["label"]); self.set_subtitle(app["desc"]); self._state_key = f"app_{app['id']}"
        self._status = Gtk.Image(); self._status.set_pixel_size(18); self.add_prefix(self._status)
        self._btn = Gtk.Button(label="Установить"); self._btn.set_size_request(120, -1); self._btn.add_css_class("suggested-action"); self._btn.add_css_class("pill")
        self._btn.connect("clicked", self._on_install); self._btn.set_sensitive(False)
        self._trash_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic"); self._trash_btn.add_css_class("destructive-action"); self._trash_btn.set_valign(Gtk.Align.CENTER)
        self._trash_btn.connect("clicked", self._on_uninstall); self._trash_btn.set_visible(False)
        self._prog = Gtk.ProgressBar(); self._prog.set_hexpand(True); self._prog.set_valign(Gtk.Align.CENTER); self._prog.set_visible(False)
        suffix = Gtk.Box(spacing=8); suffix.set_valign(Gtk.Align.CENTER); suffix.append(self._prog); suffix.append(self._btn); suffix.append(self._trash_btn); self.add_suffix(suffix)
        threading.Thread(target=self._check, daemon=True).start()

    def is_installed(self): return config.state_get(self._state_key) is True
    def _check(self):
        src = self._app["source"]; installed = backend.check_app_installed(src)
        config.state_set(self._state_key, installed); GLib.idle_add(self._set_installed_ui, installed)

    def _set_installed_ui(self, installed: bool):
        if installed:
            self._status.set_from_icon_name("object-select-symbolic")
            self._status.add_css_class("success")
            self._btn.set_visible(False); self._prog.set_visible(False)
            self._trash_btn.set_visible(True); self._trash_btn.set_sensitive(True)
        else:
            self._status.clear()
            self._status.remove_css_class("success")
            self._btn.set_visible(True); self._btn.set_label("Установить"); self._btn.set_sensitive(True)
            self._trash_btn.set_visible(False)
        if self._on_change: self._on_change()

    def _on_install(self, _=None):
        if self._installing or self.is_installed(): return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята другим процессом обновления. Подождите...\n"); return
        self._installing = True; src = self._app["source"]; self._btn.set_sensitive(False); self._btn.set_label("…")
        self._prog.set_visible(True); self._prog.set_fraction(0.0); GLib.timeout_add(120, self._pulse)
        self._log(f"\n▶  Установка {self._app['label']} ({src['label']})...\n")
        backend.run_privileged(src["cmd"], self._log, self._install_done)

    def _on_uninstall(self, _):
        if self._installing: return
        if backend.is_system_busy():
            self._log("\n⚠  Система занята. Удаление невозможно...\n"); return
        self._installing = True; self._trash_btn.set_sensitive(False); self._prog.set_visible(True); self._prog.set_fraction(0.0); GLib.timeout_add(120, self._pulse)
        src = self._app["source"]; kind, pkg = src["check"]
        if kind == "flatpak": cmd = ["flatpak", "uninstall", "-y", pkg]
        elif kind == "rpm": cmd = ["epm", "-e", pkg]
        else: cmd = ["rm", "-rf", os.path.expanduser("~/.local/share/monitor-control"), os.path.expanduser("~/Monic")]
        self._log(f"\n▶  Удаление {self._app['label']}...\n"); backend.run_privileged(cmd, self._log, self._uninstall_done)

    def _pulse(self):
        if self._installing: self._prog.pulse(); return True
        return False

    def _install_done(self, ok: bool):
        self._installing = False; self._prog.set_visible(False)
        if ok: self._log(f"✔  {self._app['label']} установлен!\n"); config.state_set(self._state_key, True); self._set_installed_ui(True)
        else: self._log(f"✘  Ошибка установки {self._app['label']}\n"); self._btn.set_sensitive(True); self._btn.set_label("Повторить")

    def _uninstall_done(self, ok: bool):
        self._installing = False; self._prog.set_visible(False)
        if ok: self._log(f"✔  {self._app['label']} удалён!\n"); config.state_set(self._state_key, False); self._set_installed_ui(False)
        else: self._log(f"✘  Ошибка удаления {self._app['label']}\n"); self._trash_btn.set_sensitive(True)

class TaskRow(Adw.ActionRow):
    def __init__(self, task, on_log, on_prog):
        super().__init__()
        self._task = task; self._on_log = on_log; self._on_prog = on_prog; self._running = False; self.result = None
        self.set_title(task["label"]); self.set_subtitle(task["desc"])
        ico = Gtk.Image.new_from_icon_name(task["icon"]); ico.set_pixel_size(22); self.add_prefix(ico)
        right = Gtk.Box(spacing=10); right.set_valign(Gtk.Align.CENTER); right.set_size_request(320,-1)
        self._prog = Gtk.ProgressBar(); self._prog.set_hexpand(True); self._prog.set_valign(Gtk.Align.CENTER)
        self._st = Gtk.Image(); self._st.set_pixel_size(18)
        self._btn = Gtk.Button(label="Запустить"); self._btn.set_size_request(110,-1); self._btn.add_css_class("suggested-action"); self._btn.add_css_class("pill")
        self._btn.connect("clicked", lambda _: self.start())
        right.append(self._prog); right.append(self._st); right.append(self._btn); self.add_suffix(right)

    def start(self):
        if self._running: return
        self._running = True; self.result = None; self._btn.set_sensitive(False); self._btn.set_label("…"); self._st.clear(); self._prog.set_fraction(0.0)
        cmd = self._task["cmd"].copy()
        if self._task["id"] == "davinci": cmd = ["find", config.get_dv_cache(), config.get_dv_proxy(), "-mindepth", "1", "-delete"]
        self._on_log(f"\n▶  {self._task['label']}...\n"); GLib.timeout_add(110, self._pulse); backend.run_privileged(cmd, self._on_log, self._finish)

    def _pulse(self):
        if self._running: self._prog.pulse(); return True
        return False

    def _finish(self, ok):
        self._running = False; self.result = ok; self._prog.set_fraction(1.0 if ok else 0.0)
        if ok:
            self._st.set_from_icon_name("object-select-symbolic"); self._st.add_css_class("success")
        else:
            self._st.set_from_icon_name("dialog-error-symbolic"); self._st.remove_css_class("success")
        self._btn.set_label("Повтор"); self._btn.set_sensitive(True)
        if ok: self._btn.remove_css_class("suggested-action"); self._btn.add_css_class("flat")
        self._on_log(f"{'✔  Готово' if ok else '✘  Ошибка'}: {self._task['label']}\n"); self._on_prog()

class SetupPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn; sc = Gtk.ScrolledWindow(); sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); sc.set_hexpand(True); sc.set_vexpand(True); self.append(sc)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18); body.set_margin_top(20); body.set_margin_bottom(20); body.set_margin_start(20); body.set_margin_end(20); sc.set_child(body)
        g = Adw.PreferencesGroup(); g.set_title("Система"); body.append(g)
        self._r_sudo = SettingRow("security-high-symbolic","Включить sudo", "control sudowheel enabled","Активировать",self._on_sudo,backend.is_sudo_enabled,"setting_sudo"); g.add(self._r_sudo)
        self._r_flathub = SettingRow("application-x-addon-symbolic","Подключить Flathub", "Устанавливает flatpak и flathub","Подключить",self._on_flathub,backend.is_flathub_enabled,"setting_flathub"); g.add(self._r_flathub)
        self._r_trim_auto = SettingRow("media-flash-symbolic", "Автоматический TRIM", "Включает еженедельную очистку блоков SSD (fstrim.timer)", "Включить", self._on_trim_timer, backend.is_fstrim_enabled, "setting_trim_auto"); g.add(self._r_trim_auto)
        self._r_journal_opt = SettingRow("document-open-recent-symbolic", "Лимиты журналов", "SystemMaxUse=100M и сжатие в journald.conf", "Настроить", self._on_journal_limit, backend.is_journal_optimized, "setting_journal_opt"); g.add(self._r_journal_opt)
        self._r_scale = SettingRow("video-display-symbolic","Дробное масштабирование", "Включает scale-monitor-framebuffer","Включить",self._on_scale,backend.is_fractional_scaling_enabled,"setting_scale"); g.add(self._r_scale)
        g2 = Adw.PreferencesGroup(); g2.set_title("Раскладка клавиатуры"); body.append(g2)
        self._r_alt = SettingRow("input-keyboard-symbolic","Alt + Shift", "Классическое переключение раскладки","Включить",self._on_altshift, None, "setting_kbd_altshift"); g2.add(self._r_alt)
        self._r_caps = SettingRow("input-keyboard-symbolic","CapsLock", "Переключение раскладки кнопкой CapsLock","Включить",self._on_capslock, None, "setting_kbd_capslock"); g2.add(self._r_caps)
        threading.Thread(target=self._detect_kbd_mode, daemon=True).start()
        g3 = Adw.PreferencesGroup(); g3.set_title("Обновление системы"); body.append(g3)
        er = Adw.ActionRow(); er.set_title("Обновить систему через EPM"); er.set_subtitle("epm update &amp;&amp; epm full-upgrade &amp;&amp; apt-get clean")
        ei = Gtk.Image.new_from_icon_name("software-update-available-symbolic"); ei.set_pixel_size(22); er.add_prefix(ei); self._epm_st = Gtk.Image(); self._epm_st.set_pixel_size(18)
        self._epm_btn = Gtk.Button(label="Запустить"); self._epm_btn.set_size_request(130,-1); self._epm_btn.add_css_class("destructive-action"); self._epm_btn.add_css_class("pill"); self._epm_btn.connect("clicked", self._on_epm)
        eb = Gtk.Box(spacing=10); eb.set_valign(Gtk.Align.CENTER); eb.append(self._epm_st); eb.append(self._epm_btn); er.add_suffix(eb); g3.add(er)

    def _on_trim_timer(self, row):
        if backend.is_system_busy(): self._log("\n⚠  Система занята. Попробуйте позже.\n"); return
        row.set_working(); self._log("\n▶  Включение fstrim.timer...\n")
        backend.run_privileged(["systemctl", "enable", "--now", "fstrim.timer"], self._log, lambda ok: GLib.idle_add(row.set_done, ok) or GLib.idle_add(self._log, f"{'✔  Таймер TRIM включен!' if ok else '✘  Ошибка'}\n"))

    def _on_journal_limit(self, row):
        if backend.is_system_busy(): self._log("\n⚠  Система занята. Попробуйте позже.\n"); return
        row.set_working(); self._log("\n▶  Оптимизация journald.conf...\n")
        cmd = ["bash", "-c", "sed -i 's/^#\\?SystemMaxUse=.*/SystemMaxUse=100M/' /etc/systemd/journald.conf && sed -i 's/^#\\?Compress=.*/Compress=yes/' /etc/systemd/journald.conf && systemctl restart systemd-journald"]
        backend.run_privileged(cmd, self._log, lambda ok: GLib.idle_add(row.set_done, ok) or GLib.idle_add(self._log, f"{'✔  Лимиты применены!' if ok else '✘  Ошибка'}\n"))

    def _detect_kbd_mode(self):
        """Определяет текущий режим и сохраняет в state. Использует кэш если уже установлен."""
        from config import state_get, state_set
        # Если режим уже сохранён — берём из кэша
        mode = state_get("setting_kbd_mode")
        if mode == "altshift":
            GLib.idle_add(self._r_alt._set_ui, True); GLib.idle_add(self._r_caps._set_ui, False); return
        if mode == "capslock":
            GLib.idle_add(self._r_caps._set_ui, True); GLib.idle_add(self._r_alt._set_ui, False); return
        # Иначе — читаем gsettings и сохраняем
        v = backend.gsettings_get("org.gnome.desktop.wm.keybindings", "switch-input-source")
        is_caps = "Caps" in v; is_alt = "Alt_L" in v or "Shift>Alt" in v
        if is_caps: state_set("setting_kbd_mode", "capslock")
        elif is_alt: state_set("setting_kbd_mode", "altshift")
        GLib.idle_add(self._r_caps._set_ui, is_caps); GLib.idle_add(self._r_alt._set_ui, is_alt)

    def _on_sudo(self, row):
        row.set_working(); self._log("\n▶  Включение sudo...\n")
        backend.run_privileged(["control","sudowheel","enabled"], lambda l: None, lambda ok: GLib.idle_add(row.set_done, ok) or GLib.idle_add(self._log, f"{'✔  sudo включён!' if ok else '✘  Ошибка'}\n"))

    def _on_flathub(self, row):
        if backend.is_system_busy(): self._log("\n⚠  Система занята. Попробуйте позже.\n"); return
        row.set_working(); self._log("\n▶  Установка Flatpak и Flathub...\n")
        def s1(ok):
            if not ok: GLib.idle_add(row.set_done,False); return
            backend.run_privileged(["apt-get","install","-y","flatpak-repo-flathub"], self._log, lambda ok2: GLib.idle_add(row.set_done,ok2) or GLib.idle_add(self._log, f"{'✔  Flathub готов!' if ok2 else '✘  Ошибка'}\n"))
        backend.run_privileged(["apt-get","install","-y","flatpak"], self._log, s1)

    def _on_scale(self, row):
        row.set_working(); self._log("\n▶  Масштабирование...\n")
        def _do():
            cur = backend.gsettings_get("org.gnome.mutter","experimental-features")
            try: import ast; items = ast.literal_eval(cur) if cur not in ("@as []","[]","") else []
            except: items = []
            if "scale-monitor-framebuffer" not in items: items.append("scale-monitor-framebuffer")
            ok = backend.run_gsettings(["set","org.gnome.mutter","experimental-features",str(items)]); GLib.idle_add(row.set_done,ok); GLib.idle_add(self._log, "✔  Включено!\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_altshift(self, row):
        row.set_working(); self._log("\n▶  Настройка Alt+Shift...\n")
        def _do():
            ok = (backend.run_gsettings(["set","org.gnome.desktop.wm.keybindings","switch-input-source","['<Shift>Alt_L']"])
               and backend.run_gsettings(["set","org.gnome.desktop.wm.keybindings","switch-input-source-backward","['<Alt>Shift_L']"]))
            if ok:
                import config as _cfg; _cfg.state_set("setting_kbd_mode", "altshift")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_caps._set_ui, False)
            GLib.idle_add(self._log, "✔  Alt+Shift готов!\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_capslock(self, row):
        row.set_working(); self._log("\n▶  Настройка CapsLock...\n")
        def _do():
            ok = (backend.run_gsettings(["set","org.gnome.desktop.wm.keybindings","switch-input-source","['Caps_Lock']"])
               and backend.run_gsettings(["set","org.gnome.desktop.wm.keybindings","switch-input-source-backward","['<Shift>Caps_Lock']"]))
            if ok:
                import config as _cfg; _cfg.state_set("setting_kbd_mode", "capslock")
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._r_alt._set_ui, False)
            GLib.idle_add(self._log, "✔  CapsLock готов!\n")
        threading.Thread(target=_do, daemon=True).start()

    def _on_epm(self, _):
        if backend.is_system_busy(): self._log("\n⚠  Система занята другим процессом обновления.\n"); return
        self._epm_btn.set_sensitive(False); self._epm_btn.set_label("…"); self._log("\n▶  EPM: обновление системы...\n")
        cmds = [["epm","update"],["epm","full-upgrade"],["apt-get","clean"]]
        def ch(i):
            if i >= len(cmds): GLib.idle_add(self._epm_fin, True); return
            backend.run_privileged(cmds[i], self._log, lambda ok: ch(i+1) if ok else GLib.idle_add(self._epm_fin,False))
        ch(0)

    def _epm_fin(self, ok):
        self._epm_st.set_label("✅" if ok else "❌"); self._epm_btn.set_label("Запустить"); self._epm_btn.set_sensitive(True); self._log(f"{'✔  EPM завершён!' if ok else '✘  Ошибка EPM'}\n")

class AppsPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn; self._rows = []; self._busy = False; sc = Gtk.ScrolledWindow(); sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); sc.set_hexpand(True); sc.set_vexpand(True); self.append(sc)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18); body.set_margin_top(20); body.set_margin_bottom(20); body.set_margin_start(20); body.set_margin_end(20); sc.set_child(body)
        self._btn_all = Gtk.Button(label="Установить все недостающие"); self._btn_all.add_css_class("suggested-action"); self._btn_all.add_css_class("pill"); self._btn_all.set_halign(Gtk.Align.CENTER); self._btn_all.connect("clicked", self._run_all); body.append(self._btn_all)
        for gd in config.APPS:
            group = Adw.PreferencesGroup(); body.append(group); expander = Adw.ExpanderRow(); expander.set_title(gd["group"]); expander.set_subtitle(f"Доступно приложений: {len(gd['items'])}"); expander.set_expanded(False); group.add(expander)
            for app in gd["items"]: row = AppRow(app, log_fn, self._refresh_btn_all); self._rows.append(row); expander.add_row(row)
        GLib.idle_add(self._refresh_btn_all)

    def _refresh_btn_all(self):
        missing = [r for r in self._rows if not r.is_installed()]; any_missing = len(missing) > 0; self._btn_all.set_sensitive(any_missing)
        if any_missing: self._btn_all.add_css_class("suggested-action"); self._btn_all.remove_css_class("flat"); self._btn_all.set_label("Установить все недостающие")
        else: self._btn_all.remove_css_class("suggested-action"); self._btn_all.add_css_class("flat"); self._btn_all.set_label("✅ Все приложения установлены")

    def _run_all(self, _):
        if self._busy: return
        if backend.is_system_busy(): self._log("\n⚠  Система занята. Массовая установка невозможна.\n"); return
        self._busy = True; self._btn_all.set_sensitive(False); self._btn_all.set_label("⏳  Установка...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        to_install = [r for r in self._rows if not r.is_installed()]
        for r in to_install:
            GLib.idle_add(r._on_install);
            while r._installing: time.sleep(0.5)
        GLib.idle_add(self._done)

    def _done(self): self._busy = False; self._refresh_btn_all(); self._log("\n✔  Массовая установка завершена\n")

class DaVinciPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL); self._log = log_fn; sc = Gtk.ScrolledWindow(); sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); sc.set_hexpand(True); sc.set_vexpand(True); self.append(sc)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18); body.set_margin_top(20); body.set_margin_bottom(20); body.set_margin_start(20); body.set_margin_end(20); sc.set_child(body)
        
        # 1. Установка DaVinci Resolve
        g1 = Adw.PreferencesGroup(); g1.set_title("Установка"); body.append(g1)
        row = Adw.ActionRow(); row.set_title("DaVinci Resolve"); row.set_subtitle("epm play davinci-resolve")
        ico = Gtk.Image.new_from_icon_name("davinci-symbolic"); ico.set_pixel_size(22); row.add_prefix(ico)
        self._inst_st = Gtk.Image(); self._inst_st.set_pixel_size(18)
        self._inst_btn = Gtk.Button(label="Установить"); self._inst_btn.set_size_request(130,-1)
        self._inst_btn.add_css_class("suggested-action"); self._inst_btn.add_css_class("pill")
        self._inst_btn.connect("clicked", self._on_install); self._inst_btn.set_sensitive(False)
        ib = Gtk.Box(spacing=10); ib.set_valign(Gtk.Align.CENTER)
        ib.append(self._inst_st); ib.append(self._inst_btn); row.add_suffix(ib); g1.add(row)
        threading.Thread(target=self._check, daemon=True).start()

        # 2. Первичная настройка (спойлер)
        g_setup = Adw.PreferencesGroup(); body.append(g_setup)
        expander = Adw.ExpanderRow()
        expander.set_title("Первичная настройка")
        expander.set_subtitle("PostInstall, AMD Radeon, AAC кодек, Fairlight")
        expander.set_expanded(False)
        g_setup.add(expander)

        # 2.1 PostInstall
        post_group = Adw.PreferencesGroup(); post_group.set_title("PostInstall")
        post_group.set_description("Выполните после установки DaVinci Resolve")
        expander.add_row(post_group)

        post_row = Adw.ActionRow()
        post_row.set_title("Удалить конфликтующие библиотеки")
        post_row.set_subtitle("Удаляет libglib/libgio/libgmodule из /opt/resolve/libs")
        post_ico = Gtk.Image.new_from_icon_name("emblem-important-symbolic"); post_ico.set_pixel_size(22)
        post_row.add_prefix(post_ico)
        self._post_st = Gtk.Image(); self._post_st.set_pixel_size(18)
        self._post_btn = Gtk.Button(label="Выполнить"); self._post_btn.set_size_request(130, -1)
        self._post_btn.add_css_class("destructive-action"); self._post_btn.add_css_class("pill")
        self._post_btn.connect("clicked", self._on_postinstall)
        post_box = Gtk.Box(spacing=10); post_box.set_valign(Gtk.Align.CENTER)
        post_box.append(self._post_st); post_box.append(self._post_btn)
        post_row.add_suffix(post_box); post_group.add(post_row)

        # 2.2 AMD Radeon (3-я позиция — внутри спойлера первой)
        self._build_amd_section(expander)

        # 2.3 AAC Audio
        aac_group = Adw.PreferencesGroup(); aac_group.set_title("AAC Audio кодек")
        expander.add_row(aac_group)
        aac_row = Adw.ActionRow(); aac_row.set_title("FFmpeg AAC Encoder Plugin")
        aac_row.set_subtitle("Плагин для экспорта AAC аудио")
        aac_ico = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic"); aac_ico.set_pixel_size(22)
        aac_row.add_prefix(aac_ico)
        self._aac_st = Gtk.Image(); self._aac_st.set_pixel_size(18)
        self._aac_btn = Gtk.Button(label="Установить"); self._aac_btn.set_size_request(130, -1)
        self._aac_btn.add_css_class("suggested-action"); self._aac_btn.add_css_class("pill")
        self._aac_btn.connect("clicked", self._on_aac_install); self._aac_btn.set_sensitive(False)
        ar = Gtk.Box(spacing=10); ar.set_valign(Gtk.Align.CENTER)
        ar.append(self._aac_st); ar.append(self._aac_btn); aac_row.add_suffix(ar); aac_group.add(aac_row)
        threading.Thread(target=self._check_aac, daemon=True).start()

        # 2.4 Fairlight Audio
        self._build_fairlight_section(expander)

        # 3. Кэш — в самом низу
        g2 = Adw.PreferencesGroup(); g2.set_title("Кэш"); body.append(g2)
        g2.set_description("Укажите папки кэша и запустите очистку при необходимости")

        self._cache_row = Adw.ActionRow(); self._cache_row.set_title("CacheClip")
        self._cache_row.set_subtitle(config.get_dv_cache())
        fi1 = Gtk.Image.new_from_icon_name("folder-symbolic"); fi1.set_pixel_size(22)
        self._cache_row.add_prefix(fi1)
        cache_btn = Gtk.Button(label="Выбрать"); cache_btn.add_css_class("flat"); cache_btn.set_valign(Gtk.Align.CENTER)
        cache_btn.connect("clicked", lambda _, r=self._cache_row, k="dv_cache_path": self._pick_folder(r, k))
        self._cache_row.add_suffix(cache_btn); g2.add(self._cache_row)

        self._proxy_row = Adw.ActionRow(); self._proxy_row.set_title("ProxyMedia")
        self._proxy_row.set_subtitle(config.get_dv_proxy())
        fi2 = Gtk.Image.new_from_icon_name("folder-symbolic"); fi2.set_pixel_size(22)
        self._proxy_row.add_prefix(fi2)
        proxy_btn = Gtk.Button(label="Выбрать"); proxy_btn.add_css_class("flat"); proxy_btn.set_valign(Gtk.Align.CENTER)
        proxy_btn.connect("clicked", lambda _, r=self._proxy_row, k="dv_proxy_path": self._pick_folder(r, k))
        self._proxy_row.add_suffix(proxy_btn); g2.add(self._proxy_row)

        g2.add(TaskRow({"id":"davinci","icon":"user-trash-symbolic","label":"Очистить кэш DaVinci",
            "desc":"Удаляет файлы из CacheClip и ProxyMedia","cmd":[]}, log_fn, lambda: None))

    def _on_postinstall(self, _):
        self._post_btn.set_sensitive(False); self._post_btn.set_label("…")
        self._log("\n▶  PostInstall: удаление конфликтующих библиотек...\n")
        cmd = ["bash", "-c",
            "rm -rf /opt/resolve/libs/libglib-2.0.so* "
            "&& rm -rf /opt/resolve/libs/libgio-2.0.so* "
            "&& rm -rf /opt/resolve/libs/libgmodule-2.0.so*"
        ]
        backend.run_privileged(cmd, self._log, self._post_done)

    def _post_done(self, ok: bool):
        self._post_st.set_label("✅" if ok else "❌")
        self._post_btn.set_label("Повторить" if not ok else "Выполнено")
        self._post_btn.set_sensitive(not ok)
        if ok:
            self._post_btn.remove_css_class("destructive-action")
            self._post_btn.add_css_class("flat")
        self._log("✔  Готово! Запустите DaVinci Resolve.\n" if ok else "✘  Ошибка PostInstall\n")

    def _pick_folder(self, row, key):
        dialog = Gtk.FileDialog(); dialog.set_title("Выберите папку")
        cur = config.state_get(key) or os.path.expanduser("~")
        if os.path.exists(cur): dialog.set_initial_folder(Gio.File.new_for_path(cur))
        widget = self
        while widget.get_parent(): widget = widget.get_parent()
        dialog.select_folder(widget, None, self._on_folder_picked, (row, key))

    def _on_folder_picked(self, dialog, result, user_data):
        row, key = user_data
        try:
            folder = dialog.select_folder_finish(result)
            if folder: path = folder.get_path(); config.state_set(key, path); row.set_subtitle(path); self._log(f"📁 Путь сохранён: {path}\n")
        except: pass

    def _check(self): ok = backend.is_davinci_installed(); GLib.idle_add(self._set_ui, ok)
    def _check_aac(self): ok = backend.is_aac_installed(); GLib.idle_add(self._set_aac_ui, ok)
    
    def _set_ui(self, ok):
        if ok: self._inst_st.set_from_icon_name("object-select-symbolic"); self._inst_st.add_css_class("success"); self._inst_btn.set_label("Установлен"); self._inst_btn.set_sensitive(False); self._inst_btn.remove_css_class("suggested-action"); self._inst_btn.add_css_class("flat")
        else: self._inst_st.clear(); self._inst_st.remove_css_class("success"); self._inst_btn.set_sensitive(True); self._inst_btn.set_label("Установить")

    def _on_install(self, _):
        if backend.is_system_busy(): self._log("\n⚠  Система занята. Попробуйте позже.\n"); return
        self._inst_btn.set_sensitive(False); self._inst_btn.set_label("…"); self._log("\n▶  Установка DaVinci Resolve...\n")
        backend.run_privileged(["epm","play","davinci-resolve"], self._log, lambda ok: GLib.idle_add(self._inst_done, ok))

    def _inst_done(self, ok): self._set_ui(ok); self._log(f"{'✔  DaVinci готов!' if ok else '✘  Ошибка установки'}\n")

    def _set_aac_ui(self, ok):
        if ok: self._aac_st.set_from_icon_name("object-select-symbolic"); self._aac_st.add_css_class("success"); self._aac_btn.set_label("Установлен"); self._aac_btn.set_sensitive(False); self._aac_btn.remove_css_class("suggested-action"); self._aac_btn.add_css_class("flat")
        else: self._aac_st.clear(); self._aac_st.remove_css_class("success"); self._aac_btn.set_sensitive(True); self._aac_btn.set_label("Установить")

    def _on_aac_install(self, _):
        if backend.is_system_busy(): self._log("\n⚠  Система занята. Попробуйте позже.\n"); return
        self._aac_btn.set_sensitive(False); self._aac_btn.set_label("…"); self._log("\n▶  Установка AAC кодека...\n")
        def _worker():
            url = "https://github.com/Toxblh/davinci-linux-aac-codec/releases/latest/download/aac_encoder_plugin-linux-bundle.tar.gz"
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    archive = os.path.join(tmp, "aac.tar.gz"); urllib.request.urlretrieve(url, archive)
                    subprocess.run(["tar", "-xzf", archive, "-C", tmp]); install_sh = None
                    for root, _, files in os.walk(tmp):
                        if "install.sh" in files: install_sh = os.path.join(root, "install.sh"); break
                    if not install_sh: GLib.idle_add(self._aac_fail, "install.sh не найден"); return
                    subprocess.run(["chmod", "+x", install_sh]); backend.run_privileged(["bash", install_sh], self._log, lambda ok: GLib.idle_add(self._set_aac_ui, ok) or GLib.idle_add(self._log, f"{'✔  AAC готов!' if ok else '✘  Ошибка'}\n"))
            except Exception as e: GLib.idle_add(self._aac_fail, str(e))
        threading.Thread(target=_worker, daemon=True).start()

    def _aac_fail(self, m): self._log(f"✘  {m}\n"); self._aac_btn.set_label("Повторить"); self._aac_btn.set_sensitive(True)

    def _build_amd_section(self, parent):
        g = Adw.PreferencesGroup()
        g.set_title("AMD Radeon")
        if hasattr(parent, "add_row"):
            parent.add_row(g)
        else:
            parent.append(g)
        g.set_description("Пакеты для работы DaVinci Resolve с видеокартами AMD")

        r = Adw.ActionRow()
        r.set_title("Поддержка AMD ROCm")
        r.set_subtitle("libGLU  ffmpeg  rocm-opencl-runtime  hip-runtime-amd  clinfo")
        i = Gtk.Image.new_from_icon_name("video-display-symbolic"); i.set_pixel_size(22); r.add_prefix(i)

        self._amd_st = Gtk.Image(); self._amd_st.set_pixel_size(18)
        self._amd_btn = Gtk.Button(label="Установить"); self._amd_btn.set_size_request(130, -1)
        self._amd_btn.add_css_class("suggested-action"); self._amd_btn.add_css_class("pill")
        self._amd_btn.connect("clicked", self._on_amd_install)
        self._amd_btn.set_sensitive(False)

        ar = Gtk.Box(spacing=10); ar.set_valign(Gtk.Align.CENTER)
        ar.append(self._amd_st); ar.append(self._amd_btn)
        r.add_suffix(ar); g.add(r)

        # Из кэша
        if config.state_get("amd_rocm") is True:
            self._set_amd_ui(True)
        else:
            threading.Thread(target=self._check_amd, daemon=True).start()

    def _check_amd(self):
        pkgs = ["libGLU", "ffmpeg", "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"]
        ok = all(subprocess.run(["rpm", "-q", p], capture_output=True).returncode == 0 for p in pkgs)
        config.state_set("amd_rocm", ok)
        GLib.idle_add(self._set_amd_ui, ok)

    def _set_amd_ui(self, ok: bool):
        if ok:
            self._amd_st.set_from_icon_name("object-select-symbolic"); self._amd_st.add_css_class("success"); self._amd_btn.set_label("Установлено")
            self._amd_btn.set_sensitive(False)
            self._amd_btn.remove_css_class("suggested-action"); self._amd_btn.add_css_class("flat")
        else:
            self._amd_st.clear(); self._amd_st.remove_css_class("success"); self._amd_btn.set_sensitive(True)

    def _on_amd_install(self, _):
        self._amd_btn.set_sensitive(False); self._amd_btn.set_label("…")
        self._log("\n▶  Установка пакетов AMD ROCm...\n")
        pkgs = ["libGLU", "ffmpeg", "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"]
        backend.run_privileged(["apt-get", "install", "-y"] + pkgs, self._log, self._amd_done)

    def _amd_done(self, ok: bool):
        config.state_set("amd_rocm", ok)
        self._set_amd_ui(ok)
        self._log(f"{'✔  AMD ROCm установлен!' if ok else '✘  Ошибка установки'}\n")
        if not ok:
            self._amd_btn.set_label("Повторить"); self._amd_btn.set_sensitive(True)

    def _build_fairlight_section(self, parent):
        g = Adw.PreferencesGroup()
        g.set_title("Fairlight Audio")
        if hasattr(parent, "add_row"):
            parent.add_row(g)
        else:
            parent.append(g)

        r = Adw.ActionRow()
        r.set_title("Включить Fairlight")
        r.set_subtitle("epmi alsa-plugins-pulse")

        i = Gtk.Image.new_from_icon_name("audio-speakers-symbolic")
        i.set_pixel_size(22)
        r.add_prefix(i)

        self._fl_st = Gtk.Image()
        self._fl_st.set_pixel_size(18)
        self._fl_btn = Gtk.Button(label="Установить")
        self._fl_btn.set_size_request(130, -1)
        self._fl_btn.add_css_class("suggested-action")
        self._fl_btn.add_css_class("pill")
        self._fl_btn.connect("clicked", self._on_fairlight)
        self._fl_btn.set_sensitive(False)

        fr = Gtk.Box(spacing=10)
        fr.set_valign(Gtk.Align.CENTER)
        fr.append(self._fl_st)
        fr.append(self._fl_btn)
        r.add_suffix(fr)
        g.add(r)
        threading.Thread(target=self._check_fairlight, daemon=True).start()

    def _check_fairlight(self): ok = backend.is_fairlight_installed(); GLib.idle_add(self._set_fl_ui, ok)

    def _set_fl_ui(self, ok):
        if ok: self._fl_st.set_from_icon_name("object-select-symbolic"); self._fl_st.add_css_class("success"); self._fl_btn.set_label("Установлен"); self._fl_btn.set_sensitive(False); self._fl_btn.remove_css_class("suggested-action"); self._fl_btn.add_css_class("flat")
        else: self._fl_st.clear(); self._fl_st.remove_css_class("success"); self._fl_btn.set_sensitive(True); self._fl_btn.set_label("Установить")

    def _on_fairlight(self, _):
        self._fl_btn.set_sensitive(False)
        self._fl_btn.set_label("…")
        self._log("\n▶  Установка Fairlight (alsa-plugins-pulse)...\n")
        backend.run_privileged(["apt-get", "install", "-y", "alsa-plugins-pulse"], self._log, self._fl_done)

    def _fl_done(self, ok):
        self._set_fl_ui(ok); self._log(f"{'✔  Fairlight готов!' if ok else '✘  Ошибка установки'}\n")
        if not ok: self._fl_btn.set_label("Повторить"); self._fl_btn.set_sensitive(True)
        
class MaintenancePage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL); self._log = log_fn; self._rows = []; self._busy = False; sc = Gtk.ScrolledWindow(); sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); sc.set_hexpand(True); sc.set_vexpand(True); self.append(sc)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18); body.set_margin_top(20); body.set_margin_bottom(20); body.set_margin_start(20); body.set_margin_end(20); sc.set_child(body)
        ov = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5); body.append(ov); oh = Gtk.Box(); ol = Gtk.Label(label="Общий прогресс"); ol.set_halign(Gtk.Align.START); ol.add_css_class("caption"); ol.set_hexpand(True)
        self._ovc = Gtk.Label(label=f"0 / {len(config.TASKS)} задач"); self._ovc.add_css_class("caption"); oh.append(ol); oh.append(self._ovc); ov.append(oh); self._ovb = Gtk.ProgressBar(); self._ovb.set_hexpand(True); ov.append(self._ovb)
        self._btn_all = Gtk.Button(label="Запустить все задачи"); self._btn_all.add_css_class("suggested-action"); self._btn_all.add_css_class("pill"); self._btn_all.set_halign(Gtk.Align.CENTER); self._btn_all.connect("clicked", self._run_all); body.append(self._btn_all)
        g = Adw.PreferencesGroup(); g.set_title("Задачи обслуживания"); body.append(g)
        for t in config.TASKS: r = TaskRow(t, self._log, self._upd); self._rows.append(r); g.add(r)

    def set_sensitive_all(self, v):
        self._btn_all.set_sensitive(v)
        for r in self._rows: r._btn.set_sensitive(v)

    def _run_all(self, _):
        if self._busy: return
        self._busy = True; self._btn_all.set_sensitive(False); self._btn_all.set_label("⏳  Выполняется...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        for r in self._rows:
            GLib.idle_add(r.start)
            while r._running or r.result is None: time.sleep(0.2)
        GLib.idle_add(self._done)

    def _done(self): self._busy = False; self._btn_all.set_sensitive(True); self._btn_all.set_label("Запустить все задачи"); self._log("\n✔  Готово!\n")

    def _upd(self):
        done = sum(1 for r in self._rows if r.result is not None); total = len(self._rows)
        self._ovb.set_fraction(done/total if total else 0.0); self._ovc.set_label(f"{done} / {total} задач")

class PlafonWindow(Adw.ApplicationWindow):
    def __init__(self, **kw):
        super().__init__(**kw); self.set_title("ALT Booster")
        settings = self._load_settings()
        self.set_default_size(settings.get("width", 740), settings.get("height", 880))
        self.connect("close-request", self._on_close)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); self.set_content(root)
        header = Adw.HeaderBar()
        self._stack = Adw.ViewStack()
        sw = Adw.ViewSwitcher(); sw.set_stack(self._stack); header.set_title_widget(sw)

        # ── Меню «⋮» ──────────────────────────────────────────────────────
        menu = Gio.Menu()
        menu.append("О приложении", "win.about")
        menu.append("Очистить лог", "win.clear_log")
        menu.append("Сбросить настройки", "win.reset_state")
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        # Регистрируем actions
        for name, cb in [
            ("about",       self._show_about),
            ("clear_log",   self._clear_log),
            ("reset_state", self._reset_state),
        ]:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        root.append(header)
        self._setup = SetupPage(self._log); self._apps = AppsPage(self._log); self._davinci = DaVinciPage(self._log); self._maint = MaintenancePage(self._log)
        for page, name, title, icon in [
            (self._setup, "setup", "Настройки", "preferences-system-symbolic"),
            (self._apps, "apps", "Приложения", "flathub-symbolic"),
            (self._davinci, "davinci", "DaVinci Resolve", "davinci-symbolic"),
            (self._maint, "maintenance", "Обслуживание", "emblem-system-symbolic"),
        ]:
            p = self._stack.add_titled(page, name, title); p.set_icon_name(icon)
        lb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0); lb.set_margin_start(20); lb.set_margin_end(20); lb.set_margin_bottom(16); lb.set_vexpand(True)
        ll = Gtk.Label(label="Лог выполнения"); ll.set_halign(Gtk.Align.START); ll.add_css_class("heading"); lb.append(ll)
        lf = Gtk.Frame(); lf.add_css_class("card"); lf.set_margin_top(6); lf.set_vexpand(True); lb.append(lf)
        ls = Gtk.ScrolledWindow(); ls.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC); ls.set_vexpand(True); lf.set_child(ls)
        self._tv = Gtk.TextView(); self._tv.set_editable(False); self._tv.set_cursor_visible(False); self._tv.set_monospace(True); self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR); self._tv.set_margin_start(10); self._tv.set_margin_end(10); self._tv.set_margin_top(8); self._tv.set_margin_bottom(8); self._buf = self._tv.get_buffer(); ls.set_child(self._tv)
        self._paned = Gtk.Paned.new(Gtk.Orientation.VERTICAL); self._paned.set_start_child(self._stack); self._paned.set_end_child(lb); self._paned.set_vexpand(True)
        self._paned.set_position(settings.get("paned_pos", 720)); root.append(self._paned)

    def ask_password(self): self._maint.set_sensitive_all(False); PasswordDialog(self, self._auth_ok, self.close)
    def _auth_ok(self): self._maint.set_sensitive_all(True); self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")
    def _load_settings(self):
        try:
            with open(config.CONFIG_FILE) as f: return json.load(f)
        except: return {}
    def _on_close(self, _):
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE,"w") as f: json.dump({"width":self.get_width(),"height":self.get_height(),"paned_pos":self._paned.get_position()},f)
        except: pass
        return False
    def _show_about(self, *_):
        dialog = Adw.AboutDialog()
        dialog.set_application_name("ALT Booster")
        dialog.set_application_icon("altbooster")
        dialog.set_developer_name("PLAFON")
        dialog.set_version("2.0")
        dialog.set_website("https://github.com/plafonlinux/altbooster")
        dialog.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        dialog.set_comments("Утилита обслуживания и настройки системы ALT Linux.\nGTK4 / Adwaita / Python 3")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_developers(["PLAFON"])
        dialog.set_copyright("© 2026 PLAFON")
        # Ссылки под иконкой
        dialog.add_link("📖 ALT Zero — документация", "https://plafon.gitbook.io/alt-zero")
        dialog.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        dialog.present(self)

    def _clear_log(self, *_):
        self._buf.set_text("")

    def _reset_state(self, *_):
        dialog = Adw.AlertDialog(
            heading="Сбросить настройки?",
            body="Все сохранённые статусы будут удалены. Утилита повторно проверит состояние системы при следующем запуске."
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reset", "Сбросить")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def _do_reset(d, response):
            if response == "reset":
                import config as _cfg
                _cfg._state.clear()
                _cfg.save_state()
                self._log("🔄 Настройки сброшены. Перезапустите приложение.\n")
        dialog.connect("response", _do_reset)
        dialog.present(self)

    def _log(self, text):
        end = self._buf.get_end_iter(); self._buf.insert(end, text); self._tv.scroll_mark_onscreen(self._buf.create_mark(None, self._buf.get_end_iter(), False))
