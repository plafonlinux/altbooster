"""Диалоговые окна: PasswordDialog, AppEditDialog."""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend


class PasswordDialog(Adw.AlertDialog):
    """Диалог ввода пароля sudo."""

    def __init__(self, parent, on_success, on_cancel):
        super().__init__(
            heading="Требуется пароль sudo",
            body=(
                "ALT Booster выполняет системные команды от имени root.\n"
                "Пароль сохраняется только на время сессии."
            ),
        )
        self._on_success = on_success
        self._on_cancel = on_cancel
        self._attempts = 0
        self._submitted = False

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

    def _on_response(self, _d, rid):
        if self._submitted:
            return
        if rid == "ok":
            self._submit()
        else:
            self._on_cancel()

    def _submit(self):
        pw = self._entry.get_text()
        if not pw:
            return
        self.set_response_enabled("ok", False)
        self._entry.set_sensitive(False)
        threading.Thread(
            target=lambda: GLib.idle_add(self._check_done, pw, backend.sudo_check(pw)),
            daemon=True,
        ).start()

    def _check_done(self, pw, ok):
        if ok:
            backend.set_sudo_password(pw)
            self._submitted = True
            self.close()
            self._on_success()
        else:
            self._attempts += 1
            self.set_body(f"❌ Неверный пароль (попытка {self._attempts}). Попробуйте снова.")
            self._entry.set_text("")
            self._entry.set_sensitive(True)
            self.set_response_enabled("ok", True)
            self._entry.grab_focus()


class AppEditDialog(Adw.PreferencesWindow):
    """Диалог добавления / редактирования приложения в apps.json."""

    _SOURCE_LABELS = ["Flathub", "EPM install", "EPM play", "APT", "Скрипт"]
    _SOURCE_KEYS   = ["flatpak", "epm_install", "epm_play", "apt", "script"]

    def __init__(self, parent, on_save, group_ids, group_titles,
                 existing_item=None, current_group=""):
        super().__init__()
        self._on_save = on_save
        self._existing = existing_item
        self._group_ids = group_ids
        self._group_titles = group_titles

        self.set_title("Редактировать" if existing_item else "Добавить приложение")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_search_enabled(False)

        page = Adw.PreferencesPage()
        self.add(page)

        # Группа
        grp_g = Adw.PreferencesGroup()
        grp_g.set_title("Категория")
        page.add(grp_g)
        self._group_row = Adw.ComboRow()
        self._group_row.set_title("Группа")
        gm = Gtk.StringList()
        for t in group_titles:
            gm.append(t)
        self._group_row.set_model(gm)
        if current_group in group_ids:
            self._group_row.set_selected(group_ids.index(current_group))
        grp_g.add(self._group_row)

        # Основные поля
        main_g = Adw.PreferencesGroup()
        main_g.set_title("Приложение")
        page.add(main_g)
        self._name_row = Adw.EntryRow()
        self._name_row.set_title("Название")
        main_g.add(self._name_row)
        self._desc_row = Adw.EntryRow()
        self._desc_row.set_title("Описание")
        main_g.add(self._desc_row)
        self._id_row = Adw.EntryRow()
        self._id_row.set_title("ID (латиница, без пробелов)")
        main_g.add(self._id_row)

        # Источник
        src_g = Adw.PreferencesGroup()
        src_g.set_title("Источник установки")
        page.add(src_g)
        self._type_row = Adw.ComboRow()
        self._type_row.set_title("Тип")
        tm = Gtk.StringList()
        for label in self._SOURCE_LABELS:
            tm.append(label)
        self._type_row.set_model(tm)
        src_g.add(self._type_row)
        self._pkg_row = Adw.EntryRow()
        self._pkg_row.set_title("Пакет / App ID")
        src_g.add(self._pkg_row)
        self._check_row = Adw.EntryRow()
        self._check_row.set_title("Check ID (если отличается от пакета)")
        src_g.add(self._check_row)

        # Кнопка сохранить
        btn_g = Adw.PreferencesGroup()
        page.add(btn_g)
        save_btn = Gtk.Button(label="Сохранить")
        save_btn.set_halign(Gtk.Align.END)
        save_btn.set_margin_top(8)
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.connect("clicked", self._on_save_clicked)
        btn_g.add(save_btn)

        if existing_item:
            self._fill(existing_item, current_group)

        self.present()

    def _fill(self, item, group_id):
        self._name_row.set_text(item.get("label", ""))
        self._desc_row.set_text(item.get("desc", ""))
        self._id_row.set_text(item.get("id", ""))
        if group_id in self._group_ids:
            self._group_row.set_selected(self._group_ids.index(group_id))

        src = item.get("source", {})
        cmd = src.get("cmd", [])
        if cmd and cmd[0] == "flatpak":
            t = "flatpak"
            pkg = cmd[4] if len(cmd) > 4 else ""
        elif cmd and cmd[0] == "epm" and len(cmd) > 1 and cmd[1] == "play":
            t = "epm_play"
            pkg = cmd[-1]
        elif cmd and cmd[0] == "epm":
            t = "epm_install"
            pkg = cmd[-1]
        elif cmd and cmd[0] in ("apt-get", "apt"):
            t = "apt"
            pkg = cmd[-1]
        else:
            t = "script"
            pkg = ""
        if t in self._SOURCE_KEYS:
            self._type_row.set_selected(self._SOURCE_KEYS.index(t))
        self._pkg_row.set_text(pkg)

        check = src.get("check", [])
        check_id = check[1] if len(check) > 1 else ""
        if check_id and check_id != pkg:
            self._check_row.set_text(check_id)

    def _build_item(self):
        name = self._name_row.get_text().strip()
        desc = self._desc_row.get_text().strip()
        iid = self._id_row.get_text().strip().replace(" ", "_").lower()
        pkg = self._pkg_row.get_text().strip()
        check_id = self._check_row.get_text().strip() or pkg
        gidx = self._group_row.get_selected()
        group_id = self._group_ids[gidx] if gidx < len(self._group_ids) else ""
        if not name or not pkg or not iid:
            return None
        tidx = self._type_row.get_selected()
        src_type = self._SOURCE_KEYS[tidx] if tidx < len(self._SOURCE_KEYS) else "flatpak"

        if src_type == "flatpak":
            cmd = ["flatpak", "install", "-y", "flathub", pkg]
            ck = "flatpak"
        elif src_type == "epm_install":
            cmd = ["epm", "-i", pkg]
            ck = "rpm"
        elif src_type == "epm_play":
            cmd = ["epm", "play", pkg]
            ck = "rpm"
        elif src_type == "apt":
            cmd = ["apt-get", "install", "-y", pkg]
            ck = "rpm"
        else:
            cmd = ["bash", "-c", pkg]
            ck = "path"

        labels = dict(zip(self._SOURCE_KEYS, self._SOURCE_LABELS))
        item = {
            "id": iid,
            "label": name,
            "desc": desc,
            "source": {
                "label": labels.get(src_type, ""),
                "cmd": cmd,
                "check": [ck, check_id],
            },
        }
        return item, group_id

    def _on_save_clicked(self, _):
        result = self._build_item()
        if not result:
            t = Adw.Toast(title="Заполните все обязательные поля")
            t.set_timeout(3)
            self.add_toast(t)
            return
        item, group_id = result
        self._on_save(item, group_id)
        self.close()
