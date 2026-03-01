"""Диалоговые окна: PasswordDialog, AppEditDialog."""

import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import backend

try:
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    _HAS_SECRET = True
except (ValueError, ImportError):
    _HAS_SECRET = False

if _HAS_SECRET:
    _SECRET_SCHEMA = Secret.Schema.new("ru.altbooster.app",
        Secret.SchemaFlags.NONE,
        {
            "type": Secret.SchemaAttributeType.STRING,
        }
    )

def get_saved_password():
    if not _HAS_SECRET:
        return None
    try:
        return Secret.password_lookup_sync(_SECRET_SCHEMA, {"type": "sudo_password"}, None)
    except Exception:
        return None

def save_password(pw):
    if not _HAS_SECRET:
        return
    try:
        Secret.password_store_sync(
            _SECRET_SCHEMA, {"type": "sudo_password"}, Secret.COLLECTION_DEFAULT,
            "ALT Booster Sudo Password", pw, None
        )
    except Exception:
        pass

def clear_saved_password():
    if not _HAS_SECRET:
        return
    try:
        Secret.password_clear_sync(_SECRET_SCHEMA, {"type": "sudo_password"}, None)
    except Exception:
        pass

class PasswordDialog(Adw.AlertDialog):
    """Диалог ввода пароля sudo."""

    def __init__(self, parent, on_success, on_cancel, on_pkexec=None):
        body = (
            "ALT Booster выполняет системные команды от имени root.\n"
            "Введите пароль sudo или нажмите «pkexec» для входа через polkit."
            if on_pkexec else
            "ALT Booster выполняет системные команды от имени root.\n"
            "Пароль сохраняется только на время сессии."
        )
        super().__init__(heading="Требуется пароль sudo", body=body)
        self._on_success = on_success
        self._on_cancel = on_cancel
        self._on_pkexec = on_pkexec
        self._attempts = 0
        self._submitted = False

        self._entry = Gtk.PasswordEntry()
        self._entry.set_show_peek_icon(True)
        self._entry.set_property("placeholder-text", "Пароль пользователя")
        self._entry.connect("activate", lambda _: self._submit())
        self._entry.connect("notify::text", self._on_text_changed)

        if _HAS_SECRET:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.append(self._entry)
            self._save_check = Gtk.CheckButton(label="Запомнить пароль")
            self._save_check.set_halign(Gtk.Align.CENTER)
            box.append(self._save_check)
            self.set_extra_child(box)
        else:
            self.set_extra_child(self._entry)

        self.add_response("cancel", "Отмена")
        if on_pkexec:
            self.add_response("pkexec", "Использовать pkexec")
        self.add_response("ok", "Войти")
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.set_response_enabled("ok", False)
        self.set_default_response("ok")
        self.set_close_response("cancel")
        self.connect("response", self._on_response)
        self.present(parent)

    def _on_text_changed(self, *args):
        self.set_response_enabled("ok", bool(self._entry.get_text()))

    def _on_response(self, _d, rid):
        if self._submitted:
            return
        if rid == "ok":
            self._submit()
        elif rid == "pkexec" and self._on_pkexec:
            self._submit_pkexec()
        else:
            self._on_cancel()

    def _submit_pkexec(self):
        self.set_response_enabled("pkexec", False)
        self.set_response_enabled("ok", False)
        self.set_body("⏳ Пожалуйста, подтвердите доступ в появившемся окне...")
        
        def _worker():
            try:
                # Проверяем реальную авторизацию через pkexec
                res = subprocess.run(["pkexec", "/bin/true"], capture_output=True, text=True)
                ok = (res.returncode == 0)
                # Код 126 = Authorization dismissed (Отмена пользователем)
                is_cancel = (res.returncode == 126)
            except Exception:
                ok = False
                is_cancel = False
            GLib.idle_add(self._check_pkexec_done, ok, is_cancel)

        threading.Thread(target=_worker, daemon=True).start()

    def _check_pkexec_done(self, ok, is_cancel=False):
        if ok:
            self._submitted = True
            self.close()
            if self._on_pkexec:
                self._on_pkexec()
        else:
            msg = "⚠ Отменено пользователем." if is_cancel else "❌ Ошибка авторизации pkexec. Попробуйте снова."
            self.set_body(msg)
            self.set_response_enabled("pkexec", True)
            self.set_response_enabled("ok", bool(self._entry.get_text()))

    def _submit(self):
        pw = self._entry.get_text()
        if not pw:
            return
        self.set_response_enabled("ok", False)
        self._entry.set_editable(False)
        threading.Thread(
            target=lambda: GLib.idle_add(self._check_done, pw, backend.sudo_check(pw)),
            daemon=True,
        ).start()

    def _check_done(self, pw, ok):
        if ok:
            backend.set_sudo_password(pw)
            if _HAS_SECRET and hasattr(self, '_save_check') and self._save_check.get_active():
                save_password(pw)
            self._submitted = True
            self.close()
            self._on_success()
        else:
            self._attempts += 1
            self.set_body(f"❌ Неверный пароль (попытка {self._attempts}). Попробуйте снова.")
            self._entry.set_text("")
            self._entry.set_editable(True)
            self.set_response_enabled("ok", True)
            self._entry.grab_focus()


