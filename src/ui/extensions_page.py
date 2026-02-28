"""Вкладка «Расширения» — GNOME Shell Extensions."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
from widgets import (
    make_button, make_scrolled_page, make_icon,
    make_status_icon, set_status_ok, set_status_error, clear_status, make_suffix_box,
)

# ── Рекомендуемые расширения: (uuid, название, описание) ─────────────────────

RECOMMENDED = [
    (
        "appindicatorsupport@rgcjonas.gmail.com",
        "AppIndicator and KStatusNotifierItem",
        "Поддержка системных лотков приложений в панели",
    ),
    (
        "Vitals@CoreCoding.com",
        "Vitals",
        "Мониторинг CPU, RAM и температуры в верхней панели",
    ),
    (
        "just-perfection-desktop@just-perfection",
        "Just Perfection",
        "Тонкая настройка элементов интерфейса GNOME Shell",
    ),
    (
        "dash-to-dock@micxgx.gmail.com",
        "Dash to Dock",
        "Dock как постоянная панель задач",
    ),
    (
        "dash-to-panel@jderose9.github.com",
        "Dash to Panel",
        "Полноценная панель задач в стиле Windows",
    ),
    (
        "blur-my-shell@aunetx",
        "Blur my Shell",
        "Эффект размытия для элементов интерфейса",
    ),
    (
        "auto-accent-colour@fthx",
        "Auto Accent Colour",
        "Автоматический цвет акцента под обои",
        "7502",
    ),
    (
        "rounded-window-corners@yilozt",
        "Rounded Window Corners Reborn",
        "Скругление углов окон и мониторов",
    ),
    (
        "pipewire-settings@tuxor1337",
        "Pipewire Settings",
        "Настройка частоты и буфера звука",
    ),
]

_USER_EXT_DIR   = Path.home() / ".local" / "share" / "gnome-shell" / "extensions"
_SYSTEM_EXT_DIR = Path("/usr/share/gnome-shell/extensions")


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _gext_path() -> str | None:
    """Возвращает путь к gext или None. Проверяет PATH и ~/.local/bin (после pip install --user)."""
    if cmd := shutil.which("gext"):
        return cmd
    local_bin = Path.home() / ".local" / "bin" / "gext"
    if local_bin.exists():
        return str(local_bin)
    return None


def _is_ext_installed(uuid: str) -> bool:
    """Проверяет установлено ли расширение (user или system)."""
    try:
        r = subprocess.run(["gnome-extensions", "list"], capture_output=True, text=True)
        return uuid in r.stdout
    except Exception:
        return False


def _read_extensions_from(ext_dir: Path) -> list[tuple[str, str]]:
    """Читает [(uuid, name), ...] из metadata.json в директории расширений."""
    result = []
    for meta in sorted(ext_dir.glob("*/metadata.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            uuid = data.get("uuid", meta.parent.name)
            name = data.get("name", uuid)
            result.append((uuid, name))
        except Exception:
            pass
    return result


def _get_enabled_uuids() -> set[str]:
    """Возвращает множество UUID включённых расширений."""
    try:
        r = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            capture_output=True, text=True,
        )
        return set(r.stdout.split())
    except Exception:
        return set()


# ── Главный класс ─────────────────────────────────────────────────────────────

class ExtensionsPage(Gtk.Box):
    """Вкладка «Расширения»: менеджер, рекомендуемые, установка по ID, список."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        scroll, self._body = make_scrolled_page()
        self.append(scroll)

        self._build_search_group()
        self._search_results_group = None
        self._installed_group = None
        self._build_installed_group()

    # ── Секция: Поиск и установка ─────────────────────────────────────────────

    def _build_search_group(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self._body.append(box)

        self._id_status = make_status_icon()

        self._id_entry = Gtk.Entry()
        self._id_entry.set_valign(Gtk.Align.CENTER)
        self._id_entry.set_placeholder_text("Поиск расширений или ID...")
        self._id_entry.set_hexpand(True)
        self._id_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "edit-clear-symbolic")
        self._id_entry.connect("icon-press", lambda e, p, *_: e.set_text("") if p == Gtk.EntryIconPosition.SECONDARY else None)
        self._id_entry.connect("activate", self._on_search_activate)
        self._id_entry.connect("notify::text", self._on_search_text_changed)

        self._id_btn = make_button("Найти", width=110)
        self._id_btn.connect("clicked", self._on_search_activate)

        box.append(self._id_status)
        box.append(self._id_entry)
        box.append(self._id_btn)

    def _on_search_text_changed(self, entry, _):
        """Скрывает результаты поиска, если поле ввода очищено."""
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

    def _install_by_id(self, ext_id):
        self._id_btn.set_sensitive(False)
        self._id_btn.set_label("…")
        clear_status(self._id_status)
        self._log(f"\n▶  Установка расширения {ext_id}...\n")

        def _do():
            gext = _gext_path()
            if not gext:
                GLib.idle_add(self._log, "▶  gext не найден, устанавливаю...\n")
                r_pip = subprocess.run(
                    ["pip3", "install", "gnome-extensions-cli", "--user"],
                    capture_output=True, text=True,
                )
                if r_pip.returncode != 0:
                    GLib.idle_add(self._log, f"✘  Не удалось установить gext: {r_pip.stderr.strip()}\n")
                    GLib.idle_add(set_status_error, self._id_status)
                    GLib.idle_add(self._id_btn.set_label, "Установить")
                    GLib.idle_add(self._id_btn.set_sensitive, True)
                    return
                GLib.idle_add(self._log, "✔  gext установлен!\n")
                gext = _gext_path() or "gext"

            r = subprocess.run([gext, "install", ext_id], capture_output=True, text=True)
            if r.stdout:
                GLib.idle_add(self._log, r.stdout)
            ok = r.returncode == 0
            if ok:
                GLib.idle_add(set_status_ok, self._id_status)
                GLib.idle_add(self._id_entry.set_text, "")
                GLib.idle_add(self._log, "✔  Расширение установлено!\n")
                GLib.idle_add(self._refresh_installed)
            else:
                GLib.idle_add(set_status_error, self._id_status)
                GLib.idle_add(self._log, f"✘  Ошибка: {r.stderr.strip()}\n")
            GLib.idle_add(self._id_btn.set_label, "Найти")
            GLib.idle_add(self._id_btn.set_sensitive, True)

        threading.Thread(target=_do, daemon=True).start()

    def _search_extensions(self, query):
        self._id_btn.set_sensitive(False)
        self._id_btn.set_label("Поиск...")
        clear_status(self._id_status)
        
        # Очищаем предыдущие результаты
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
                
                # Получаем список установленных UUID
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
            
            GLib.idle_add(self._id_btn.set_label, "Найти")
            GLib.idle_add(self._id_btn.set_sensitive, True)

        threading.Thread(target=_do, daemon=True).start()

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
        
        # Вставляем группу сразу после группы поиска (индекс 1, т.к. 0 это группа поиска)
        # Но так как мы используем append/remove, проще вставить перед installed_group
        if self._installed_group:
            # Находим виджет перед installed_group
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
                installed=is_installed
            )
            group.add(row)

    # ── Секция: Установленные расширения ──────────────────────────────────────

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
        # Пересоздаём группу — Adw.PreferencesGroup не поддерживает remove строк
        self._body.remove(self._installed_group)
        self._installed_group = self._make_installed_group_widget()
        self._body.append(self._installed_group)

        # Фильтруем системные расширения, которые перекрыты пользовательскими (дубликаты)
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

        if user_exts or missing_recs:
            exp = Adw.ExpanderRow()
            exp.set_title("Пользовательские")
            count_str = f"{len(user_exts)} уст."
            if missing_recs:
                count_str += f", {len(missing_recs)} реком."
            exp.set_subtitle(count_str)
            exp.set_expanded(True)
            for uuid, name in user_exts:
                exp.add_row(self._make_installed_row(uuid, name, uuid in enabled, is_user=True))
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
            for uuid, name in visible_system_exts:
                exp.add_row(self._make_installed_row(uuid, name, uuid in enabled, is_user=False))
            self._installed_group.add(exp)

    def _make_installed_row(self, uuid: str, name: str, enabled: bool, is_user: bool = False) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(name)
        row.set_subtitle(uuid)
        row.add_prefix(make_icon("application-x-addon-symbolic"))

        switch = Gtk.Switch()
        switch.set_active(enabled)
        switch.set_valign(Gtk.Align.CENTER)

        def on_state_set(sw, state, u=uuid):
            self._toggle_extension(u, state, sw)
            return True  # Блокируем авто-смену; обновляем вручную после команды

        switch.connect("state-set", on_state_set)

        suffix_widgets: list = [switch]

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
        suffix_widgets.append(del_btn)

        row.add_suffix(make_suffix_box(*suffix_widgets))
        return row

    def _make_recommended_row(self, uuid, name, desc, install_id=None, installed=False):
        row = Adw.ActionRow()
        row.set_title(name)
        row.set_subtitle(desc)
        row.add_prefix(make_icon("application-x-addon-symbolic"))

        status = make_status_icon()
        
        if installed:
            set_status_ok(status)
            btn = make_button("Установлено")
            btn.set_sensitive(False)
            btn.add_css_class("flat")
        else:
            btn = make_button("Установить")
            btn.connect("clicked", lambda _, u=uuid, b=btn, s=status, iid=install_id: self._on_install_ext(u, b, s, iid))
            
        row.add_suffix(make_suffix_box(status, btn))
        return row

    def _on_install_ext(self, uuid, btn, status, install_id=None):
        btn.set_sensitive(False)
        btn.set_label("…")
        
        # Поддержка установки через EPM (для системных пакетов)
        if install_id and install_id.startswith("epm:"):
            pkg = install_id[4:]
            self._log(f"\n▶  Установка {pkg} (EPM)...\n")
            def _done(ok):
                if ok:
                    GLib.idle_add(self._log, "✔  Установлено!\n")
                    GLib.idle_add(self._refresh_installed)
                else:
                    GLib.idle_add(self._log, f"✘  Ошибка установки {pkg}\n")
                    GLib.idle_add(set_status_error, status)
                    GLib.idle_add(btn.set_label, "Повторить")
                    GLib.idle_add(btn.set_sensitive, True)
            backend.run_epm(["epm", "-i", "-y", pkg], self._log, _done)
            return

        self._log(f"\n▶  Установка {uuid}...\n")

        def _do():
            gext = _gext_path()
            if not gext:
                GLib.idle_add(self._log, "▶  gext не найден, устанавливаю...\n")
                r_pip = subprocess.run(
                    ["pip3", "install", "gnome-extensions-cli", "--user"],
                    capture_output=True, text=True,
                )
                if r_pip.returncode != 0:
                    GLib.idle_add(self._log, f"✘  Не удалось установить gext: {r_pip.stderr.strip()}\n")
                    GLib.idle_add(set_status_error, status)
                    GLib.idle_add(btn.set_label, "Повторить")
                    GLib.idle_add(btn.set_sensitive, True)
                    return
                GLib.idle_add(self._log, "✔  gext установлен!\n")
                gext = _gext_path() or "gext"

            target = install_id if install_id else uuid
            r = subprocess.run([gext, "install", target], capture_output=True, text=True)
            if r.stdout:
                GLib.idle_add(self._log, r.stdout)
            ok = r.returncode == 0
            
            if ok:
                GLib.idle_add(self._log, "✔  Установлено!\n")
                GLib.idle_add(self._refresh_installed)
            else:
                GLib.idle_add(self._log, f"✘  Ошибка: {r.stderr.strip()}\n")
                GLib.idle_add(set_status_error, status)
                GLib.idle_add(btn.set_label, "Повторить")
                GLib.idle_add(btn.set_sensitive, True)

        threading.Thread(target=_do, daemon=True).start()

    def _toggle_extension(self, uuid: str, state: bool, switch: Gtk.Switch) -> None:
        """Включает или выключает расширение через gnome-extensions."""
        cmd = ["gnome-extensions", "enable" if state else "disable", uuid]

        def _do():
            r = subprocess.run(cmd, capture_output=True, text=True)
            ok = r.returncode == 0
            if ok:
                GLib.idle_add(switch.set_state, state)
                action = "включено" if state else "выключено"
                GLib.idle_add(self._log, f"✔  {uuid.split('@')[0]} {action}\n")
            else:
                GLib.idle_add(self._log, f"✘  Ошибка: {r.stderr.strip()}\n")

        threading.Thread(target=_do, daemon=True).start()

    def _on_delete_ext(self, uuid: str, is_user: bool = True) -> None:
        """Диалог подтверждения перед удалением расширения."""
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
        """Удаляет пользовательское расширение (shutil.rmtree)."""
        ext_path = _USER_EXT_DIR / uuid
        self._log(f"\n▶  Удаление {uuid}...\n")

        def _do():
            try:
                shutil.rmtree(ext_path)
                GLib.idle_add(self._log, f"✔  {uuid} удалён!\n")
                GLib.idle_add(self._refresh_installed)
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка удаления: {e}\n")

        threading.Thread(target=_do, daemon=True).start()

    def _do_delete_system_ext(self, uuid: str) -> None:
        """Удаляет системное расширение с проверкой RPM-зависимостей."""
        ext_path = _SYSTEM_EXT_DIR / uuid
        self._log(f"\n▶  Проверка зависимостей для {uuid}...\n")

        def _do():
            # Определяем, принадлежит ли директория RPM-пакету
            r_own = subprocess.run(
                ["rpm", "-qf", str(ext_path)],
                capture_output=True, text=True,
            )

            if r_own.returncode == 0:
                pkg_name = r_own.stdout.strip().splitlines()[0]
                GLib.idle_add(self._log, f"▶  Пакет RPM: {pkg_name}\n")

                # Проверяем что зависит от этого пакета
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
                    GLib.idle_add(
                        self._log,
                        f"✘  Удаление невозможно — от «{pkg_name}» зависят:\n"
                        + "\n".join(f"    • {d}" for d in deps_out.splitlines())
                        + "\n",
                    )
                    return

                # Зависимостей нет — удаляем пакет через rpm -e
                GLib.idle_add(self._log, f"▶  Удаляю пакет {pkg_name}...\n")
                ok = backend.run_privileged_sync(["rpm", "-e", pkg_name], self._log)
            else:
                # Директория не принадлежит RPM — удаляем напрямую через sudo rm -rf
                GLib.idle_add(self._log, "▶  Директория не принадлежит RPM, удаляю rm -rf...\n")
                ok = backend.run_privileged_sync(["rm", "-rf", str(ext_path)], self._log)

            if ok:
                GLib.idle_add(self._log, f"✔  {uuid} удалён!\n")
                GLib.idle_add(self._refresh_installed)
            else:
                GLib.idle_add(self._log, "✘  Ошибка удаления\n")

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_installed(self):
        threading.Thread(target=self._load_installed, daemon=True).start()
