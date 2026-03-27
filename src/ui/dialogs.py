
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


class AppEditDialog(Adw.PreferencesDialog):

    def __init__(self, parent, on_save, group_ids, group_titles,
                 existing_item=None, current_group=""):
        super().__init__()
        self._on_save = on_save
        self._existing = existing_item
        self._group_ids = group_ids
        self._group_titles = group_titles

        self._sources = []
        if existing_item:
            if "sources" in existing_item:
                self._sources = [s.copy() for s in existing_item["sources"]]
            elif "source" in existing_item:
                self._sources = [existing_item["source"].copy()]
        self._source_widgets = []

        self.set_title("Редактировать" if existing_item else "Добавить приложение")
        self.set_search_enabled(False)

        page = Adw.PreferencesPage()
        self.add(page)

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

        self._sources_group = Adw.PreferencesGroup()
        self._sources_group.set_title("Источники установки")
        page.add(self._sources_group)

        self._refresh_sources_ui()

        add_src_btn = Gtk.Button(label="Добавить источник")
        add_src_btn.set_halign(Gtk.Align.CENTER)
        add_src_btn.add_css_class("flat")
        add_src_btn.connect("clicked", self._on_add_source)
        self._sources_group.set_header_suffix(add_src_btn)

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

        self.present(parent)

    def _refresh_sources_ui(self):
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

            cmd = src.get("cmd", [])
            pkg = ""
            if cmd:
                pkg = cmd[-1] if len(cmd) > 0 else ""
                if cmd[0] == "flatpak" and len(cmd) > 4:
                    pkg = cmd[4]
            row.set_subtitle(pkg)

            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.set_valign(Gtk.Align.CENTER)
            edit_btn.set_tooltip_text("Редактировать")
            edit_btn.add_css_class("flat")
            edit_btn.connect("clicked", lambda _, idx=i: self._on_edit_source(idx))

            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text("Удалить")
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

        item = {"id": iid, "label": name, "desc": desc, "sources": self._sources}
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

    _SOURCE_LABELS = ["Flathub", "EPM", "EPM play", "APT", "Скрипт"]
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
            "check": [ck, check_id],
        }
        self._on_apply(new_src)