class AppEditDialog(Adw.PreferencesWindow):
    """Диалог добавления / редактирования приложения в apps.json."""

    def __init__(self, parent, on_save, group_ids, group_titles,
                 existing_item=None, current_group=""):
        super().__init__()
        self._on_save = on_save
        self._existing = existing_item
        self._group_ids = group_ids
        self._group_titles = group_titles
        
        # Список источников (список dict)
        self._sources = []
        if existing_item:
            if "sources" in existing_item:
                self._sources = [s.copy() for s in existing_item["sources"]]
            elif "source" in existing_item:
                self._sources = [existing_item["source"].copy()]
        self._source_widgets = []

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

        # Источники
        self._sources_group = Adw.PreferencesGroup()
        self._sources_group.set_title("Источники установки")
        page.add(self._sources_group)
        
        self._refresh_sources_ui()
        
        add_src_btn = Gtk.Button(label="Добавить источник")
        add_src_btn.set_halign(Gtk.Align.CENTER)
        add_src_btn.add_css_class("flat")
        add_src_btn.connect("clicked", self._on_add_source)
        self._sources_group.set_header_suffix(add_src_btn)

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

    def _refresh_sources_ui(self):
        # Очищаем группу (удаляем все строки)
        for row in self._source_widgets:
            self._sources_group.remove(row)
        self._source_widgets.clear()
            
        if not self._sources:
            row = Adw.ActionRow()
            row.set_title("Нет источников")
            self._sources_group.add(row)
            self._source_widgets.append(row)
            return

        for i, src in enumerate(self._sources):
            row = Adw.ActionRow()
            row.set_title(src.get("label", "Source"))
            
            # Определяем тип для подзаголовка
            cmd = src.get("cmd", [])
            pkg = ""
            if cmd:
                pkg = cmd[-1] if len(cmd) > 0 else ""
                if cmd[0] == "flatpak" and len(cmd) > 4: pkg = cmd[4]
            
            row.set_subtitle(pkg)
            
            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.set_valign(Gtk.Align.CENTER)
            edit_btn.add_css_class("flat")
            edit_btn.connect("clicked", lambda _, idx=i: self._on_edit_source(idx))
            
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.add_css_class("flat")
            del_btn.add_css_class("destructive-action")
            del_btn.connect("clicked", lambda _, idx=i: self._on_delete_source(idx))
            
            row.add_suffix(edit_btn)
            row.add_suffix(del_btn)
            self._sources_group.add(row)
            self._source_widgets.append(row)

    def _on_add_source(self, _):
        self._open_source_editor(None, -1)

    def _on_edit_source(self, idx):
        self._open_source_editor(self._sources[idx], idx)

    def _on_delete_source(self, idx):
        del self._sources[idx]
        self._refresh_sources_ui()

    def _open_source_editor(self, src_data, idx):
        # Открываем подстраницу для редактирования источника
        sp = SourceEditPage(src_data, lambda new_src: self._save_source(new_src, idx))
        self.push_subpage(sp)

    def _save_source(self, new_src, idx):
        if idx == -1:
            self._sources.append(new_src)
        else:
            self._sources[idx] = new_src
        self.pop_subpage()
        self._refresh_sources_ui()

    def _fill(self, item, group_id):
        self._name_row.set_text(item.get("label", ""))
        self._desc_row.set_text(item.get("desc", ""))
        self._id_row.set_text(item.get("id", ""))
        if group_id in self._group_ids:
            self._group_row.set_selected(self._group_ids.index(group_id))

    def _build_item(self):
        name = self._name_row.get_text().strip()
        desc = self._desc_row.get_text().strip()
        iid = self._id_row.get_text().strip().replace(" ", "_").lower()
        gidx = self._group_row.get_selected()
        group_id = self._group_ids[gidx] if gidx < len(self._group_ids) else ""
        
        if not name or not iid:
            return None
        
        if not self._sources:
             return None

        item = {
            "id": iid,
            "label": name,
            "desc": desc,
            "sources": self._sources
        }
        return item, group_id

    def _on_save_clicked(self, _):
        result = self._build_item()
        if not result:
            t = Adw.Toast(title="Заполните поля и добавьте хотя бы один источник")
            t.set_timeout(3)
            self.add_toast(t)
            return
        item, group_id = result
        self._on_save(item, group_id)
        self.close()


