
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
import config
from ui.common import load_module
from ui.rows import TaskRow, SettingRow
from widgets import make_icon, make_scrolled_page, make_suffix_box


@dataclass
class FlatpakApp:
    app_id: str
    name: str
    version: str
    installation: str
    masked: bool = field(default=False)
    icon_path: str | None = field(default=None)


def _is_flatpak_available() -> bool:
    return shutil.which("flatpak") is not None


def _list_flatpak_apps() -> list[FlatpakApp]:
    try:
        r = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application,name,version,installation"],
            capture_output=True, text=True, timeout=15,
        )
        apps = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            app_id, name, version, installation = parts[0], parts[1], parts[2], parts[3]
            apps.append(FlatpakApp(
                app_id=app_id.strip(),
                name=name.strip() or app_id.strip(),
                version=version.strip(),
                installation=installation.strip(),
            ))
        return apps
    except Exception:
        return []


def _get_masked_ids() -> set[str]:
    masked: set[str] = set()
    for scope in ("--user", "--system"):
        try:
            r = subprocess.run(
                ["flatpak", "mask", scope],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    masked.add(line)
        except Exception:
            pass
    return masked


def _run_user_op(cmd: list, on_line, on_done) -> None:
    def _worker():
        ok = False
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8",
            )
            for line in proc.stdout:
                GLib.idle_add(on_line, line)
            proc.wait()
            ok = proc.returncode == 0
        except Exception as e:
            GLib.idle_add(on_line, f"✘ Ошибка: {e}\n")
        GLib.idle_add(on_done, ok)

    threading.Thread(target=_worker, daemon=True).start()


def _build_icon_index() -> dict[str, str]:
    index: dict[str, str] = {}

    for size in ("64x64", "128x128", "scalable"):
        for base in (
            Path.home() / ".local" / "share" / "flatpak" / "appstream",
            Path("/var/lib/flatpak/appstream"),
        ):
            for icons_dir in base.glob(f"*/*/active/icons/{size}"):
                if not icons_dir.is_dir():
                    continue
                for icon_file in icons_dir.iterdir():
                    if icon_file.stem not in index:
                        index[icon_file.stem] = str(icon_file)

    for app_base in (
        Path.home() / ".local" / "share" / "flatpak" / "app",
        Path("/var/lib/flatpak/app"),
    ):
        if not app_base.exists():
            continue
        for app_dir in app_base.iterdir():
            app_id = app_dir.name
            if app_id in index:
                continue
            hicolor = app_dir / "current" / "active" / "files" / "share" / "icons" / "hicolor"
            if not hicolor.exists():
                continue
            for size in ("64x64", "128x128", "scalable"):
                apps_dir = hicolor / size / "apps"
                if not apps_dir.is_dir():
                    continue
                for icon_file in apps_dir.iterdir():
                    if icon_file.stem == app_id:
                        index[app_id] = str(icon_file)
                        break
                if app_id in index:
                    break

    return index


def _make_app_icon(icon_path: str | None) -> Gtk.Widget:
    if icon_path:
        try:
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import Gdk, GdkPixbuf
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 32, 32, True)
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            img = Gtk.Image()
            img.set_from_paintable(texture)
            img.set_size_request(32, 32)
            return img
        except Exception:
            pass
    return make_icon("package-x-generic-symbolic")


