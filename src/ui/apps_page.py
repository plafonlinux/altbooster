"""Вкладка «Приложения» — modules/apps.json + CRUD."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
import config
from widgets import (
    make_button, make_scrolled_page,
    make_status_icon, set_status_ok, set_status_error, clear_status, make_suffix_box,
)
from ui.common import load_module, _MODULES_DIR
from ui.dialogs import AppEditDialog
from ui.rows import AppRow


class AppsPage(Gtk.Box):
    """Вкладка «Приложения» — modules/apps.json + CRUD."""

    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        self._rows = []
        self._busy = False
        self._system_json_path = _MODULES_DIR / "apps.json"
        self._json_path = config.CONFIG_DIR / "apps.json"
        self._data = {}

        scroll, body = make_scrolled_page()
        self._body = body
        self.append(scroll)

        self._btns_box = Gtk.CenterBox()
        self._body.append(self._btns_box)

        self._btn_all = make_button("Установить всё")
        self._btn_all.add_css_class("success")
        self._btn_all.connect("clicked", self._on_install_all_clicked)

        start_box = Gtk.Box()
        start_box.set_halign(Gtk.Align.START)
        start_box.append(self._btn_all)
        self._btns_box.set_start_widget(start_box)

        # Поиск пакетов (Center)
        self._pkg_search_groups = []
        search_box = Gtk.Box(spacing=6)

        self._branch_combo = Gtk.DropDown()
        self._branch_combo.set_model(Gtk.StringList.new(["p11", "Sisyphus", "epm play", "Flathub"]))
        self._epm_play_cache = None
        self._branch_combo.set_selected(0)
        self._branch_combo.set_valign(Gtk.Align.CENTER)
        self._branch_combo.set_tooltip_text("Источник для поиска пакетов")
        search_box.append(self._branch_combo)

        self._search_entry = Gtk.Entry()
        self._search_entry.set_hexpand(True)
        self._search_entry.set_placeholder_text("Поиск пакетов ALT...")
        self._search_entry.set_valign(Gtk.Align.CENTER)
        self._search_entry.connect("activate", self._on_pkg_search)
        self._search_entry.connect("notify::text", self._on_search_text_changed)
        search_box.append(self._search_entry)

        self._search_status = make_status_icon()
        search_box.append(self._search_status)

        self._btns_box.set_center_widget(search_box)

        self._btn_reset = Gtk.Button(label="Вернуть стандартный список")
        self._btn_reset.set_tooltip_text("Сбросить список к стандартному (обновить)")
        self._btn_reset.add_css_class("destructive-action")
        self._btn_reset.add_css_class("pill")
        self._btn_reset.connect("clicked", self._on_factory_reset)

        end_box = Gtk.Box()
        end_box.set_halign(Gtk.Align.END)
        end_box.append(self._btn_reset)
        self._btns_box.set_end_widget(end_box)

        self._load_and_build()
        GLib.idle_add(self._refresh_btn_all)

    def _update_reset_button_ui(self, is_default):
        """Обновляет кнопку сброса в зависимости от того, стандартный ли список."""
        if is_default:
            self._btn_reset.set_label("✓ Список по умолчанию")
            self._btn_reset.set_tooltip_text("Список приложений соответствует стандартному")
            self._btn_reset.remove_css_class("destructive-action")
            self._btn_reset.add_css_class("success")
            self._btn_reset.set_sensitive(False)
        else:
            self._btn_reset.set_label("Вернуть стандартный список")
            self._btn_reset.set_tooltip_text("Сбросить список к стандартному (обновить)")
            self._btn_reset.remove_css_class("success")
            self._btn_reset.add_css_class("destructive-action")
            self._btn_reset.set_sensitive(True)

    def _on_search_text_changed(self, entry, _):
        if not entry.get_text():
            self._clear_pkg_search_results()

    def _on_pkg_search(self, *_):
        text = self._search_entry.get_text().strip()
        if not text:
            return
        self._search_entry.set_sensitive(False)
        self._branch_combo.set_sensitive(False)
        clear_status(self._search_status)
        self._clear_pkg_search_results()
        branch_map = {0: "p11", 1: "sisyphus", 2: "epm_play", 3: "flathub"}
        branch = branch_map.get(self._branch_combo.get_selected(), "p11")
        threading.Thread(target=self._do_pkg_search, args=(text, branch), daemon=True).start()

    def _fetch_from_source(self, query, branch):
        if branch == "flathub":
            return self._fetch_flathub(query)
        if branch == "epm_play":
            return self._search_epm_play(query)
        params = urllib.parse.urlencode({"name": query, "branch": branch})
        url = f"https://rdb.altlinux.org/api/site/find_packages?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for pkg in data.get("packages", []):
            versions = pkg.get("versions", [])
            version = versions[0].get("version", "") if versions else ""
            results.append({
                "display_name": pkg.get("name", ""),
                "install_id": pkg.get("name", ""),
                "summary": pkg.get("summary") or "",
                "version": version,
                "install_type": "epm",
                "branch": branch,
            })
        return results

    def _check_installed(self, install_id, install_type):
        try:
            if install_type == "flatpak":
                r = subprocess.run(["flatpak", "info", install_id], capture_output=True, timeout=5)
            else:
                r = subprocess.run(["rpm", "-q", install_id], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _do_pkg_search(self, query, branch):
        primary = []
        try:
            primary = self._fetch_from_source(query, branch)
            for pkg in primary:
                pkg["installed"] = self._check_installed(pkg["install_id"], pkg["install_type"])
        except Exception:
            pass

        if primary:
            GLib.idle_add(self._display_pkg_results, [(branch, primary)], False)
        else:
            GLib.idle_add(self._log, "ℹ Не найдено в выбранном источнике, ищу в других...\n")
            all_branches = ["p11", "sisyphus", "epm_play", "flathub"]
            fallback = []
            for ob in all_branches:
                if ob == branch:
                    continue
                try:
                    r = self._fetch_from_source(query, ob)
                    for pkg in r:
                        pkg["installed"] = self._check_installed(pkg["install_id"], pkg["install_type"])
                    if r:
                        fallback.append((ob, r))
                except Exception:
                    pass
            GLib.idle_add(self._display_pkg_results, fallback, True)
        
        GLib.idle_add(self._search_entry.set_sensitive, True)
        GLib.idle_add(self._branch_combo.set_sensitive, True)

    def _fetch_flathub(self, query):
        body = json.dumps({"query": query, "locale": "ru"}).encode()
        req = urllib.request.Request(
            "https://flathub.org/api/v2/search",
            data=body,
            headers={"User-Agent": "ALTBooster/1.0", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return [
            {
                "display_name": hit.get("name") or hit.get("app_id", ""),
                "install_id": hit.get("app_id", ""),
                "summary": hit.get("summary") or "",
                "version": "",
                "install_type": "flatpak",
                "branch": "flathub",
            }
            for hit in data.get("hits", [])
        ]

    def _load_epm_play_list(self):
        try:
            r = subprocess.run(["epm", "play"], capture_output=True, text=True, timeout=30)
            apps = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or " - " not in line:
                    continue
                name, _, desc = line.partition(" - ")
                name = name.strip()
                desc = desc.strip()
                if name:
                    apps.append((name, desc))
            return apps
        except Exception:
            return []

    def _search_epm_play(self, query):
        if self._epm_play_cache is None:
            self._epm_play_cache = self._load_epm_play_list()
        q = query.lower()
        return [
            {
                "display_name": name,
                "install_id": name,
                "summary": desc,
                "version": "",
                "install_type": "epm_play",
                "branch": "epm_play",
            }
            for name, desc in self._epm_play_cache
            if q in name.lower() or q in desc.lower()
        ]

    def _display_pkg_results(self, branch_results_list, is_fallback=False):
        if not branch_results_list:
            set_status_error(self._search_status)
            self._log("ℹ Пакеты не найдены ни в одном источнике.\n")
            return
        set_status_ok(self._search_status)
        label_map = {"flathub": "Flathub", "p11": "p11", "sisyphus": "Sisyphus", "epm_play": "EPM Play"}
        title_prefix = "Найдено в" if is_fallback else "Результаты в"
        prev = self._btns_box
        for branch, results in branch_results_list:
            label = label_map.get(branch, branch)
            group = Adw.PreferencesGroup()
            group.set_title(f"{title_prefix} {label} ({len(results)})")
            self._body.insert_child_after(group, prev)
            self._pkg_search_groups.append(group)
            prev = group
            for pkg in results:
                display_name = pkg["display_name"]
                install_id = pkg["install_id"]
                summary = pkg.get("summary", "")[:120]
                version = pkg.get("version", "")
                install_type = pkg.get("install_type", "epm")
                installed = pkg.get("installed", False)
                if version and summary:
                    subtitle = f"v{version} — {summary}"
                elif summary:
                    subtitle = summary
                elif install_type == "flatpak":
                    subtitle = install_id
                else:
                    subtitle = version
                row = Adw.ActionRow()
                row.set_title(display_name)
                if subtitle:
                    row.set_subtitle(subtitle)
                status = make_status_icon()
                if installed:
                    set_status_ok(status)
                    inst_btn = make_button("Установлено", width=110)
                    inst_btn.set_sensitive(False)
                    inst_btn.add_css_class("flat")
                    row.add_suffix(make_suffix_box(status, inst_btn))
                else:
                    in_list = self._is_in_list(install_id)
                    add_btn = Gtk.Button(label="В списке" if in_list else "Добавить")
                    add_btn.add_css_class("flat")
                    add_btn.add_css_class("pill")
                    add_btn.set_valign(Gtk.Align.CENTER)
                    if in_list:
                        add_btn.set_sensitive(False)
                    else:
                        add_btn.connect(
                            "clicked",
                            lambda _, p=pkg, b=add_btn: self._add_pkg_to_list(p, b),
                        )
                    inst_btn = make_button("Установить", width=100)
                    inst_btn.connect(
                        "clicked",
                        lambda _, iid=install_id, itype=install_type, b=inst_btn, s=status:
                            self._install_pkg(iid, itype, b, s),
                    )
                    row.add_suffix(make_suffix_box(status, add_btn, inst_btn))
                group.add(row)

    def _clear_pkg_search_results(self):
        for group in self._pkg_search_groups:
            try:
                self._body.remove(group)
            except Exception:
                pass
        self._pkg_search_groups = []
        clear_status(self._search_status)

    def _is_in_list(self, install_id):
        """Проверяет, есть ли пакет с таким install_id уже в apps.json."""
        # Проверяем по ID приложения или по команде установки
        for g in self._data.get("groups", []):
            for item in g.get("items", []):
                if item.get("id") == install_id.replace(".", "_").replace("-", "_").replace(" ", "_").lower():
                    return True
                for src in item.get("sources", []):
                    if install_id in src.get("cmd", []):
                        return True
                # старый формат source (не sources)
                src = item.get("source")
                if src and install_id in src.get("cmd", []):
                    return True
        return False

    def _add_pkg_to_list(self, pkg, btn_add):
        """Добавляет пакет из поиска в apps.json без установки."""
        display_name = pkg["display_name"]
        install_id = pkg["install_id"]
        install_type = pkg["install_type"]
        branch = pkg.get("branch")
        summary = pkg.get("summary", "")[:100]

        # Хаки для конкретных пакетов (переопределение типа установки)
        lid = install_id.lower()
        if lid in ("furmark", "occt", "yandex-browser", "chrome", "google-chrome-stable"):
            install_type = "epm_play"
            branch = "epm_play"
        elif lid in ("firefox", "yandex-browser-stable"):
            install_type = "epm"
            # Если вдруг ветка не определена (или пришла не из p11/sisyphus), ставим p11 для метки
            if branch not in ("p11", "Sisyphus"):
                branch = "p11"

        if install_type == "flatpak":
            source = {
                "label": "Flathub",
                "cmd": ["flatpak", "install", "-y", "flathub", install_id],
                "check": ["flatpak", install_id],
            }
        elif install_type == "epm_play":
            check_type = "which" if install_id.lower() == "occt" else "rpm"
            source = {
                "label": "EPM Play",
                "cmd": ["epm", "play", install_id],
                "check": [check_type, install_id],
            }
        else:
            # Для epm install используем название ветки
            label = branch if branch in ["p11", "Sisyphus"] else "EPM install"
            source = {
                "label": label,
                "cmd": ["epm", "-i", "-y", install_id],
                "check": ["rpm", install_id],
            }

        item_id = install_id.replace(".", "_").replace("-", "_").replace(" ", "_").lower()

        gs = self._data.get("groups", [])
        if not gs:
            self._log("⚠ Нет групп для добавления.\n")
            return

        # Ищем, существует ли уже такое приложение
        target_item = None
        for g in gs:
            for it in g.get("items", []):
                if it.get("id") == item_id:
                    target_item = it
                    break
            if target_item:
                break
        
        if target_item:
            # Если есть — добавляем источник к нему
            if "sources" not in target_item:
                target_item["sources"] = [target_item["source"]] if "source" in target_item else []
                if "source" in target_item: del target_item["source"]
            
            # Проверяем дубликаты источников
            exists = any(s.get("label") == source["label"] and s.get("cmd") == source["cmd"] for s in target_item["sources"])
            if not exists:
                target_item["sources"].append(source)
                self._log(f"✔ Источник «{source['label']}» добавлен к «{target_item['label']}»\n")
                self._write_json()
                GLib.idle_add(self._load_and_build) # Перезагружаем UI, чтобы обновить бейджики
            else:
                self._log(f"ℹ Источник уже существует у «{target_item['label']}»\n")
            
            btn_add.set_label("Обновлено")
        else:
            # Если нет — создаем новое
            item = {"id": item_id, "label": display_name, "desc": summary, "sources": [source]}
            gs[0].setdefault("items", []).append(item)
            self._write_json()
            GLib.idle_add(self._load_and_build)
            
            btn_add.set_label("Добавлено")
            self._log(f"✔ {display_name} добавлен в список\n")
            
        btn_add.set_sensitive(False)

    def _install_pkg(self, pkg_id, install_type, btn, status):
        btn.set_sensitive(False)
        btn.set_label("…")
        self._log(f"\n▶  Установка {pkg_id}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Установка {pkg_id}...")
        done_cb = lambda ok: self._pkg_install_done(ok, pkg_id, btn, status)
        if install_type == "flatpak":
            backend.run_privileged(
                ["flatpak", "install", "flathub", "-y", pkg_id],
                self._log, done_cb,
            )
        elif install_type == "epm_play":
            backend.run_epm(["epm", "play", pkg_id], self._log, done_cb)
        else:
            backend.run_epm(["epm", "install", "-y", pkg_id], self._log, done_cb)

    def _pkg_install_done(self, ok, pkg_id, btn, status):
        win = self.get_root()
        if ok:
            set_status_ok(status)
            btn.set_label("Установлено")
            self._log(f"✔  {pkg_id} установлен!\n")
        else:
            set_status_error(status)
            btn.set_label("Повторить")
            btn.set_sensitive(True)
            self._log(f"✘  Ошибка установки {pkg_id}\n")
        if hasattr(win, "stop_progress"): win.stop_progress(ok)

    def _clear_body(self):
        """Очищает все виджеты со страницы, кроме панели кнопок."""
        child = self._body.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if child is not self._btns_box:
                self._body.remove(child)
            child = nxt
        self._rows.clear()
        self._pkg_search_groups = []

    def _load_and_build(self):
        self._clear_body()
        is_default = False
        try:
            # Если пользовательского файла нет, копируем системный
            if not self._json_path.exists():
                if self._system_json_path.exists():
                    os.makedirs(self._json_path.parent, exist_ok=True)
                    shutil.copy(self._system_json_path, self._json_path)
                    is_default = True
                else:
                    self._log(f"✘ Ошибка: Файл конфигурации не найден: {self._system_json_path}\n")
                    self._data = {"groups": []}
                    self._update_reset_button_ui(False)
                    self._build()
                    return

            # Загружаем оба файла для сравнения и работы
            user_data = {}
            system_data = {}
            with open(self._json_path, encoding="utf-8") as f:
                user_data = json.load(f)
            if self._system_json_path.exists():
                with open(self._system_json_path, encoding="utf-8") as f_sys:
                    system_data = json.load(f_sys)
            if user_data == system_data:
                is_default = True

            self._data = user_data

            if not isinstance(self._data, dict):
                raise ValueError("JSON должен быть объектом (dict)")
            # Создаём бэкап при первой успешной загрузке
            backup_path = self._json_path.with_suffix(".json.bak")
            if not backup_path.exists():
                shutil.copy(self._json_path, backup_path)
            self._update_reset_button_ui(is_default)
            self._build()
        except json.JSONDecodeError as e:
            self._log(f"✘ Ошибка синтаксиса в apps.json: {e}\n   Проверьте строку {e.lineno}, позицию {e.colno}.\n")
            self._data = {"groups": []}
            self._update_reset_button_ui(False)
            self._add_error_widgets()
        except (OSError, ValueError) as e:
            self._log(f"✘ Ошибка загрузки приложений: {e}\n")
            self._data = {"groups": []}
            self._update_reset_button_ui(False)

    def _build(self):
        groups = self._data.get("groups", [])
        if not groups:
            self._log("⚠ Список групп приложений пуст или не загружен.\n")

        for gdata in self._data.get("groups", []):
            pg = Adw.PreferencesGroup()
            self._body.append(pg)

            exp = Adw.ExpanderRow()
            exp.set_title(gdata.get("title", ""))
            exp.set_subtitle(f"Доступно приложений: {len(gdata.get('items', []))}")
            exp.set_expanded(False)
            pg.add(exp)

            add_btn = Gtk.Button()
            add_btn.set_icon_name("list-add-symbolic")
            add_btn.set_tooltip_text("Добавить приложение в эту группу")
            add_btn.add_css_class("flat")
            gid = gdata.get("id", "")
            add_btn.connect("clicked", lambda _b, g=gid: self._on_add(g))
            exp.add_suffix(add_btn)

            for app in gdata.get("items", []):
                # Нормализация для AppRow
                sources = []
                if "sources" in app:
                    sources = app["sources"]
                elif "source" in app:
                    sources = [app["source"]]
                
                # Валидация источников (tuple check)
                for s in sources:
                    chk = s.get("check", [])
                    s["check"] = tuple(chk) if isinstance(chk, list) else chk

                app_n = dict(app, sources=sources)
                row = AppRow(app_n, self._log, self._refresh_btn_all)
                self._rows.append(row)
                exp.add_row(row)

                gid2 = gdata.get("id", "")

                edit_btn = Gtk.Button()
                edit_btn.set_icon_name("document-edit-symbolic")
                edit_btn.set_tooltip_text("Редактировать")
                edit_btn.set_valign(Gtk.Align.CENTER)
                edit_btn.add_css_class("flat")
                edit_btn.add_css_class("circular")
                edit_btn.connect("clicked", lambda _b, a=app, g=gid2: self._on_edit(a, g))
                row.add_suffix(edit_btn)

                del_btn = Gtk.Button()
                del_btn.set_icon_name("list-remove-symbolic")
                del_btn.set_tooltip_text("Убрать из списка")
                del_btn.set_valign(Gtk.Align.CENTER)
                del_btn.add_css_class("flat")
                del_btn.add_css_class("circular")
                del_btn.connect("clicked", lambda _b, a=app, g=gid2: self._on_delete(a, g))
                row.add_suffix(del_btn)

    def _add_error_widgets(self):
        """Добавляет виджеты с сообщением об ошибке и кнопкой сброса."""
        group = Adw.PreferencesGroup()
        group.set_title("Ошибка конфигурации")

        row = Adw.ActionRow()
        row.set_title("Не удалось загрузить список приложений")
        row.set_subtitle("Файл apps.json повреждён. Вы можете исправить его вручную или сбросить к последней рабочей версии.")
        row.add_prefix(Gtk.Image.new_from_icon_name("dialog-error-symbolic"))

        reset_btn = make_button("Сбросить", style="destructive-action")
        reset_btn.connect("clicked", self._on_reset_apps_json)
        row.add_suffix(reset_btn)

        group.add(row)
        self._body.append(group)
        self._log("⚠ Список групп приложений пуст или не загружен.\n")

    def _on_reset_apps_json(self, _):
        """Обработчик сброса apps.json из бэкапа."""
        backup_path = self._json_path.with_suffix(".json.bak")
        source_path = None

        if backup_path.exists():
            source_path = backup_path
        elif self._system_json_path.exists():
            source_path = self._system_json_path
        else:
            self._log("✘ Нет ни резервной копии, ни системного файла. Сброс невозможен.\n")
            return

        dialog = Adw.AlertDialog(
            heading="Сбросить список приложений?",
            body="Текущий файл apps.json будет заменён рабочей версией (из бэкапа или системной). Все изменения будут утеряны.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reset", "Сбросить")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "reset":
                shutil.copy(source_path, self._json_path)
                self._log("✔ Файл apps.json сброшен. Перезагрузка списка...\n")
                self._load_and_build()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _on_factory_reset(self, _):
        """Сброс к системному apps.json (обновление списка)."""
        dialog = Adw.AlertDialog(
            heading="Обновить список приложений?",
            body="Текущий список будет заменён на стандартный из новой версии программы. Ваши ручные изменения в списке будут потеряны.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reset", "Сбросить")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "reset":
                if self._system_json_path.exists():
                    shutil.copy(self._system_json_path, self._json_path)
                    shutil.copy(self._system_json_path, self._json_path.with_suffix(".json.bak"))
                    self._log("✔ Список приложений обновлён до стандартного.\n")
                    self._load_and_build()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def _group_ids(self):
        return [g.get("id", "") for g in self._data.get("groups", [])]

    def _group_titles(self):
        return [g.get("title", "") for g in self._data.get("groups", [])]

    def _on_add(self, group_id=""):
        if not group_id:
            gs = self._data.get("groups", [])
            group_id = gs[0]["id"] if gs else ""
        AppEditDialog(
            self.get_root(),
            lambda item, gid: self._save_item(item, gid, None),
            self._group_ids(), self._group_titles(),
            current_group=group_id,
        )

    def _on_edit(self, item, group_id):
        AppEditDialog(
            self.get_root(),
            lambda upd, gid: self._save_item(upd, gid, item.get("id")),
            self._group_ids(), self._group_titles(),
            existing_item=item, current_group=group_id,
        )

    def _on_delete(self, item, group_id):
        d = Adw.AlertDialog()
        d.set_heading("Убрать из списка?")
        d.set_body(f"«{item.get('label', '')}» будет удалён из apps.json.\n"
                    "Само приложение не удалится из системы.")
        d.add_response("cancel", "Отмена")
        d.add_response("delete", "Удалить")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: self._do_delete(item, group_id) if r == "delete" else None)
        d.present(self.get_root())

    def _save_item(self, item, group_id, existing_id):
        for g in self._data.get("groups", []):
            if g.get("id") != group_id:
                continue
            items = g.setdefault("items", [])
            if existing_id:
                for i, it in enumerate(items):
                    if it.get("id") == existing_id:
                        items[i] = item
                        break
                else:
                    items.append(item)
            else:
                ids = {it.get("id") for it in items}
                if item["id"] in ids:
                    item = dict(item, id=item["id"] + "_2")
                items.append(item)
            break
        self._write_json()
        GLib.idle_add(self._load_and_build)

    def _do_delete(self, item, group_id):
        for g in self._data.get("groups", []):
            if g.get("id") == group_id:
                g["items"] = [it for it in g.get("items", []) if it.get("id") != item.get("id")]
                break
        self._write_json()
        GLib.idle_add(self._load_and_build)

    def _write_json(self):
        try:
            # Атомарная запись: пишем во временный файл -> переименовываем
            # Это предотвращает повреждение файла (обнуление) при сбое во время записи
            fd, tmp_path = tempfile.mkstemp(dir=self._json_path.parent, suffix=".tmp", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except (OSError, TypeError) as e:
            self._log(f"\n✘  Ошибка сохранения apps.json: {e}\n")
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ── Массовая установка ───────────────────────────────────────────────────

    def _on_install_all_clicked(self, btn):
        dialog = Adw.AlertDialog(
            heading="Установить все приложения?",
            body=(
                "Будет выполнена установка всех приложений из списка.\n"
                "Основной источник — Flathub (требуется интернет). Это может занять продолжительное время."
            ),
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("install", "Установить")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "install":
                self._run_all(btn)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _refresh_btn_all(self):
        has_missing = any(not r.is_installed() for r in self._rows)
        self._btn_all.set_sensitive(has_missing)
        if has_missing:
            self._btn_all.set_label("Установить всё")
            self._btn_all.add_css_class("suggested-action")
            self._btn_all.add_css_class("success")
            self._btn_all.remove_css_class("flat")
        else:
            self._btn_all.set_label("✓ Все приложения установлены")
            self._btn_all.remove_css_class("suggested-action")
            self._btn_all.add_css_class("success")
            self._btn_all.remove_css_class("flat")

    def run_all_external(self, btn):
        dialog = Adw.AlertDialog(
            heading="Массовая установка приложений",
            body=(
                "Эта кнопка запустит фоновую установку абсолютно всех программ "
                "из вкладки «Приложения».\n\n"
                "Вы можете предварительно перейти на эту вкладку, чтобы удалить "
                "ненужный софт или добавить свои собственные программы "
                "(DEB, Flatpak, скрипты) через встроенный редактор.\n\n"
                "Начать массовую установку сейчас?"
            ),
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("install", "Установить всё")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "install":
                self._ext_btn = btn
                btn.set_sensitive(False)
                btn.set_label("⏳ Установка...")
                self._run_all(None)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _run_all(self, _):
        if self._busy:
            return
        self._busy = True
        self._cancel_install = False
        self._btn_all.set_sensitive(False)
        self._btn_all.set_label("⏳  Установка...")
        
        win = self.get_root()
        if hasattr(win, "start_progress"):
            win.start_progress("Массовая установка приложений...", self._cancel_all)
            
        threading.Thread(target=self._worker, daemon=True).start()

    def _cancel_all(self):
        self._cancel_install = True
        self._log("\n⚠  Запрос отмены. Завершаю текущую операцию и останавливаюсь...\n")

    def _worker(self):
        for row in (r for r in self._rows if not r.is_installed()):
            if self._cancel_install:
                break
            # Ждем, пока предыдущая установка (если была) сбросит флаг
            while row._installing:
                time.sleep(0.5)
            
            GLib.idle_add(row._on_install)
            
            # Даем время на запуск установки в основном потоке
            time.sleep(1.0)
            while row._installing:
                time.sleep(0.5)
        GLib.idle_add(self._done)

    def _done(self):
        self._busy = False
        self._refresh_btn_all()

        if hasattr(self, "_ext_btn") and self._ext_btn:
            self._ext_btn.set_sensitive(True)
            self._ext_btn.set_label("Запустить")
            self._ext_btn = None
        self._log("\n✔  Массовая установка завершена\n")

        win = self.get_root()
        if hasattr(win, "stop_progress"):
            ok = not getattr(self, "_cancel_install", False)
            win.stop_progress(ok)