class SourceEditPage(Adw.NavigationPage):
    """Страница редактирования одного источника."""
    
    _SOURCE_LABELS = ["Flathub", "EPM install", "EPM play", "APT", "Скрипт"]
    _SOURCE_KEYS   = ["flatpak", "epm_install", "epm_play", "apt", "script"]

    def __init__(self, src_data, on_apply):
        super().__init__()
        self.set_title("Источник")
        self._on_apply = on_apply
        
        pref_page = Adw.PreferencesPage()
        self.set_child(pref_page)
        
        grp = Adw.PreferencesGroup()
        grp.set_title("Настройки источника")
        pref_page.add(grp)
        
        self._type_row = Adw.ComboRow()
        self._type_row.set_title("Тип")
        tm = Gtk.StringList()
        for label in self._SOURCE_LABELS:
            tm.append(label)
        self._type_row.set_model(tm)
        grp.add(self._type_row)
        
        self._pkg_row = Adw.EntryRow()
        self._pkg_row.set_title("Пакет / App ID")
        grp.add(self._pkg_row)
        
        self._check_row = Adw.EntryRow()
        self._check_row.set_title("Check ID (если отличается)")
        grp.add(self._check_row)
        
        btn_grp = Adw.PreferencesGroup()
        pref_page.add(btn_grp)
        btn = Gtk.Button(label="Готово")
        btn.set_halign(Gtk.Align.END)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", self._on_done)
        btn_grp.add(btn)
        
        if src_data:
            self._fill(src_data)

    def _fill(self, src):
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

    def _on_done(self, _):
        pkg = self._pkg_row.get_text().strip()
        if not pkg:
            return
            
        tidx = self._type_row.get_selected()
        src_type = self._SOURCE_KEYS[tidx] if tidx < len(self._SOURCE_KEYS) else "flatpak"
        check_id = self._check_row.get_text().strip() or pkg
        
        if src_type == "flatpak":
            cmd = ["flatpak", "install", "-y", "flathub", pkg]
            ck = "flatpak"
        elif src_type == "epm_install":
            cmd = ["epm", "-i", "-y", pkg]
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
        
        new_src = {
            "label": labels.get(src_type, ""),
            "cmd": cmd,
            "check": [ck, check_id]
        }
        self._on_apply(new_src)
