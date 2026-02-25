"""Вкладка «Приложения» — modules/apps.json + CRUD."""

import json
import shutil
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend
from widgets import make_button, make_scrolled_page
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
        self._json_path = _MODULES_DIR / "apps.json"
        self._data = {}

        scroll, body = make_scrolled_page()
        self._body = body
        self.append(scroll)

        self._btn_all = make_button("Установить все недостающие")
        self._btn_all.set_halign(Gtk.Align.CENTER)
        self._btn_all.connect("clicked", self._run_all)
        body.append(self._btn_all)

        self._load_and_build()
        GLib.idle_add(self._refresh_btn_all)

    def _load_and_build(self):
        try:
            with open(self._json_path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._data = {"groups": []}
        self._build()

    def _build(self):
        child = self._body.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if child is not self._btn_all:
                self._body.remove(child)
            child = nxt
        self._rows.clear()

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
                src = dict(app["source"])
                chk = src.get("check", [])
                src["check"] = tuple(chk) if isinstance(chk, list) else chk

                # Пропускаем epm-приложения если epm недоступен
                if src.get("cmd") and src["cmd"][0] == "epm" and not shutil.which("epm"):
                    continue

                app_n = dict(app, source=src)
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
        GLib.idle_add(self._rebuild)

    def _do_delete(self, item, group_id):
        for g in self._data.get("groups", []):
            if g.get("id") == group_id:
                g["items"] = [it for it in g.get("items", []) if it.get("id") != item.get("id")]
                break
        self._write_json()
        GLib.idle_add(self._rebuild)

    def _rebuild(self):
        self._build()
        self._refresh_btn_all()

    def _write_json(self):
        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self._log(f"\n✘  Ошибка сохранения apps.json: {e}\n")

    # ── Массовая установка ───────────────────────────────────────────────────

    def _refresh_btn_all(self):
        has_missing = any(not r.is_installed() for r in self._rows)
        self._btn_all.set_sensitive(has_missing)
        if has_missing:
            self._btn_all.set_label("Установить все недостающие")
            self._btn_all.add_css_class("suggested-action")
            self._btn_all.remove_css_class("flat")
        else:
            self._btn_all.set_label("✅ Все приложения установлены")
            self._btn_all.remove_css_class("suggested-action")
            self._btn_all.add_css_class("flat")

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
        if backend.is_system_busy():
            self._log("\n⚠  Система занята.\n")
            return
        self._busy = True
        self._btn_all.set_sensitive(False)
        self._btn_all.set_label("⏳  Установка...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        for row in (r for r in self._rows if not r.is_installed()):
            GLib.idle_add(row._on_install)
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
