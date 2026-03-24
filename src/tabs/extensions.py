
from __future__ import annotations

import json
import os
from typing import Callable
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from core.config import CONFIG_DIR

_SHELLVER_BACKUP = CONFIG_DIR / "ext_shellver_backup.json"
from ui.widgets import (
    make_button, make_scrolled_page, make_icon,
    make_status_icon, set_status_ok, set_status_error, clear_status, make_suffix_box,
    scroll_child_into_view,
)


RECOMMENDED = [
    (
        "appindicatorsupport@rgcjonas.gmail.com",
        "AppIndicator and KStatusNotifierItem",
        "Добавляет поддержку системного лотка для старых приложений.",
        "615",
    ),
    (
        "Vitals@CoreCoding.com",
        "Vitals",
        "Системный монитор (CPU, RAM, сеть) в верхней панели.",
        "1460",
    ),
    (
        "just-perfection-desktop@just-perfection",
        "Just Perfection",
        "Тонкая настройка множества элементов интерфейса GNOME.",
        "3843",
    ),
    (
        "dash-to-dock@micxgx.gmail.com",
        "Dash to Dock",
        "Превращает панель GNOME в док-станцию в стиле macOS.",
        "307",
    ),
    (
        "dash-to-panel@jderose9.github.com",
        "Dash to Panel",
        "Создаёт единую панель задач в стиле Windows/KDE.",
        "1160",
    ),
    (
        "blur-my-shell@aunetx",
        "Blur my Shell",
        "Эффект размытия для обзора, панели и других элементов.",
        "3193",
    ),
    (
        "pigeon@subz69.github",
        "Pigeon Email Notifier",
        "Уведомления о новых письмах для почтовых ящиков IMAP.",
        "9301",
    ),
    (
        "auto-accent-colour@Wartybix",
        "Auto Accent Colour",
        "Автоматически подбирает цвет акцента под обои рабочего стола.",
        "7502",
    ),
    (
        "rounded-window-corners@fxgn",
        "Rounded Window Corners Reborn",
        "Скругляет углы окон приложений.",
        "7048",
    ),
    (
        "ding@rastersoft.com",
        "Desktop Icons NG (DING)",
        "Добавляет иконки на рабочий стол.",
        "2087",
    ),
    (
        "no-overview@fthx",
        "No Overview at Startup",
        "Отключает обзор при запуске сеанса.",
        "4099",
    ),
    (
        "status-tray@keithvassallo.com",
        "Status Tray",
        "Позволяет группировать и скрывать значки в системном лотке.",
        "9164",
    ),
    (
        "right-click-next@derVedro",
        "Right Click Next",
        "Добавляет кнопки «Следующий трек» и «Предыдущий» в контекстное меню медиаплеера.",
        "7600",
    ),
]

_USER_EXT_DIR   = Path.home() / ".local" / "share" / "gnome-shell" / "extensions"
_SYSTEM_EXT_DIR = Path("/usr/share/gnome-shell/extensions")


def _gext_path() -> str | None:
    if cmd := shutil.which("gext"):
        return cmd
    local_bin = Path.home() / ".local" / "bin" / "gext"
    if local_bin.exists():
        return str(local_bin)
    return None