def _backup_flatpak(backup_dir: Path, what: dict, log_fn, done_fn, password: str | None = None) -> None:
    def _worker():
        ok = True
        tmp_meta = None
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            work_dir = backup_dir

            if password:
                tmp_meta = Path(tempfile.mkdtemp(prefix="altbooster-meta-"))
                work_dir = tmp_meta

            if what.get("apps"):
                GLib.idle_add(log_fn, "▶  Экспорт списка приложений...\n")
                r = subprocess.run(
                    ["flatpak", "list", "--app", "--columns=installation,ref"],
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                (work_dir / "flatpak-apps.txt").write_text(r.stdout, encoding="utf-8")
                GLib.idle_add(log_fn, "   Сохранено: flatpak-apps.txt\n")

            if what.get("remotes"):
                GLib.idle_add(log_fn, "▶  Экспорт репозиториев...\n")
                r = subprocess.run(
                    ["flatpak", "remotes", "--columns=name,url"],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                (work_dir / "flatpak-remotes.txt").write_text(r.stdout, encoding="utf-8")
                GLib.idle_add(log_fn, "   Сохранено: flatpak-remotes.txt\n")

            if what.get("overrides"):
                GLib.idle_add(log_fn, "▶  Экспорт переопределений прав...\n")
                overrides_src = Path.home() / ".local" / "share" / "flatpak" / "overrides"
                if overrides_src.exists():
                    if password:
                        shutil.copytree(str(overrides_src), str(work_dir / "overrides"))
                        GLib.idle_add(log_fn, "   Сохранено: overrides/\n")
                    else:
                        r = subprocess.run(
                            ["tar", "-czf", str(backup_dir / "overrides.tar.gz"),
                             "-C", str(overrides_src.parent), "overrides"],
                            capture_output=True, timeout=30,
                        )
                        if r.returncode == 0:
                            GLib.idle_add(log_fn, "   Сохранено: overrides.tar.gz\n")
                        else:
                            GLib.idle_add(log_fn, "   ⚠  Не удалось сохранить переопределения\n")
                else:
                    GLib.idle_add(log_fn, "   Нет переопределений для сохранения\n")

            if password:
                import datetime
                (work_dir / "backup.ok").write_text(
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8"
                )
                GLib.idle_add(log_fn, "▶  Шифрование бэкапа (AES-256)...\n")

                var_app = Path.home() / ".var" / "app"
                gpg_path = backup_dir / "flatpak-backup.gpg"
                tar_args = [
                    "tar",
                    "--exclude=*/cache", "--exclude=*/Cache", "--exclude=*/.cache",
                    "-c",
                    "-C", str(tmp_meta.parent), tmp_meta.name,
                ]
                if what.get("data") and var_app.exists():
                    GLib.idle_add(log_fn, "   Включение ~/.var/app/ (полный бэкап)...\n")
                    tar_args += ["-C", str(var_app.parent), "app"]

                r_fd, w_fd = os.pipe()
                os.write(w_fd, password.encode())
                os.close(w_fd)

                tar_proc = subprocess.Popen(tar_args, stdout=subprocess.PIPE)
                gpg_proc = subprocess.Popen(
                    ["gpg", "--batch", "--yes", f"--passphrase-fd={r_fd}",
                     "--symmetric", "--cipher-algo", "AES256", "-o", str(gpg_path)],
                    stdin=tar_proc.stdout,
                    pass_fds=(r_fd,),
                )
                tar_proc.stdout.close()
                gpg_proc.wait()
                tar_proc.wait()
                os.close(r_fd)

                ok = tar_proc.returncode == 0 and gpg_proc.returncode == 0
                if ok:
                    GLib.idle_add(log_fn, "   Сохранено: flatpak-backup.gpg\n")
                else:
                    GLib.idle_add(log_fn, "   ⚠  Ошибка шифрования\n")

            elif what.get("data"):
                GLib.idle_add(log_fn, "▶  Экспорт данных приложений (~/.var/app/)...\n")
                var_app = Path.home() / ".var" / "app"
                if var_app.exists():
                    dest_dir = backup_dir / "var-app"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    r = subprocess.run(
                        ["rsync", "-a", "--delete",
                         "--exclude=cache/", "--exclude=Cache/", "--exclude=.cache/",
                         str(var_app) + "/", str(dest_dir) + "/"],
                        capture_output=True, text=True, encoding="utf-8",
                    )
                    if r.returncode == 0:
                        GLib.idle_add(log_fn, "   Сохранено: var-app/\n")
                    else:
                        GLib.idle_add(log_fn, f"   ⚠  Ошибка rsync: {r.stderr[:200]}\n")
                        ok = False
                else:
                    GLib.idle_add(log_fn, "   ~/.var/app/ не найден, пропуск\n")

        except Exception as e:
            GLib.idle_add(log_fn, f"✘ Ошибка: {e}\n")
            ok = False
        finally:
            if tmp_meta and tmp_meta.exists():
                shutil.rmtree(tmp_meta, ignore_errors=True)

        if ok and not password:
            import datetime
            (backup_dir / "backup.ok").write_text(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8"
            )

        GLib.idle_add(done_fn, ok)

    threading.Thread(target=_worker, daemon=True).start()


def _restore_flatpak(backup_dir: Path, log_fn, done_fn) -> None:
    def _worker():
        ok = True
        try:
            remotes_file = backup_dir / "flatpak-remotes.txt"
            if remotes_file.exists():
                GLib.idle_add(log_fn, "▶  Восстановление репозиториев...\n")
                for line in remotes_file.read_text(encoding="utf-8").splitlines():
                    parts = line.split("\t")
                    if len(parts) < 2:
                        continue
                    name, url = parts[0].strip(), parts[1].strip()
                    if not name or not url:
                        continue
                    r = subprocess.run(
                        ["flatpak", "remote-add", "--user", "--if-not-exists", name, url],
                        capture_output=True, text=True, encoding="utf-8", timeout=30,
                    )
                    status = "✔" if r.returncode == 0 else "⚠"
                    GLib.idle_add(log_fn, f"   {status} {name}\n")

            apps_file = backup_dir / "flatpak-apps.txt"
            if apps_file.exists():
                GLib.idle_add(log_fn, "▶  Переустановка приложений...\n")
                for line in apps_file.read_text(encoding="utf-8").splitlines():
                    parts = line.split("\t")
                    if len(parts) < 2:
                        continue
                    installation, ref = parts[0].strip(), parts[1].strip()
                    if not ref:
                        continue
                    if installation == "system":
                        GLib.idle_add(log_fn, f"   ⚠  Пропуск системного {ref} (требует привилегий)\n")
                        continue
                    r = subprocess.run(
                        ["flatpak", "install", "-y", "--user", ref],
                        capture_output=True, text=True, encoding="utf-8", timeout=300,
                    )
                    status = "✔" if r.returncode == 0 else "⚠"
                    GLib.idle_add(log_fn, f"   {status} {ref}\n")

            overrides_archive = backup_dir / "overrides.tar.gz"
            overrides_dir_src = backup_dir / "overrides"
            if overrides_archive.exists():
                GLib.idle_add(log_fn, "▶  Восстановление переопределений прав...\n")
                dest = Path.home() / ".local" / "share" / "flatpak"
                dest.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(
                    ["tar", "-xzf", str(overrides_archive), "-C", str(dest)],
                    capture_output=True, timeout=30,
                )
                status = "✔" if r.returncode == 0 else "⚠"
                GLib.idle_add(log_fn, f"   {status} Переопределения восстановлены\n")
            elif overrides_dir_src.exists():
                GLib.idle_add(log_fn, "▶  Восстановление переопределений прав...\n")
                dest_overrides = Path.home() / ".local" / "share" / "flatpak" / "overrides"
                if dest_overrides.exists():
                    shutil.rmtree(dest_overrides)
                shutil.copytree(str(overrides_dir_src), str(dest_overrides))
                GLib.idle_add(log_fn, "   ✔ Переопределения восстановлены\n")

            data_src = None
            if (backup_dir / "var-app").exists():
                data_src = backup_dir / "var-app"
            elif (backup_dir / "app").exists():
                data_src = backup_dir / "app"
            if data_src:
                GLib.idle_add(log_fn, "▶  Восстановление данных приложений...\n")
                var_app_dest = Path.home() / ".var" / "app"
                var_app_dest.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(
                    ["rsync", "-a",
                     str(data_src) + "/", str(var_app_dest) + "/"],
                    capture_output=True, text=True, encoding="utf-8",
                )
                if r.returncode == 0:
                    GLib.idle_add(log_fn, "   ✔ Данные восстановлены\n")
                else:
                    GLib.idle_add(log_fn, f"   ⚠ Ошибка: {r.stderr[:200]}\n")
                    ok = False

        except Exception as e:
            GLib.idle_add(log_fn, f"✘ Ошибка: {e}\n")
            ok = False
        GLib.idle_add(done_fn, ok)

    threading.Thread(target=_worker, daemon=True).start()


class ConfirmFlatpakRow(TaskRow):

    def __init__(self, task, log_fn):
        super().__init__(task, log_fn, None)

    def start(self, *args):
        if getattr(self, "_running", False):
            return
        dialog = Adw.AlertDialog(
            heading="Удалить мусор Flatpak?",
            body=(
                "Будут удалены runtime-библиотеки, которые не требуются установленным приложениям.\n\n"
                "⚠ Внимание: Если вы устанавливали runtime вручную для сторонних программ "
                "(не из Flathub), они могут перестать работать."
            ),
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("run", "Удалить")
        dialog.set_response_appearance("run", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: super(ConfirmFlatpakRow, self).start() if r == "run" else None)
        dialog.present(self.get_root())


class FlatpakPage(Gtk.Box):

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._apps_group: Adw.PreferencesGroup | None = None
        self._refresh_btn: Gtk.Button | None = None
        self._update_all_btn: Gtk.Button | None = None

        scroll, self._body = make_scrolled_page()

        export_btn = Gtk.Button(label="Экспорт")
        export_btn.set_icon_name("document-save-symbolic")
        export_btn.add_css_class("suggested-action")
        export_btn.add_css_class("pill")
        export_btn.set_tooltip_text("Сохранить список приложений, репозитории, права и данные ~/.var/app/ в папку. Поддерживает шифрование AES-256.")
        export_btn.connect("clicked", self._on_export_clicked)

        import_btn = Gtk.Button(label="Импорт")
        import_btn.set_icon_name("document-open-symbolic")
        import_btn.add_css_class("pill")
        import_btn.set_tooltip_text("Восстановить данные из ранее созданного бэкапа. Поддерживает обычные и зашифрованные бэкапы (.gpg).")
        import_btn.connect("clicked", self._on_restore_clicked)

        fab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fab_box.set_halign(Gtk.Align.END)
        fab_box.set_valign(Gtk.Align.END)
        fab_box.set_margin_end(18)
        fab_box.set_margin_bottom(18)
        fab_box.append(import_btn)
        fab_box.append(export_btn)

        overlay = Gtk.Overlay()
        overlay.set_child(scroll)
        overlay.add_overlay(fab_box)
        self.append(overlay)

        self._build_header_group()
        self._build_apps_group()
        self._build_manage_group()


    def _build_header_group(self):
        group = Adw.PreferencesGroup()
        group.set_title("Установленные приложения Flatpak")
        group.set_description(
            "Установленные Flatpak-приложения. "
            "Можно обновлять, удалять и замораживать обновления по отдельности."
        )
        self._body.append(group)

        suffix_box = Gtk.Box(spacing=8)
        suffix_box.set_valign(Gtk.Align.CENTER)

        self._refresh_btn = Gtk.Button()
        self._refresh_btn.set_icon_name("view-refresh-symbolic")
        self._refresh_btn.add_css_class("flat")
        self._refresh_btn.set_tooltip_text("Обновить список")
        self._refresh_btn.connect("clicked", lambda _: self._refresh())
        suffix_box.append(self._refresh_btn)

        self._update_all_btn = Gtk.Button(label="Обновить все")
        self._update_all_btn.add_css_class("suggested-action")
        self._update_all_btn.add_css_class("pill")
        self._update_all_btn.set_size_request(120, -1)
        self._update_all_btn.set_valign(Gtk.Align.CENTER)
        self._update_all_btn.set_sensitive(False)
        self._update_all_btn.connect("clicked", lambda _: self._on_update_all())
        suffix_box.append(self._update_all_btn)

        group.set_header_suffix(suffix_box)

    def _build_apps_group(self):
        self._apps_group = Adw.PreferencesGroup()
        self._body.append(self._apps_group)
        self._refresh()


    def _refresh(self):
        self._clear_apps_group()
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(False)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)

        spinner_row = Adw.ActionRow()
        spinner_row.set_title("Загрузка...")
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_valign(Gtk.Align.CENTER)
        spinner_row.add_suffix(spinner)
        self._apps_group.add(spinner_row)

        threading.Thread(target=self._load_apps, daemon=True).start()

    def _clear_apps_group(self):
        if self._apps_group:
            self._body.remove(self._apps_group)
        self._apps_group = Adw.PreferencesGroup()
        self._body.append(self._apps_group)

    def _load_apps(self):
        if not _is_flatpak_available():
            GLib.idle_add(self._show_unavailable_state)
            return
        if not backend.is_flathub_enabled():
            GLib.idle_add(self._show_no_flathub_dialog)
            return
        apps = _list_flatpak_apps()
        masked = _get_masked_ids()
        icons = _build_icon_index()
        for app in apps:
            app.masked = app.app_id in masked
            app.icon_path = icons.get(app.app_id)
        GLib.idle_add(self._populate_apps, apps)

    def _populate_apps(self, apps: list[FlatpakApp]):
        self._clear_apps_group()
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(True)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(bool(apps))

        if not apps:
            row = Adw.ActionRow()
            row.set_title("Flatpak-приложений не найдено")
            row.set_subtitle("Установите Flatpak-приложения — они появятся здесь")
            row.add_prefix(make_icon("package-x-generic-symbolic"))
            self._apps_group.add(row)
            return

        user_apps   = sorted([a for a in apps if a.installation == "user"],   key=lambda a: a.name.lower())
        system_apps = sorted([a for a in apps if a.installation == "system"], key=lambda a: a.name.lower())
        other_apps  = sorted([a for a in apps if a.installation not in ("user", "system")], key=lambda a: a.name.lower())

        if user_apps and system_apps:
            exp_u = Adw.ExpanderRow()
            exp_u.set_title("Пользовательские")
            exp_u.set_subtitle(f"{len(user_apps)} прил.")
            exp_u.set_expanded(True)
            for app in user_apps:
                exp_u.add_row(self._make_app_row(app))
            self._apps_group.add(exp_u)

            exp_s = Adw.ExpanderRow()
            exp_s.set_title("Системные")
            exp_s.set_subtitle(f"{len(system_apps)} прил.")
            exp_s.set_expanded(True)
            for app in system_apps:
                exp_s.add_row(self._make_app_row(app))
            self._apps_group.add(exp_s)

            for app in other_apps:
                self._apps_group.add(self._make_app_row(app))
        else:
            for app in user_apps + system_apps + other_apps:
                self._apps_group.add(self._make_app_row(app))

    def _show_unavailable_state(self):
        self._clear_apps_group()
        row = Adw.ActionRow()
        row.set_title("flatpak не установлен")
        row.set_subtitle("Установите пакет «flatpak» для управления Flatpak-приложениями")
        row.add_prefix(make_icon("dialog-warning-symbolic"))
        self._apps_group.add(row)
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(True)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)

    def _show_no_flathub_dialog(self):
        self._clear_apps_group()
        row = Adw.ActionRow()
        row.set_title("Flathub не подключён")
        row.set_subtitle("Добавьте репозиторий Flathub для установки и управления приложениями")
        row.add_prefix(make_icon("dialog-information-symbolic"))
        self._apps_group.add(row)
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(True)
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)

        dialog = Adw.AlertDialog(
            heading="Flathub не подключён",
            body="Для управления Flatpak-приложениями необходимо подключить репозиторий Flathub.\n\nПодключить сейчас?",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("setup", "Подключить Flathub")
        dialog.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("setup")
        dialog.connect("response", self._on_no_flathub_response)
        dialog.present(self.get_root())

    def _on_no_flathub_response(self, _dialog, response):
        if response != "setup":
            return
        self._log("\n▶  Установка Flatpak и Flathub...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Установка Flatpak...")

        def _step2(ok):
            if not ok:
                if hasattr(win, "stop_progress"):
                    GLib.idle_add(win.stop_progress, False)
                return
            backend.run_privileged(
                ["apt-get", "install", "-y", "flatpak-repo-flathub"],
                self._log,
                lambda ok2: (
                    GLib.idle_add(self._log, "✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n"),
                    GLib.idle_add(win.stop_progress, ok2) if hasattr(win, "stop_progress") else None,
                    GLib.idle_add(self._refresh) if ok2 else None,
                ),
            )

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, _step2)


    def _make_app_row(self, app: FlatpakApp) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(app.name)
        row.set_subtitle(app.app_id)
        row.add_prefix(_make_app_icon(app.icon_path))

        ver_label = Gtk.Label(label=app.version or "—")
        ver_label.add_css_class("dim-label")
        ver_label.add_css_class("caption")
        ver_label.set_valign(Gtk.Align.CENTER)

        freeze_btn = Gtk.ToggleButton(label="❄")
        freeze_btn.set_active(app.masked)
        freeze_btn.add_css_class("flat")
        freeze_btn.add_css_class("circular")
        freeze_btn.set_valign(Gtk.Align.CENTER)
        freeze_btn.set_tooltip_text("Заморозить обновления (flatpak mask)")
        if app.masked:
            freeze_btn.add_css_class("accent")
        _hid: list[int] = []
        _hid.append(freeze_btn.connect(
            "toggled",
            lambda btn, a=app, h=_hid: self._on_freeze_toggle(a, btn, h),
        ))

        upd_btn = Gtk.Button()
        upd_btn.set_icon_name("software-update-available-symbolic")
        upd_btn.add_css_class("flat")
        upd_btn.add_css_class("circular")
        upd_btn.set_valign(Gtk.Align.CENTER)
        upd_btn.set_tooltip_text("Обновить")
        upd_btn.connect("clicked", lambda _, a=app: self._on_update_app(a))

        del_btn = Gtk.Button()
        del_btn.set_icon_name("user-trash-symbolic")
        del_btn.add_css_class("destructive-action")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("circular")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.set_tooltip_text("Удалить")
        del_btn.connect("clicked", lambda _, a=app: self._on_remove_app(a))

        row.add_suffix(make_suffix_box(ver_label, freeze_btn, upd_btn, del_btn))
        return row


    def _on_remove_app(self, app: FlatpakApp):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Удалить приложение?")
        dialog.set_body(
            f"«{app.name}» ({app.app_id}) будет удалён.\n"
            f"Расположение: {app.installation}."
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: self._do_remove_app(app) if r == "delete" else None)
        dialog.present(self.get_root())

    def _do_remove_app(self, app: FlatpakApp):
        self._log(f"\n▶  Удаление {app.name} ({app.app_id})...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Удаление {app.name}...")

        scope = "--user" if app.installation == "user" else "--system"
        cmd = ["flatpak", "uninstall", "-y", scope, app.app_id]

        def _done(ok):
            msg = f"✔  {app.name} удалён!\n" if ok else f"✘  Ошибка удаления {app.name}\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            GLib.idle_add(self._refresh)

        if app.installation == "user":
            _run_user_op(cmd, self._log, _done)
        else:
            backend.run_privileged(cmd, self._log, _done)

    def _on_update_app(self, app: FlatpakApp):
        self._log(f"\n▶  Обновление {app.name} ({app.app_id})...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress(f"Обновление {app.name}...")

        scope = "--user" if app.installation == "user" else "--system"
        cmd = ["flatpak", "update", "-y", scope, app.app_id]

        def _done(ok):
            msg = f"✔  {app.name} обновлён!\n" if ok else f"✘  Ошибка обновления {app.name}\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            GLib.idle_add(self._refresh)

        if app.installation == "user":
            _run_user_op(cmd, self._log, _done)
        else:
            backend.run_privileged(cmd, self._log, _done)

    def _on_freeze_toggle(self, app: FlatpakApp, btn: Gtk.ToggleButton, hid: list[int]):
        state = btn.get_active()
        scope = "--user" if app.installation == "user" else "--system"
        if state:
            cmd = ["flatpak", "mask", scope, app.app_id]
            action = "заморожен"
        else:
            cmd = ["flatpak", "mask", "--remove", scope, app.app_id]
            action = "разморожен"

        btn.set_sensitive(False)

        def _apply_state(new_state: bool):
            btn.handler_block(hid[0])
            btn.set_active(new_state)
            if new_state:
                btn.add_css_class("accent")
            else:
                btn.remove_css_class("accent")
            btn.handler_unblock(hid[0])
            btn.set_sensitive(True)

        def _done(ok):
            if ok:
                GLib.idle_add(self._log, f"✔  {app.name} {action}\n")
                GLib.idle_add(_apply_state, state)
            else:
                GLib.idle_add(
                    self._log,
                    f"✘  Не удалось изменить заморозку для {app.name}\n"
                    "   Команда flatpak mask доступна в flatpak ≥ 1.8\n",
                )
                GLib.idle_add(_apply_state, not state)

        if app.installation == "user":
            _run_user_op(cmd, self._log, _done)
        else:
            backend.run_privileged(cmd, self._log, _done)


    def _on_export_clicked(self, _btn):
        dialog = Adw.AlertDialog(
            heading="Экспорт данных Flatpak",
            body="Выберите что включить в бэкап:",
        )

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(4)

        cb_apps = Gtk.CheckButton(label="Список приложений")
        cb_apps.set_active(True)
        cb_remotes = Gtk.CheckButton(label="Репозитории (remotes)")
        cb_remotes.set_active(True)
        cb_overrides = Gtk.CheckButton(label="Переопределения прав (overrides)")
        cb_overrides.set_active(True)
        cb_data = Gtk.CheckButton(label="Данные приложений (~/.var/app/, без cache)")
        cb_data.set_active(True)

        data_warn = Gtk.Label(label="⚠ Может занять несколько минут в зависимости от объёма данных")
        data_warn.add_css_class("dim-label")
        data_warn.add_css_class("caption")
        data_warn.set_margin_start(24)
        data_warn.set_halign(Gtk.Align.START)
        data_warn.set_wrap(True)
        cb_data.connect("toggled", lambda btn: data_warn.set_visible(btn.get_active()))

        for cb in (cb_apps, cb_remotes, cb_overrides, cb_data):
            vbox.append(cb)
        vbox.append(data_warn)

        sep = Gtk.Separator()
        sep.set_margin_top(8)
        sep.set_margin_bottom(4)
        vbox.append(sep)

        cb_encrypt = Gtk.CheckButton(label="Зашифровать бэкап (AES-256)")
        vbox.append(cb_encrypt)

        encrypt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        encrypt_box.set_margin_start(24)
        encrypt_box.set_margin_top(4)
        encrypt_box.set_visible(False)

        pw_entry = Gtk.PasswordEntry()
        pw_entry.set_property("placeholder-text", "Пароль")
        pw_entry.set_show_peek_icon(True)
        pw_confirm = Gtk.PasswordEntry()
        pw_confirm.set_property("placeholder-text", "Подтвердить пароль")
        pw_confirm.set_show_peek_icon(True)
        encrypt_box.append(pw_entry)
        encrypt_box.append(pw_confirm)
        vbox.append(encrypt_box)

        cb_encrypt.connect("toggled", lambda btn: encrypt_box.set_visible(btn.get_active()))

        dialog.set_extra_child(vbox)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("ok", "Выбрать папку...")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def _on_response(d, r):
            if r != "ok":
                return
            what = {
                "apps": cb_apps.get_active(),
                "remotes": cb_remotes.get_active(),
                "overrides": cb_overrides.get_active(),
                "data": cb_data.get_active(),
            }
            if not any(what.values()):
                return
            password = None
            if cb_encrypt.get_active():
                pw = pw_entry.get_text()
                pw2 = pw_confirm.get_text()
                if not pw or pw != pw2:
                    err = Adw.AlertDialog(
                        heading="Ошибка пароля",
                        body="Пароли не совпадают или пустые. Попробуйте ещё раз.",
                    )
                    err.add_response("ok", "Ок")
                    err.present(self.get_root())
                    return
                password = pw
            self._pick_export_folder(what, password)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _pick_export_folder(self, what: dict, password: str | None = None):
        fc = Gtk.FileChooserNative(
            title="Выберите папку для бэкапа",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self.get_root(),
            accept_label="Экспортировать",
            cancel_label="Отмена",
        )

        def _on_response(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                folder = Path(d.get_file().get_path())
                self._do_export(folder, what, password)
            d.unref()

        fc.connect("response", _on_response)
        fc.show()

    def _do_export(self, folder: Path, what: dict, password: str | None = None):
        win = self.get_root()
        self._log(f"\n▶  Экспорт в {folder}...\n")
        if hasattr(win, "start_progress"):
            win.start_progress("Экспорт данных Flatpak...")

        def _done(ok):
            msg = f"✔  Бэкап сохранён в {folder}\n" if ok else "✘  Ошибка при экспорте\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)

        _backup_flatpak(folder, what, self._log, _done, password)

    def _on_restore_clicked(self, _btn):
        fc = Gtk.FileChooserNative(
            title="Выберите папку с бэкапом",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self.get_root(),
            accept_label="Выбрать",
            cancel_label="Отмена",
        )

        def _on_response(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                folder = Path(d.get_file().get_path())
                self._confirm_restore(folder)
            d.unref()

        fc.connect("response", _on_response)
        fc.show()

    def _confirm_restore(self, folder: Path):
        gpg_file = folder / "flatpak-backup.gpg"
        if gpg_file.exists():
            self._ask_decrypt_password(gpg_file)
            return

        marker = folder / "backup.ok"
        if marker.exists():
            date_str = marker.read_text(encoding="utf-8").strip()
            body = f"Данные будут восстановлены из:\n{folder}\n\nБэкап создан: {date_str}\n\n⚠ Существующие данные приложений будут перезаписаны."
        else:
            body = f"Данные будут восстановлены из:\n{folder}\n\n⚠ Файл подтверждения (backup.ok) не найден — бэкап может быть неполным или повреждённым.\n\nВсё равно продолжить?"

        dialog = Adw.AlertDialog(
            heading="Восстановить данные Flatpak?",
            body=body,
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("restore", "Восстановить")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._do_restore(folder) if r == "restore" else None)
        dialog.present(self.get_root())

    def _ask_decrypt_password(self, gpg_file: Path):
        dialog = Adw.AlertDialog(
            heading="Зашифрованный бэкап",
            body=f"Введите пароль для расшифровки:\n{gpg_file.name}",
        )
        pw_entry = Gtk.PasswordEntry()
        pw_entry.set_property("placeholder-text", "Пароль")
        pw_entry.set_show_peek_icon(True)
        pw_entry.set_margin_top(8)
        dialog.set_extra_child(pw_entry)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("ok", "Расшифровать")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def _on_response(d, r):
            if r == "ok":
                self._do_restore_encrypted(gpg_file, pw_entry.get_text())

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _do_restore_encrypted(self, gpg_file: Path, password: str):
        win = self.get_root()
        self._log(f"\n▶  Расшифровка {gpg_file.name}...\n")
        if hasattr(win, "start_progress"):
            win.start_progress("Расшифровка бэкапа...")

        def _worker():
            tmp = None
            ok = True
            try:
                tmp = Path(tempfile.mkdtemp(prefix="altbooster-restore-"))
                tar_path = tmp / "backup.tar"

                r_fd, w_fd = os.pipe()
                os.write(w_fd, password.encode())
                os.close(w_fd)

                r = subprocess.run(
                    ["gpg", "--batch", "--yes", f"--passphrase-fd={r_fd}",
                     "--decrypt", "-o", str(tar_path), str(gpg_file)],
                    capture_output=True, pass_fds=(r_fd,),
                )
                os.close(r_fd)

                if r.returncode != 0:
                    GLib.idle_add(self._log, "✘  Ошибка расшифровки — неверный пароль или повреждён файл\n")
                    ok = False
                else:
                    GLib.idle_add(self._log, "   Расшифровано, восстанавливаю...\n")
                    r2 = subprocess.run(
                        ["tar", "-xf", str(tar_path), "-C", str(tmp)],
                        capture_output=True,
                    )
                    if r2.returncode != 0:
                        GLib.idle_add(self._log, "✘  Ошибка распаковки архива\n")
                        ok = False
                    else:
                        tar_path.unlink()

            except Exception as e:
                GLib.idle_add(self._log, f"✘ Ошибка: {e}\n")
                ok = False

            if ok and tmp:
                def _done(ok2):
                    msg = "✔  Восстановление завершено!\n" if ok2 else "✘  Восстановление завершено с ошибками\n"
                    GLib.idle_add(self._log, msg)
                    if hasattr(win, "stop_progress"):
                        GLib.idle_add(win.stop_progress, ok2)
                    shutil.rmtree(tmp, ignore_errors=True)
                _restore_flatpak(tmp, self._log, _done)
            else:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)
                if hasattr(win, "stop_progress"):
                    GLib.idle_add(win.stop_progress, False)

        threading.Thread(target=_worker, daemon=True).start()

    def _do_restore(self, folder: Path):
        win = self.get_root()
        self._log(f"\n▶  Восстановление из {folder}...\n")
        if hasattr(win, "start_progress"):
            win.start_progress("Восстановление данных Flatpak...")

        def _done(ok):
            msg = "✔  Восстановление завершено!\n" if ok else "✘  Восстановление завершено с ошибками\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)

        _restore_flatpak(folder, self._log, _done)

    def _build_manage_group(self):
        group = Adw.PreferencesGroup()
        self._body.append(group)

        expander = Adw.ExpanderRow()
        expander.set_title("Обслуживание Flatpak")
        expander.set_expanded(False)
        group.add(expander)

        flathub_row = SettingRow(
            "application-x-addon-symbolic",
            "Подключить Flathub",
            "Устанавливает flatpak и flathub",
            "Включить", self._on_flathub, backend.is_flathub_enabled,
            "setting_flathub", "Активировано", self._on_flathub_undo, "Удалить",
        )
        expander.add_row(flathub_row)

        try:
            data = load_module("maintenance")
            tasks = data.get("tasks", [])
        except (OSError, Exception):
            tasks = []

        flatpak_ids = {"flatpak", "flatpak_repair", "flatpak_home"}
        for task in tasks:
            if task["id"] not in flatpak_ids:
                continue
            if task["id"] == "flatpak":
                task_row = ConfirmFlatpakRow(task, self._log)
            else:
                task_row = TaskRow(task, self._log, None)
            expander.add_row(task_row)


    def _on_flathub(self, row):
        row.set_working()
        self._log("\n▶  Установка Flatpak и Flathub...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Установка Flatpak...")

        def step2(ok):
            if not ok:
                row.set_done(False)
                return
            backend.run_privileged(
                ["apt-get", "install", "-y", "flatpak-repo-flathub"],
                self._log,
                lambda ok2: (
                    row.set_done(ok2),
                    self._log("✔  Flathub готов!\n" if ok2 else "✘  Ошибка\n"),
                    win.stop_progress(ok2) if hasattr(win, "stop_progress") else None,
                ),
            )

        backend.run_privileged(["apt-get", "install", "-y", "flatpak"], self._log, step2)

    def _on_flathub_undo(self, row):
        row.set_working()
        self._log("\n▶  Удаление Flatpak и Flathub...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Удаление Flatpak...")
        backend.run_privileged(
            ["apt-get", "remove", "-y", "flatpak", "flatpak-repo-flathub"],
            self._log,
            lambda ok: (
                row.set_undo_done(ok),
                self._log("✔  Flatpak удалён!\n" if ok else "✘  Ошибка\n"),
                win.stop_progress(ok) if hasattr(win, "stop_progress") else None,
            ),
        )

    def _on_update_all(self):
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Обновление всех Flatpak-приложений...")
        self._log("\n▶  Обновление всех Flatpak-приложений...\n")
        if self._update_all_btn:
            self._update_all_btn.set_sensitive(False)
        if self._refresh_btn:
            self._refresh_btn.set_sensitive(False)

        def _done_system(ok_sys):
            ok = ok_sys
            msg = "✔  Все Flatpak-приложения обновлены!\n" if ok else "⚠  Обновление завершено с ошибками\n"
            GLib.idle_add(self._log, msg)
            if hasattr(win, "stop_progress"):
                GLib.idle_add(win.stop_progress, ok)
            GLib.idle_add(self._refresh)

        def _done_user(ok_user):
            if not ok_user:
                GLib.idle_add(self._log, "⚠  Ошибки при обновлении пользовательских приложений\n")
            backend.run_privileged(["flatpak", "update", "-y", "--system"], self._log, _done_system)

        _run_user_op(["flatpak", "update", "-y", "--user"], self._log, _done_user)

