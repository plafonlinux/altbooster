
import os
import subprocess
import tempfile
import threading
import urllib.request
import zipfile

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import backend
import config
from widgets import (
    make_icon, make_button, make_status_icon,
    set_status_ok, set_status_error, clear_status, make_suffix_box, make_scrolled_page,
)
from ui.rows import TaskRow

_AAC_URL = "https://github.com/Toxblh/davinci-linux-aac-codec/releases/latest/download/aac_encoder_plugin-linux-bundle.tar.gz"
_POSTINSTALL_CMD = [
    "bash", "-c",
    "rm -rf /opt/resolve/libs/libglib-2.0.so* && "
    "rm -rf /opt/resolve/libs/libgio-2.0.so* && "
    "rm -rf /opt/resolve/libs/libgmodule-2.0.so*",
]
_ROCM_PKGS = ["apt-get", "install", "-y", "libGLU", "ffmpeg",
              "rocm-opencl-runtime", "hip-runtime-amd", "clinfo"]
def _build_fairlight_cmd() -> list:
    user_home = os.path.expanduser("~")
    asound_content = (
        "pcm.!default {\\n"
        "    type pulse\\n"
        "}\\n"
        "ctl.!default pulse\\n"
    )
    return [
        "bash", "-c",
        f"apt-get install -y alsa-plugins-pulse && "
        f"printf '{asound_content}' > /etc/asound.conf && "
        f"printf '{asound_content}' > '{user_home}/.asoundrc'",
    ]


class DaVinciPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        overlay = Gtk.Overlay()
        self.append(overlay)

        scroll, body = make_scrolled_page()
        overlay.set_child(scroll)

        self._build_install_group(body)
        self._build_setup_group(body)
        self._build_cache_group(body)

        dr_btn = make_button("DaVinci Ready", style="suggested-action")
        dr_btn.add_css_class("pill")
        dr_btn.set_halign(Gtk.Align.END)
        dr_btn.set_valign(Gtk.Align.END)
        dr_btn.set_margin_end(12)
        dr_btn.set_margin_bottom(12)

        dr_btn.connect("clicked", self.run_ready_preset)
        overlay.add_overlay(dr_btn)


    def _build_install_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Установка")
        body.append(group)

        self._dv_file_row = Adw.ActionRow()
        self._dv_file_row.set_title("Официальный установщик")
        self._dv_file_row.set_subtitle("Выберите .zip или .run скачанный с blackmagicdesign.com")
        self._dv_file_row.add_prefix(make_icon("folder-download-symbolic"))
        self._dv_installer_path = None

        site_btn = Gtk.Button(label="Сайт")
        site_btn.add_css_class("flat")
        site_btn.set_valign(Gtk.Align.CENTER)
        site_btn.set_tooltip_text("Открыть страницу загрузки на blackmagicdesign.com")
        site_btn.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(
            "https://www.blackmagicdesign.com/ru/products/davinciresolve", None))

        pick_btn = Gtk.Button(label="Выбрать файл")
        pick_btn.add_css_class("flat")
        pick_btn.set_valign(Gtk.Align.CENTER)
        pick_btn.connect("clicked", self._on_pick_dv_installer)

        self._dv_file_st = make_status_icon()
        self._dv_inst_from_file_btn = make_button("Установить")
        self._dv_inst_from_file_btn.set_sensitive(False)
        self._dv_inst_from_file_btn.connect("clicked", self._on_install_from_file)

        suffix = Gtk.Box(spacing=6)
        suffix.set_valign(Gtk.Align.CENTER)
        for w in [site_btn, pick_btn, self._dv_file_st, self._dv_inst_from_file_btn]:
            suffix.append(w)
        self._dv_file_row.add_suffix(suffix)
        group.add(self._dv_file_row)

        threading.Thread(
            target=lambda: GLib.idle_add(self._set_install_ui, backend.is_davinci_installed()),
            daemon=True,
        ).start()


    def _build_setup_group(self, body):
        pg = Adw.PreferencesGroup()
        pg.set_title("PostInstall")
        pg.set_description("Выполните после установки DaVinci Resolve")
        body.append(pg)
        r = Adw.ActionRow()
        r.set_title("Удалить конфликтующие библиотеки")
        r.set_subtitle("libglib/libgio/libgmodule из /opt/resolve/libs")
        r.add_prefix(make_icon("emblem-important-symbolic"))
        self._post_st = make_status_icon()
        self._post_btn = make_button("Выполнить", style="destructive-action")
        self._post_btn.connect("clicked", self._on_postinstall)
        r.add_suffix(make_suffix_box(self._post_st, self._post_btn))
        pg.add(r)

        ag = Adw.PreferencesGroup()
        ag.set_title("AMD Radeon")
        ag.set_description("Пакеты для работы с видеокартами AMD")
        body.append(ag)
        r2 = Adw.ActionRow()
        r2.set_title("Поддержка AMD ROCm")
        r2.set_subtitle("libGLU  ffmpeg  rocm-opencl-runtime  hip-runtime-amd  clinfo")
        r2.add_prefix(make_icon("video-display-symbolic"))
        self._amd_st = make_status_icon()
        self._amd_btn = make_button("Установить")
        self._amd_btn.connect("clicked", self._on_amd_install)
        self._amd_btn.set_sensitive(False)
        r2.add_suffix(make_suffix_box(self._amd_st, self._amd_btn))
        ag.add(r2)
        if config.state_get("amd_rocm") is True:
            self._set_amd_ui(True)
        else:
            threading.Thread(
                target=lambda: GLib.idle_add(
                    self._set_amd_ui,
                    subprocess.run(["rpm", "-q", "rocm-opencl-runtime"], capture_output=True).returncode == 0,
                ),
                daemon=True,
            ).start()

        acg = Adw.PreferencesGroup()
        acg.set_title("AAC Audio кодек")
        body.append(acg)
        r3 = Adw.ActionRow()
        r3.set_title("FFmpeg AAC Encoder Plugin")
        r3.set_subtitle("Плагин для экспорта AAC аудио")
        r3.add_prefix(make_icon("audio-x-generic-symbolic"))
        self._aac_st = make_status_icon()
        self._aac_btn = make_button("Установить")
        self._aac_btn.connect("clicked", self._on_aac_install)
        self._aac_btn.set_sensitive(False)
        r3.add_suffix(make_suffix_box(self._aac_st, self._aac_btn))
        acg.add(r3)
        threading.Thread(
            target=lambda: GLib.idle_add(self._set_aac_ui, backend.is_aac_installed()),
            daemon=True,
        ).start()

        flg = Adw.PreferencesGroup()
        flg.set_title("Fairlight Audio")
        body.append(flg)
        r4 = Adw.ActionRow()
        r4.set_title("Включить Fairlight")
        r4.set_subtitle("alsa-plugins-pulse + /etc/asound.conf → pcm.!default pulse")
        r4.add_prefix(make_icon("audio-speakers-symbolic"))
        self._fl_st = make_status_icon()
        self._fl_btn = make_button("Установить")
        self._fl_btn.connect("clicked", self._on_fairlight)
        self._fl_btn.set_sensitive(False)
        info_btn = Gtk.Button()
        info_btn.set_icon_name("dialog-information-symbolic")
        info_btn.add_css_class("flat")
        info_btn.set_valign(Gtk.Align.CENTER)
        info_btn.set_tooltip_text("Что делать после установки")
        info_btn.connect("clicked", self._on_fairlight_info)
        r4.add_suffix(make_suffix_box(info_btn, self._fl_st, self._fl_btn))
        flg.add(r4)
        threading.Thread(
            target=lambda: GLib.idle_add(self._set_fl_ui, backend.is_fairlight_installed()),
            daemon=True,
        ).start()


    def _build_cache_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Кэш")
        group.set_description("Укажите папки кэша и запустите очистку при необходимости")
        body.append(group)
        self._cache_row = self._make_folder_row("CacheClip", config.get_dv_cache(), "dv_cache_path")
        self._proxy_row = self._make_folder_row("ProxyMedia", config.get_dv_proxy(), "dv_proxy_path")
        group.add(self._cache_row)
        group.add(self._proxy_row)
        group.add(TaskRow(
            {"id": "davinci", "icon": "user-trash-symbolic",
             "label": "Очистить кэш DaVinci", "desc": "Удаляет файлы из CacheClip и ProxyMedia",
             "cmd": []},
            self._log, lambda: None,
        ))

    def _make_folder_row(self, title, path, state_key):
        _descriptions = {
            "dv_cache_path": "Папка для временных файлов кэша рендеринга",
            "dv_proxy_path": "Папка для прокси-медиафайлов",
        }
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(path if path else _descriptions.get(state_key, "Не задано"))
        row.add_prefix(make_icon("folder-symbolic"))
        btn = Gtk.Button(label="Выбрать")
        btn.add_css_class("flat")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _, r=row, k=state_key: self._pick_folder(r, k))
        row.add_suffix(btn)
        return row

    def _pick_folder(self, row, key):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите папку")
        cur = config.state_get(key) or os.path.expanduser("~")
        if os.path.exists(cur):
            dialog.set_initial_folder(Gio.File.new_for_path(cur))
        w = self
        while w.get_parent():
            w = w.get_parent()
        dialog.select_folder(w, None, lambda d, r: self._folder_picked(d, r, row, key))

    def _folder_picked(self, dialog, result, row, key):
        try:
            f = dialog.select_folder_finish(result)
            if f:
                path = f.get_path()
                config.state_set(key, path)
                row.set_subtitle(path)
                self._log(f"📁 {path}\n")
        except Exception:
            pass


    def _set_install_ui(self, ok):
        if ok:
            set_status_ok(self._dv_file_st)
        else:
            clear_status(self._dv_file_st)

    def _set_amd_ui(self, ok):
        if ok:
            set_status_ok(self._amd_st)
            self._amd_btn.set_label("Установлено")
            self._amd_btn.set_sensitive(False)
            self._amd_btn.remove_css_class("suggested-action")
            self._amd_btn.add_css_class("flat")
        else:
            clear_status(self._amd_st)
            self._amd_btn.set_sensitive(True)
            self._amd_btn.set_label("Установить")

    def _set_aac_ui(self, ok):
        if ok:
            set_status_ok(self._aac_st)
            self._aac_btn.set_label("Установлен")
            self._aac_btn.set_sensitive(False)
            self._aac_btn.remove_css_class("suggested-action")
            self._aac_btn.add_css_class("flat")
        else:
            clear_status(self._aac_st)
            self._aac_btn.set_sensitive(True)
            self._aac_btn.set_label("Установить")

    def _set_fl_ui(self, ok):
        self._fl_btn.set_sensitive(True)
        if ok:
            set_status_ok(self._fl_st)
            self._fl_btn.set_label("Применить снова")
            self._fl_btn.remove_css_class("suggested-action")
            self._fl_btn.remove_css_class("flat")
            self._fl_btn.add_css_class("destructive-action")
            self._fl_btn.set_opacity(0.6)
        else:
            clear_status(self._fl_st)
            self._fl_btn.set_label("Установить")
            self._fl_btn.remove_css_class("flat")
            self._fl_btn.remove_css_class("destructive-action")
            self._fl_btn.set_opacity(1.0)


    def _on_pick_dv_installer(self, _):
        dialog = Gtk.FileDialog()
        dialog.set_title("Выберите установщик DaVinci Resolve (.zip или .run)")
        f = Gtk.FileFilter()
        f.set_name("Установщик DaVinci Resolve (*.zip, *.run)")
        f.add_pattern("*.zip")
        f.add_pattern("*.run")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dialog.set_filters(store)
        w = self
        while w.get_parent():
            w = w.get_parent()
        dialog.open(w, None, self._on_installer_file_picked)

    def _on_installer_file_picked(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                self._dv_installer_path = f.get_path()
                name = os.path.basename(self._dv_installer_path)
                self._dv_file_row.set_subtitle(f"Выбран: {name}")
                self._dv_inst_from_file_btn.set_sensitive(True)
                clear_status(self._dv_file_st)
        except Exception:
            pass

    def _on_install_from_file(self, _):
        if not self._dv_installer_path:
            return
        self._dv_inst_from_file_btn.set_sensitive(False)
        self._dv_inst_from_file_btn.set_label("…")
        clear_status(self._dv_file_st)
        name = os.path.basename(self._dv_installer_path)
        self._log(f"\n▶  Установка DaVinci Resolve из {name}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Установка DaVinci Resolve...")
        threading.Thread(target=self._do_install_from_file, daemon=True).start()

    def _do_install_from_file(self):
        path = self._dv_installer_path
        ext = os.path.splitext(path)[1].lower()
        ok = False
        try:
            if ext == ".zip":
                GLib.idle_add(self._log, "▶  Распаковка архива...\n")
                with tempfile.TemporaryDirectory() as tmp:
                    with zipfile.ZipFile(path) as zf:
                        zf.extractall(tmp)
                    run_files = [
                        os.path.join(root, fname)
                        for root, _, files in os.walk(tmp)
                        for fname in files if fname.endswith(".run")
                    ]
                    if not run_files:
                        GLib.idle_add(self._log, "✘  .run файл не найден в архиве\n")
                        GLib.idle_add(self._dv_inst_from_file_btn.set_label, "Установить")
                        GLib.idle_add(self._dv_inst_from_file_btn.set_sensitive, True)
                        return
                    run_path = run_files[0]
                    GLib.idle_add(self._log, f"▶  Запуск {os.path.basename(run_path)} -i ...\n")
                    os.chmod(run_path, 0o755)
                    ok = backend.run_privileged_sync([run_path, "-i"], self._log)
            elif ext == ".run":
                os.chmod(path, 0o755)
                GLib.idle_add(self._log, f"▶  Запуск {os.path.basename(path)} -i ...\n")
                ok = backend.run_privileged_sync([path, "-i"], self._log)
            else:
                GLib.idle_add(self._log, "✘  Поддерживаются только .zip и .run\n")
        except Exception as e:
            GLib.idle_add(self._log, f"✘  Ошибка: {e}\n")

        def _done():
            if ok:
                set_status_ok(self._dv_file_st)
                self._dv_inst_from_file_btn.set_label("Установить")
                self._dv_inst_from_file_btn.set_sensitive(True)
                self._log("✔  DaVinci Resolve установлен!\n")
            else:
                set_status_error(self._dv_file_st)
                self._dv_inst_from_file_btn.set_label("Повторить")
                self._dv_inst_from_file_btn.set_sensitive(True)
                self._log("✘  Ошибка установки. Проверьте лог.\n")
            win = self.get_root()
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        GLib.idle_add(_done)

    def _on_postinstall(self, _):
        self._post_btn.set_sensitive(False)
        self._post_btn.set_label("…")
        self._log("\n▶  PostInstall...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("PostInstall...")
        backend.run_privileged(_POSTINSTALL_CMD, self._log, self._post_done)

    def _post_done(self, ok):
        if ok:
            set_status_ok(self._post_st)
            self._post_btn.set_label("Выполнено")
            self._post_btn.set_sensitive(False)
            self._post_btn.remove_css_class("destructive-action")
            self._post_btn.add_css_class("flat")
            self._log("\n✔  Готово!\n")
        else:
            set_status_error(self._post_st)
            self._post_btn.set_label("Повторить")
            self._post_btn.set_sensitive(True)
            self._log("\n✘  Ошибка PostInstall\n")
        win = self.get_root()
        if hasattr(win, "stop_progress"): win.stop_progress(ok)
        self._reset_btn_later(self._post_btn, "Выполнить")

    def _on_amd_install(self, _):
        self._amd_btn.set_sensitive(False)
        self._amd_btn.set_label("…")
        self._log("\n▶  Установка AMD ROCm...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка AMD ROCm...")
        backend.run_privileged(
            _ROCM_PKGS, self._log,
            lambda ok: (
                config.state_set("amd_rocm", ok),
                self._set_amd_ui(ok),
                self._log("✔  AMD ROCm!\n" if ok else "✘  Ошибка\n"),
                win.stop_progress(ok) if hasattr(win, "stop_progress") else None
            ),
        )

    def _on_aac_install(self, _):
        self._aac_btn.set_sensitive(False)
        self._aac_btn.set_label("…")
        self._log("\n▶  Установка AAC кодека...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка AAC кодека...")

        def _worker():
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    arch = os.path.join(tmp, "aac.tar.gz")
                    urllib.request.urlretrieve(_AAC_URL, arch)
                    backend.install_aac_codec(
                        arch, self._log,
                        lambda ok: (
                            GLib.idle_add(self._set_aac_ui, ok),
                            GLib.idle_add(win.stop_progress, ok) if hasattr(win, "stop_progress") else None
                        )
                    )
            except Exception as e:
                GLib.idle_add(self._log, f"✘  {e}\n")
                GLib.idle_add(self._aac_btn.set_label, "Повторить")
                GLib.idle_add(self._aac_btn.set_sensitive, True)
                if hasattr(win, "stop_progress"): GLib.idle_add(win.stop_progress, False)

        threading.Thread(target=_worker, daemon=True).start()

    _FAIRLIGHT_HINT = (
        "После перезапуска DaVinci Resolve выполните два шага:\n\n"
        "1. Включите авто-патчинг входов:\n"
        "   Preferences → User → Fairlight →\n"
        "   Enable Auto Patching\n\n"
        "2. Укажите папку для записи:\n"
        "   File → Project Settings →\n"
        "   Master Settings → Capture and Playback"
    )

    def _on_fairlight_info(self, _):
        dialog = Adw.AlertDialog(
            heading="Настройка Fairlight",
            body=self._FAIRLIGHT_HINT,
        )
        dialog.add_response("ok", "Понятно")
        dialog.set_default_response("ok")
        dialog.present(self.get_root())

    def _on_fairlight(self, _):
        self._fl_btn.set_sensitive(False)
        self._fl_btn.set_label("…")
        self._log("\n▶  Fairlight...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка Fairlight...")

        def _done(ok):
            self._set_fl_ui(ok)
            if ok:
                self._log("✔  Готово! Перезапустите DaVinci Resolve.\n")
                self._on_fairlight_info(None)
            else:
                self._log("✘  Ошибка\n")
            if hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(_build_fairlight_cmd(), self._log, _done)


    def run_ready_preset(self, btn):
        amd_ok = subprocess.run(["rpm", "-q", "rocm-opencl-runtime"],
                                 capture_output=True).returncode == 0
        aac_ok = backend.is_aac_installed()
        fl_ok  = backend.is_fairlight_installed()

        lines = ["1. PostInstall — удаление конфликтующих библиотек glib/gio/gmodule"]
        if not amd_ok:
            lines.append("2. AMD ROCm — libGLU, ffmpeg, rocm-opencl-runtime")
        if not aac_ok:
            lines.append("3. AAC кодек — плагин для экспорта AAC аудио")
        if not fl_ok:
            lines.append("4. Fairlight — alsa-plugins-pulse")

        suffix = (
            "\n\nВсе дополнительные компоненты уже установлены."
            if (amd_ok and aac_ok and fl_ok) else ""
        )

        dialog = Adw.AlertDialog(
            heading="DaVinci Resolve Ready",
            body="\n".join(lines) + suffix,
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("run", "Выполнить")
        dialog.set_response_appearance("run", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("run")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, r: self._run_ready_preset_exec(btn) if r == "run" else None)
        dialog.present(self.get_root())

    def _run_ready_preset_exec(self, btn):
        btn.set_sensitive(False)
        btn.set_label("⏳ Выполняется...")
        self._log("\n▶  DaVinci Resolve Ready...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("DaVinci Resolve Ready...")

        steps = [
            ("PostInstall", _POSTINSTALL_CMD, "privileged", None),
            ("AMD ROCm", _ROCM_PKGS, "privileged",
             lambda: subprocess.run(["rpm", "-q", "rocm-opencl-runtime"], capture_output=True).returncode == 0),
            ("Fairlight", _build_fairlight_cmd(), "privileged",
             backend.is_fairlight_installed),
            ("AAC", None, "aac", backend.is_aac_installed),
        ]

        def _worker():
            all_ok = True
            for i, (name, cmd, kind, check_fn) in enumerate(steps, 1):
                if check_fn and check_fn():
                    GLib.idle_add(self._log, f"✔  {name} уже установлен.\n")
                    continue
                GLib.idle_add(self._log, f"\n▶  [{i}/{len(steps)}] {name}...\n")
                if kind == "aac":
                    ok = self._install_aac_sync()
                else:
                    ok = backend.run_privileged_sync(cmd, self._log)
                if not ok:
                    all_ok = False
                    break

            def _finish():
                btn.set_label("✔ Готово" if all_ok else "✘ Ошибка")
                btn.set_sensitive(True)
                if all_ok:
                    set_status_ok(self._post_st)
                    self._post_btn.set_label("Выполнено")
                    self._post_btn.set_sensitive(False)
                    self._post_btn.remove_css_class("destructive-action")
                    self._post_btn.add_css_class("flat")

                threading.Thread(
                    target=lambda: GLib.idle_add(
                        self._set_amd_ui,
                        subprocess.run(["rpm", "-q", "rocm-opencl-runtime"], capture_output=True).returncode == 0,
                    ),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=lambda: GLib.idle_add(self._set_fl_ui, backend.is_fairlight_installed()),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=lambda: GLib.idle_add(self._set_aac_ui, backend.is_aac_installed()),
                    daemon=True,
                ).start()
                self._reset_btn_later(btn, "DaVinci Ready")
                if hasattr(win, "stop_progress"): win.stop_progress(all_ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_worker, daemon=True).start()

    def _install_aac_sync(self) -> bool:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                arch = os.path.join(tmp, "aac.tar.gz")
                urllib.request.urlretrieve(_AAC_URL, arch)
                return backend.run_privileged_sync(
                    ["bash", "-c",
                     f"tar xzf '{arch}' -C /tmp && "
                     "cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"],
                    self._log,
                )
        except Exception as e:
            GLib.idle_add(self._log, f"✘  {e}\n")
            return False


    @staticmethod
    def _reset_btn_later(btn, label, delay=3000):
        def _reset():
            btn.set_sensitive(True)
            btn.set_label(label)
            return False
        GLib.timeout_add(delay, _reset)