def _fix_float_versions_in_metadata(log_fn: Callable[[str], None] | None = None) -> tuple[list[str], list[str]]:
    fixed = []
    broken_system = []

    for meta_path in _SYSTEM_EXT_DIR.glob("*/metadata.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            orig_ver = data.get("version")
            if isinstance(orig_ver, float):
                data["version"] = str(orig_ver)
                new_text = json.dumps(data, ensure_ascii=False, indent=2)
                ok = False
                try:
                    fd, tmp = tempfile.mkstemp(suffix=".json")
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(new_text)
                    ok = backend.run_privileged_sync(["cp", tmp, str(meta_path)], lambda _: None)
                except Exception:
                    pass
                finally:
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
                if ok:
                    if log_fn:
                        log_fn(f"✔  Исправлена float-версия в системном расширении {meta_path.parent.name}\n")
                else:
                    broken_system.append(meta_path.parent.name)
                    if log_fn:
                        log_fn(
                            f"⚠  Системное расширение {meta_path.parent.name} имеет версию float ({orig_ver}). "
                            f"Исправить без прав не удалось — будет использован нативный метод установки.\n"
                        )
        except Exception:
            pass

    for meta_path in _USER_EXT_DIR.glob("*/metadata.json"):
        try:
            text = meta_path.read_text(encoding="utf-8")
            data = json.loads(text)
            ver = data.get("version")
            if isinstance(ver, float):
                data["version"] = str(ver)
                meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                fixed.append(meta_path.parent.name)
        except Exception:
            pass
    return fixed, broken_system


def _patch_shell_versions(gnome_ver: str, log_fn=None) -> tuple[list[str], list[str]]:
    patched = []
    failed_system = []
    backup: dict[str, list] = {}

    try:
        existing_backup = json.loads(_SHELLVER_BACKUP.read_text(encoding="utf-8"))
        backup.update(existing_backup)
    except Exception:
        pass

    for meta_path in _USER_EXT_DIR.glob("*/metadata.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            versions = data.get("shell-version", [])
            if gnome_ver not in versions:
                uuid = meta_path.parent.name
                if uuid not in backup:
                    backup[uuid] = list(versions)
                versions.append(gnome_ver)
                data["shell-version"] = versions
                meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                patched.append(uuid)
                if log_fn:
                    log_fn(f"✔  {uuid}: добавлена версия {gnome_ver}\n")
        except Exception:
            pass

    for meta_path in _SYSTEM_EXT_DIR.glob("*/metadata.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            versions = data.get("shell-version", [])
            if gnome_ver not in versions:
                uuid = meta_path.parent.name
                if uuid not in backup:
                    backup[uuid] = list(versions)
                versions.append(gnome_ver)
                new_text = json.dumps(data, ensure_ascii=False, indent=2)
                ok = False
                try:
                    fd, tmp = tempfile.mkstemp(suffix=".json")
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(new_text)
                    ok = backend.run_privileged_sync(["cp", tmp, str(meta_path)], lambda _: None)
                except Exception:
                    pass
                finally:
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
                if ok:
                    patched.append(uuid)
                    if log_fn:
                        log_fn(f"✔  (сист.) {uuid}: добавлена версия {gnome_ver}\n")
                else:
                    failed_system.append(uuid)
                    if log_fn:
                        log_fn(f"⚠  (сист.) {uuid}: нет прав для записи\n")
        except Exception:
            pass

    if patched:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _SHELLVER_BACKUP.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return patched, failed_system


def _revert_shell_versions(log_fn=None) -> int:
    try:
        backup = json.loads(_SHELLVER_BACKUP.read_text(encoding="utf-8"))
    except Exception:
        if log_fn:
            log_fn("⚠  Файл бэкапа не найден — нечего откатывать\n")
        return 0

    reverted = 0
    for uuid, orig_versions in backup.items():
        for ext_dir in (_USER_EXT_DIR, _SYSTEM_EXT_DIR):
            meta_path = ext_dir / uuid / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                data["shell-version"] = orig_versions
                new_text = json.dumps(data, ensure_ascii=False, indent=2)
                if ext_dir == _USER_EXT_DIR:
                    meta_path.write_text(new_text, encoding="utf-8")
                    reverted += 1
                    if log_fn:
                        log_fn(f"✔  {uuid}: восстановлен оригинальный shell-version\n")
                else:
                    ok = False
                    try:
                        fd, tmp = tempfile.mkstemp(suffix=".json")
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(new_text)
                        ok = backend.run_privileged_sync(["cp", tmp, str(meta_path)], lambda _: None)
                    except Exception:
                        pass
                    finally:
                        try:
                            os.unlink(tmp)
                        except Exception:
                            pass
                    if ok:
                        reverted += 1
                        if log_fn:
                            log_fn(f"✔  (сист.) {uuid}: восстановлен оригинальный shell-version\n")
                    else:
                        if log_fn:
                            log_fn(f"⚠  (сист.) {uuid}: нет прав для записи\n")
            except Exception:
                pass

    try:
        _SHELLVER_BACKUP.unlink()
    except Exception:
        pass

    return reverted


def _is_ext_installed(uuid: str) -> bool:
    try:
        r = subprocess.run(["gnome-extensions", "list"], capture_output=True, text=True)
        return uuid in r.stdout
    except Exception:
        return False


def _read_extensions_from(ext_dir: Path) -> list[tuple[str, str, str]]:
    result = []
    for meta in sorted(ext_dir.glob("*/metadata.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            uuid = data.get("uuid", meta.parent.name)
            name = data.get("name", uuid)
            desc = data.get("description", "")
            result.append((uuid, name, desc))
        except Exception:
            pass
    return result


def _get_enabled_uuids() -> set[str]:
    try:
        r = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            capture_output=True, text=True,
        )
        return set(r.stdout.split())
    except Exception:
        return set()


def _detect_install_error(stdout: str, stderr: str) -> str:
    combined = "\n".join([stdout or "", stderr or ""]).strip()
    if not combined:
        return ""
    patterns = (
        r"cannot find extension",
        r"not found",
        r"no extension",
        r"error",
        r"failed",
    )
    lowered = combined.lower()
    if any(re.search(p, lowered) for p in patterns):
        # Популярный кейс gext: "Cannot find extension 12345"
        m = re.search(r"cannot find extension\s+([^\s]+)", lowered)
        if m:
            return f"Расширение с ID {m.group(1)} не найдено"
        if "not found" in lowered or "no extension" in lowered:
            return "Расширение не найдено"
        if "failed" in lowered:
            return "Сбой установки расширения"
        if "error" in lowered:
            return "Ошибка установки расширения"
        return "Не удалось установить расширение"
    return ""


def _make_info_button(desc: str) -> Gtk.Button:
    info_btn = Gtk.Button()
    info_btn.set_icon_name("dialog-information-symbolic")
    info_btn.add_css_class("flat")
    info_btn.add_css_class("circular")
    info_btn.set_valign(Gtk.Align.CENTER)
    info_btn.set_tooltip_text(desc)
    info_btn.set_sensitive(bool(desc))
    return info_btn


class ExtensionsPage(Gtk.Box):

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._ext_row_by_uuid: dict[str, Adw.ActionRow] = {}
        self._search_busy = False

        scroll, self._body = make_scrolled_page()
        self._scroll = scroll
        self.append(scroll)

        self._build_search_group()
        self._build_compat_group()
        self._search_results_group = None
        self._installed_group = None
        self._build_installed_group()


    def _get_shell_version(self) -> str:
        try:
            r = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True)
            m = re.search(r"(\d+)", r.stdout)
            return m.group(1) if m else "47"
        except Exception:
            return "47"

    def _count_incompatible(self, gnome_ver: str) -> int:
        count = 0
        for ext_dir in (_USER_EXT_DIR, _SYSTEM_EXT_DIR):
            for meta_path in ext_dir.glob("*/metadata.json"):
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    if gnome_ver not in data.get("shell-version", []):
                        count += 1
                except Exception:
                    pass
        return count

    def _build_compat_group(self):
        gnome_ver = self._get_shell_version()
        has_backup = _SHELLVER_BACKUP.exists()

        self._compat_group = Adw.PreferencesGroup()
        self._compat_group.set_title("Совместимость")
        self._compat_group.set_visible(has_backup)
        self._body.append(self._compat_group)

        self._compat_row = Adw.ActionRow()
        self._compat_row.set_title(f"Расширения и GNOME {gnome_ver}")

        self._compat_patch_btn = make_button("Применить")
        self._compat_patch_btn.set_valign(Gtk.Align.CENTER)
        self._compat_patch_btn.set_sensitive(False)
        self._compat_patch_btn.connect("clicked", self._on_patch_compat)

        self._compat_revert_btn = make_button("Откатить")
        self._compat_revert_btn.set_valign(Gtk.Align.CENTER)
        self._compat_revert_btn.add_css_class("destructive-action")
        self._compat_revert_btn.set_sensitive(has_backup)
        self._compat_revert_btn.connect("clicked", self._on_revert_compat)

        self._compat_row.add_suffix(make_suffix_box(self._compat_revert_btn, self._compat_patch_btn))
        self._compat_group.add(self._compat_row)

        if has_backup:
            self._compat_row.set_subtitle("Исправления применены — доступен откат")
            return

        def _check():
            count = self._count_incompatible(gnome_ver)

            def _update():
                if count == 0:
                    self._compat_group.set_visible(False)
                else:
                    self._compat_row.set_subtitle(
                        f"Экспериментально: {count} расш. не поддерживают GNOME {gnome_ver}"
                    )
                    self._compat_patch_btn.set_sensitive(True)
                    self._compat_group.set_visible(True)

            GLib.idle_add(_update)

        threading.Thread(target=_check, daemon=True).start()

    def _offer_shell_restart(self):
        is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        dialog = Adw.AlertDialog()
        dialog.set_heading("Перезапустить GNOME Shell?")
        if is_wayland:
            dialog.set_body("На Wayland перезапуск Shell недоступен.\nВыйдите из сессии и войдите снова.")
            dialog.add_response("ok", "Понятно")
        else:
            dialog.set_body("Чтобы изменения вступили в силу, перезапустите GNOME Shell (X11).")
            dialog.add_response("cancel", "Позже")
            dialog.add_response("restart", "Перезапустить")
            dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)

            def on_response(_d, r):
                if r == "restart":
                    subprocess.Popen(["gnome-shell", "--replace"])

            dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _on_patch_compat(self, _btn):
        gnome_ver = self._get_shell_version()
        self._compat_patch_btn.set_sensitive(False)
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Обновление metadata.json для GNOME {gnome_ver}...")

        def _do():
            patched, failed = _patch_shell_versions(gnome_ver, self._log)

            def _finish():
                if patched:
                    self._log(f"✔  Обновлено расширений: {len(patched)}\n")
                    self._compat_revert_btn.set_sensitive(True)
                    self._compat_row.set_subtitle("Исправления применены — доступен откат")
                    self._offer_shell_restart()
                else:
                    self._log(f"ℹ  Все расширения уже поддерживают GNOME {gnome_ver}\n")
                    self._compat_group.set_visible(False)
                if failed:
                    self._log(f"⚠  Системные расширения без прав ({len(failed)}): {', '.join(failed)}\n")
                if hasattr(win, "stop_progress"):
                    win.stop_progress(True)

            GLib.idle_add(_finish)

        threading.Thread(target=_do, daemon=True).start()

    def _on_revert_compat(self, _btn):
        self._compat_revert_btn.set_sensitive(False)
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Откат shell-version в metadata.json...")

        def _do():
            count = _revert_shell_versions(self._log)

            def _finish():
                if count:
                    self._log(f"✔  Откат выполнен для {count} расширений\n")
                    self._compat_revert_btn.set_sensitive(False)
                    self._compat_group.set_visible(False)
                    self._offer_shell_restart()
                else:
                    self._log("ℹ  Нечего откатывать\n")
                    self._compat_revert_btn.set_sensitive(_SHELLVER_BACKUP.exists())
                if hasattr(win, "stop_progress"):
                    win.stop_progress(True)

            GLib.idle_add(_finish)

        threading.Thread(target=_do, daemon=True).start()

    def _install_native_fallback(self, target_id: str, uuid_hint: str = None) -> tuple[bool, str | None]:
        GLib.idle_add(self._log, "⚠  gext дал сбой. Пробую нативный метод установки...\n")
        try:
            shell_ver = self._get_shell_version()
            key = "pk" if target_id.isdigit() else "uuid"
            url = f"https://extensions.gnome.org/extension-info/?{key}={target_id}&shell_version={shell_ver}"
            
            req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            
            dl_path = data.get("download_url")
            if not dl_path:
                raise ValueError("Не удалось найти версию для вашего GNOME Shell")
            
            uuid = data.get("uuid")
            full_url = f"https://extensions.gnome.org{dl_path}"
            
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                urllib.request.urlretrieve(full_url, tmp.name)
                zip_path = tmp.name
            
            subprocess.run(["gnome-extensions", "install", "--force", zip_path], check=True)
            os.unlink(zip_path)
            
            if uuid:
                subprocess.run(["gnome-extensions", "enable", uuid])
            return True, uuid
        except Exception as e:
            GLib.idle_add(self._log, f"✘  Нативный метод тоже не помог: {e}\n")
            return False, None

    def _build_search_group(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self._body.append(box)

        self._id_entry = Gtk.Entry()
        self._id_entry.set_valign(Gtk.Align.CENTER)
        self._id_entry.set_placeholder_text("Поиск расширений или ID...")
        self._id_entry.set_hexpand(True)
        self._id_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "edit-clear-symbolic")
        self._id_entry.connect("icon-press", lambda e, p, *_: e.set_text("") if p == Gtk.EntryIconPosition.SECONDARY else None)
        self._id_entry.connect("activate", self._on_search_activate)
        self._id_entry.connect("notify::text", self._on_search_text_changed)

        self._id_status = make_status_icon()

        box.append(self._id_entry)
        box.append(self._id_status)

    def _on_search_text_changed(self, entry, _):
        if not entry.get_text() and self._search_results_group:
            self._body.remove(self._search_results_group)
            self._search_results_group = None
            clear_status(self._id_status)

    def _on_search_activate(self, *_):
        text = self._id_entry.get_text().strip()
        if not text:
            return

        if text.isdigit():
            self._install_by_id(text)
        else:
            self._search_extensions(text)

    def _ensure_gext(self) -> str | None:
        gext = _gext_path()
        if gext:
            return gext

        GLib.idle_add(self._log, "▶  gext не найден, устанавливаю...\n")
        
        pip_cmd = next((c for c in ("pip3", "pip") if shutil.which(c)), None)
        
        if not pip_cmd:
            GLib.idle_add(self._log, "▶  pip не найден. Устанавливаю системные пакеты...\n")
            if not backend.run_privileged_sync(["apt-get", "install", "-y", "pip", "python3-module-pip"], self._log):
                return None
            pip_cmd = next((c for c in ("pip3", "pip") if shutil.which(c)), None)

        if not pip_cmd:
            return None

        r_pip = subprocess.run(
            [pip_cmd, "install", "gnome-extensions-cli", "--user"],
            capture_output=True, text=True,
        )
        if r_pip.returncode != 0:
            return None
            
        GLib.idle_add(self._log, "✔  gext установлен!\n")
        return _gext_path() or "gext"

    def _install_by_id(self, ext_id):
        self._id_entry.set_sensitive(False)
        clear_status(self._id_status)
        self._log(f"\n▶  Установка расширения {ext_id}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Установка расширения {ext_id}...")

        def _do():
            ok = False
            err_msg = ""
            try:
                gext = self._ensure_gext()

                fixed, broken_system = _fix_float_versions_in_metadata(self._log)
                if fixed:
                    GLib.idle_add(self._log, f"⚠  Исправлены float-версии в metadata.json: {', '.join(fixed)}\n")

                if gext and not broken_system:
                    r = subprocess.run([gext, "install", ext_id], capture_output=True, text=True)
                    if r.stdout:
                        GLib.idle_add(self._log, r.stdout)
                    err_msg = _detect_install_error(r.stdout, r.stderr)
                    ok = (r.returncode == 0) and not err_msg
                    if not ok and not err_msg:
                        err_msg = r.stderr.strip() or "Не удалось установить расширение"

                if not ok:
                    ok, _ = self._install_native_fallback(ext_id)
            except Exception as e:
                err_msg = str(e)
            finally:
                def _finish():
                    if ok:
                        self._log("✔  Расширение установлено!\n")
                        if hasattr(win, "stop_progress"): win.stop_progress(True)
                        set_status_ok(self._id_status)
                        self._id_entry.set_text("")
                        self._refresh_installed()
                    else:
                        self._log(f"✘  Ошибка: {err_msg}\n")
                        if hasattr(win, "stop_progress"): win.stop_progress(False)
                        set_status_error(self._id_status)
                    self._id_entry.set_sensitive(True)

                GLib.idle_add(_finish)

        threading.Thread(target=_do, daemon=True).start()

    def _search_extensions(self, query):
        if self._search_busy:
            return
        self._search_busy = True
        clear_status(self._id_status)
        
        if self._search_results_group:
            self._body.remove(self._search_results_group)
            self._search_results_group = None

        def _do():
            try:
                params = urllib.parse.urlencode({"search": query, "n_per_page": 10})
                url = f"https://extensions.gnome.org/extension-query/?{params}"
                req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster"})
                
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                
                results = data.get("extensions", [])
                
                installed_uuids = set()
                try:
                    r = subprocess.run(["gnome-extensions", "list"], capture_output=True, text=True)
                    if r.returncode == 0:
                        installed_uuids = set(line.strip() for line in r.stdout.splitlines() if line.strip())
                except Exception:
                    pass

                GLib.idle_add(self._display_search_results, results, installed_uuids)
                
            except Exception as e:
                GLib.idle_add(self._log, f"✘ Ошибка поиска: {e}\n")
                GLib.idle_add(set_status_error, self._id_status)
            finally:
                GLib.idle_add(self._finish_search_request)

        threading.Thread(target=_do, daemon=True).start()

    def _finish_search_request(self):
        self._search_busy = False

    def on_tab_visible(self):
        # Подстраховка: поле поиска всегда должно оставаться доступным.
        self._search_busy = False
        self._id_entry.set_sensitive(True)

    def _display_search_results(self, results, installed_uuids=None):
        if installed_uuids is None:
            installed_uuids = set()
        if not results:
            set_status_error(self._id_status)
            self._log("ℹ Ничего не найдено.\n")
            return

        set_status_ok(self._id_status)
        
        group = Adw.PreferencesGroup()
        group.set_title(f"Результаты поиска ({len(results)})")
        
        if self._installed_group:
            prev = None
            child = self._body.get_first_child()
            while child:
                if child == self._installed_group:
                    break
                prev = child
                child = child.get_next_sibling()
            self._body.insert_child_after(group, prev)
        else:
            self._body.append(group)
            
        self._search_results_group = group

        for ext in results:
            uuid = ext.get("uuid", "")
            is_installed = uuid in installed_uuids
            row = self._make_recommended_row(
                uuid,
                ext.get("name", "Без названия"),
                ext.get("description", ""),
                str(ext.get("pk", "")),
                installed=is_installed,
                register_uuid=False,
            )
            group.add(row)


    def _build_installed_group(self):
        self._installed_group = self._make_installed_group_widget()
        self._body.append(self._installed_group)
        threading.Thread(target=self._load_installed, daemon=True).start()

    def _make_installed_group_widget(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Список расширений")

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Обновить список")
        refresh_btn.connect("clicked", lambda _: self._refresh_installed())
        group.set_header_suffix(refresh_btn)

        return group

    def _load_installed(self):
        user_exts   = _read_extensions_from(_USER_EXT_DIR)
        system_exts = _read_extensions_from(_SYSTEM_EXT_DIR)
        enabled     = _get_enabled_uuids()

        GLib.idle_add(self._populate_installed, user_exts, system_exts, enabled)

    def _populate_installed(self, user_exts, system_exts, enabled):
        self._ext_row_by_uuid.clear()
        self._body.remove(self._installed_group)
        self._installed_group = self._make_installed_group_widget()
        self._body.append(self._installed_group)

        user_uuids = {u[0] for u in user_exts}
        visible_system_exts = [e for e in system_exts if e[0] not in user_uuids]

        installed_uuids = {u[0] for u in user_exts} | {u[0] for u in system_exts}
        missing_recs = [r for r in RECOMMENDED if r[0] not in installed_uuids]

        if not user_exts and not visible_system_exts and not missing_recs:
            row = Adw.ActionRow()
            row.set_title("Расширений не найдено")
            row.set_subtitle("Установите расширения выше — они появятся здесь")
            self._installed_group.add(row)
            return

        rec_desc_by_uuid = {r[0]: r[2] for r in RECOMMENDED}

        if user_exts or missing_recs:
            exp = Adw.ExpanderRow()
            exp.set_title("Пользовательские")
            count_str = f"{len(user_exts)} уст."
            if missing_recs:
                count_str += f", {len(missing_recs)} реком."
            exp.set_subtitle(count_str)
            exp.set_expanded(True)
            for uuid, name, desc in user_exts:
                display_desc = rec_desc_by_uuid.get(uuid) or desc
                exp.add_row(self._make_installed_row(uuid, name, display_desc, uuid in enabled, is_user=True))
            for r in missing_recs:
                uuid, name, desc = r[0], r[1], r[2]
                install_id = r[3] if len(r) > 3 else None
                exp.add_row(self._make_recommended_row(uuid, name, desc, install_id))
            self._installed_group.add(exp)

        if visible_system_exts:
            exp = Adw.ExpanderRow()
            exp.set_title("Системные")
            exp.set_subtitle(f"{len(visible_system_exts)} расш.")
            exp.set_expanded(False)
            for uuid, name, desc in visible_system_exts:
                display_desc = rec_desc_by_uuid.get(uuid) or desc
                exp.add_row(self._make_installed_row(uuid, name, display_desc, uuid in enabled, is_user=False))
            self._installed_group.add(exp)

    def _make_installed_row(
        self, uuid: str, name: str, desc: str, enabled: bool, is_user: bool = False, *, register_uuid: bool = True
    ) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(name)
        row.set_subtitle(uuid)
        row.add_prefix(make_icon("application-x-addon-symbolic"))

        switch = Gtk.Switch()
        switch.set_active(enabled)
        switch.set_valign(Gtk.Align.CENTER)

        def on_state_set(sw, state, u=uuid):
            self._toggle_extension(u, state, sw)
            return True

        switch.connect("state-set", on_state_set)

        has_prefs = (
            (_USER_EXT_DIR / uuid / "prefs.js").exists() or
            (_SYSTEM_EXT_DIR / uuid / "prefs.js").exists()
        )

        info_btn = _make_info_button(desc)

        prefs_btn = Gtk.Button()
        prefs_btn.set_icon_name("emblem-system-symbolic")
        prefs_btn.add_css_class("flat")
        prefs_btn.add_css_class("circular")
        prefs_btn.set_valign(Gtk.Align.CENTER)
        prefs_btn.set_tooltip_text("Настройки расширения")
        prefs_btn.set_sensitive(has_prefs)
        prefs_btn.connect("clicked", lambda _, u=uuid: subprocess.Popen(["gnome-extensions", "prefs", u]))

        del_btn = Gtk.Button()
        del_btn.set_icon_name("user-trash-symbolic")
        del_btn.add_css_class("destructive-action")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("circular")
        del_btn.set_valign(Gtk.Align.CENTER)
        if is_user:
            del_btn.set_tooltip_text("Удалить расширение")
        else:
            del_btn.set_tooltip_text("Удалить системное расширение (с проверкой зависимостей)")
        del_btn.connect("clicked", lambda _, u=uuid, usr=is_user: self._on_delete_ext(u, usr))

        row.add_suffix(make_suffix_box(info_btn, prefs_btn, switch, del_btn))
        if register_uuid:
            self._ext_row_by_uuid[uuid] = row
        return row

    def _make_recommended_row(
        self, uuid, name, desc, install_id=None, installed=False, *, register_uuid: bool = True
    ):
        row = Adw.ActionRow()
        row.set_title(name)
        row.set_subtitle(uuid)
        row.add_prefix(make_icon("application-x-addon-symbolic"))

        status = make_status_icon()
        info_btn = _make_info_button(desc)

        if installed:
            set_status_ok(status)
            btn = make_button("Установлено")
            btn.set_sensitive(False)
            btn.add_css_class("flat")
            row.add_suffix(make_suffix_box(info_btn, status, btn))
        else:
            btn = make_button("Установить")
            btn.connect("clicked", lambda _, u=uuid, b=btn, s=status, iid=install_id: self._on_install_ext(u, b, s, iid))
            row.add_suffix(make_suffix_box(info_btn, status, btn))
        if register_uuid:
            self._ext_row_by_uuid[uuid] = row
        return row

    def focus_extension_by_uuid(self, uuid: str) -> bool:
        row = self._ext_row_by_uuid.get(uuid)
        if row is None:
            return False
        w = row
        while w is not None:
            if isinstance(w, Adw.ExpanderRow):
                w.set_expanded(True)
            w = w.get_parent()
        scroll_child_into_view(self._scroll, row)
        row.grab_focus()
        return True

    def _on_install_ext(self, uuid, btn, status, install_id=None):
        btn.set_sensitive(False)
        btn.set_label("…")

        if (_SYSTEM_EXT_DIR / uuid).exists():
            self._log(
                f"⚠  Расширение {uuid} уже установлено системно.\n"
                "   Пользовательская копия поверх системной не работает в GNOME Shell.\n"
                "   Установка отменена.\n"
            )
            set_status_error(status)
            btn.set_label("Уже системное")
            return

        if install_id and install_id.startswith("epm:"):
            pkg = install_id[4:]
            self._log(f"\n▶  Установка {pkg} (EPM)...\n")
            win = self.get_root()
            if hasattr(win, "start_progress"): win.start_progress(f"Установка {pkg}...")
            def _done(ok):
                if ok:
                    self._log("✔  Установлено!\n")
                    GLib.idle_add(self._refresh_installed)
                else:
                    self._log(f"✘  Ошибка установки {pkg}\n")
                    GLib.idle_add(set_status_error, status)
                    GLib.idle_add(btn.set_label, "Повторить")
                    GLib.idle_add(btn.set_sensitive, True)
                if hasattr(win, "stop_progress"): win.stop_progress(ok)
            backend.run_epm(["epm", "-i", "-y", pkg], self._log, _done)
            return

        self._log(f"\n▶  Установка {uuid}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Установка расширения...")

        def _do():
            gext = self._ensure_gext()
            
            fixed, broken_system = _fix_float_versions_in_metadata(self._log)
            if fixed:
                GLib.idle_add(self._log, f"⚠  Исправлены float-версии в metadata.json: {', '.join(fixed)}\n")

            target = install_id if install_id else uuid
            ok = False
            err_msg = ""
            if gext and not broken_system:
                r = subprocess.run([gext, "install", target], capture_output=True, text=True)
                if r.stdout: GLib.idle_add(self._log, r.stdout)
                err_msg = _detect_install_error(r.stdout, r.stderr)
                ok = (r.returncode == 0) and not err_msg
                if not ok and not err_msg:
                    err_msg = r.stderr.strip() or "Не удалось установить расширение"
            
            if not ok:
                ok, _ = self._install_native_fallback(target, uuid_hint=uuid)
            
            if ok:
                self._log("✔  Установлено!\n")
                GLib.idle_add(self._refresh_installed)
            else:
                if err_msg: self._log(f"✘  Ошибка: {err_msg}\n")
                GLib.idle_add(set_status_error, status)
                GLib.idle_add(btn.set_label, "Повторить")
                GLib.idle_add(btn.set_sensitive, True)
            GLib.idle_add(lambda: win.stop_progress(ok) if hasattr(win, "stop_progress") else None)

        threading.Thread(target=_do, daemon=True).start()

    def _toggle_extension(self, uuid: str, state: bool, switch: Gtk.Switch) -> None:
        cmd = ["gnome-extensions", "enable" if state else "disable", uuid]
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"{'Включение' if state else 'Отключение'} расширения...")

        def _do():
            r = subprocess.run(cmd, capture_output=True, text=True)
            ok = r.returncode == 0
            if ok:
                GLib.idle_add(switch.set_state, state)
                action = "включено" if state else "выключено"
                self._log(f"✔  {uuid.split('@')[0]} {action}\n")
            else:
                self._log(f"✘  Ошибка: {r.stderr.strip()}\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)

        threading.Thread(target=_do, daemon=True).start()

    def _on_delete_ext(self, uuid: str, is_user: bool = True) -> None:
        if is_user:
            body = f"«{uuid}» будет удалён из\n~/.local/share/gnome-shell/extensions/"
        else:
            body = (
                f"«{uuid}» будет удалён из системы.\n"
                "Сначала будет проверено, нет ли зависящих пакетов RPM.\n"
                "Потребуются права администратора."
            )
        dialog = Adw.AlertDialog()
        dialog.set_heading("Удалить расширение?")
        dialog.set_body(body)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, r):
            if r != "delete":
                return
            if is_user:
                self._do_delete_ext(uuid)
            else:
                self._do_delete_system_ext(uuid)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _do_delete_ext(self, uuid: str) -> None:
        self._log(f"\n▶  Удаление {uuid}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление расширения...")

        def _do():
            ok = False
            try:
                r = subprocess.run(
                    ["gnome-extensions", "uninstall", uuid],
                    capture_output=True, text=True,
                )
                ok = r.returncode == 0

                if not ok:
                    ext_path = _USER_EXT_DIR / uuid
                    if not ext_path.exists():
                        for meta in _USER_EXT_DIR.glob("*/metadata.json"):
                            try:
                                if json.loads(meta.read_text(encoding="utf-8")).get("uuid") == uuid:
                                    ext_path = meta.parent
                                    break
                            except Exception:
                                pass

                    if ext_path.exists():
                        shutil.rmtree(ext_path)
                        ok = True
                    else:
                        GLib.idle_add(self._log, "ℹ  Папка расширения не найдена (возможно, уже удалено).\n")

                if ok:
                    GLib.idle_add(self._log, f"✔  {uuid} удалён!\n")
                    GLib.idle_add(self._refresh_installed)
                else:
                    GLib.idle_add(self._log, f"✘  Не удалось удалить: {r.stderr.strip()}\n")

            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка удаления: {e}\n")

            GLib.idle_add(lambda: win.stop_progress(ok) if hasattr(win, "stop_progress") else None)

        threading.Thread(target=_do, daemon=True).start()

    def _do_delete_system_ext(self, uuid: str) -> None:
        ext_path = _SYSTEM_EXT_DIR / uuid
        self._log(f"\n▶  Проверка зависимостей для {uuid}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Удаление системного расширения...")

        def _do():
            r_own = subprocess.run(
                ["rpm", "-qf", str(ext_path)],
                capture_output=True, text=True,
            )

            if r_own.returncode == 0:
                pkg_name = r_own.stdout.strip().splitlines()[0]
                GLib.idle_add(self._log, f"▶  Пакет RPM: {pkg_name}\n")

                r_deps = subprocess.run(
                    ["rpm", "-q", "--whatrequires", pkg_name],
                    capture_output=True, text=True,
                )
                deps_out = r_deps.stdout.strip()
                has_deps = (
                    r_deps.returncode == 0
                    and bool(deps_out)
                    and "no package" not in deps_out.lower()
                )
                if has_deps:
                    self._log(
                        f"✘  Удаление невозможно — от «{pkg_name}» зависят:\n"
                        + "\n".join(f"    • {d}" for d in deps_out.splitlines())
                        + "\n",
                    )
                    if hasattr(win, "stop_progress"): win.stop_progress(False)
                    return

                GLib.idle_add(self._log, f"▶  Удаляю пакет {pkg_name}...\n")
                ok = backend.run_privileged_sync(["rpm", "-e", pkg_name], self._log)
            else:
                GLib.idle_add(self._log, "▶  Директория не принадлежит RPM, удаляю rm -rf...\n")
                ok = backend.run_privileged_sync(["rm", "-rf", str(ext_path)], self._log)

            if ok:
                self._log(f"✔  {uuid} удалён!\n")
                GLib.idle_add(self._refresh_installed)
            else:
                self._log("✘  Ошибка удаления\n")
            GLib.idle_add(lambda: win.stop_progress(ok) if hasattr(win, "stop_progress") else None)

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_installed(self):
        threading.Thread(target=self._load_installed, daemon=True).start()

