"""
profile_dialog.py — диалоги для работы с пресетами ALT Booster.

Содержит две публичных функции:
  show_preset_save_dialog(parent, existing_names, on_save)
  show_preset_import_dialog(parent, data, on_apply)
"""

from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


def show_preset_save_dialog(
    parent: Gtk.Window,
    existing_names: list[str],
    on_save: Callable[[str], None],
) -> None:
    """Показывает диалог ввода имени для нового пресета.

    Кнопка «Сохранить» активна только если введено непустое уникальное имя.
    on_save(name) — вызывается с готовым именем при подтверждении.
    """
    entry = Gtk.Entry()
    entry.set_placeholder_text("Название пресета")
    entry.set_activates_default(True)

    warning = Gtk.Label()
    warning.set_xalign(0.0)
    warning.add_css_class("warning")
    warning.set_visible(False)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_margin_top(6)
    box.append(entry)
    box.append(warning)

    dlg = Adw.AlertDialog(heading="Сохранить пресет")
    dlg.set_extra_child(box)
    dlg.add_response("cancel", "Отмена")
    dlg.add_response("save", "Сохранить")
    dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("save")
    dlg.set_close_response("cancel")

    def _validate(*_):
        name = entry.get_text().strip()
        if name in existing_names:
            warning.set_text(f"Пресет «{name}» уже существует — будет перезаписан")
            warning.set_visible(True)
        else:
            warning.set_visible(False)
        dlg.set_response_enabled("save", bool(name))

    entry.connect("notify::text", _validate)
    dlg.set_response_enabled("save", False)

    def _on_response(_d, r):
        if r == "save":
            name = entry.get_text().strip()
            if name:
                on_save(name)

    dlg.connect("response", _on_response)
    dlg.present(parent)


def show_preset_import_dialog(
    parent: Gtk.Window,
    data: dict,
    on_apply: Callable[[dict, dict], None],
) -> None:
    """Показывает диалог применения пресета с резюме и чекбоксами.

    on_apply(data, flags) — вызывается при подтверждении.
    flags = {"apps": bool, "extensions": bool, "settings": bool}
    """
    name = data.get("name", "Пресет")
    apps = data.get("apps") or []
    extensions = data.get("extensions") or []
    has_settings = bool(
        data.get("gsettings") or data.get("state") or data.get("custom_apps")
    )

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_top(8)
    box.set_size_request(320, -1)

    # ── Приложения ────────────────────────────────────────────────────────────
    check_apps = Gtk.CheckButton(label=f"Установить приложения ({len(apps)})")
    check_apps.set_active(bool(apps))
    check_apps.set_sensitive(bool(apps))
    box.append(check_apps)

    if apps:
        apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        apps_box.set_margin_start(20)
        apps_box.set_margin_bottom(4)
        # Показываем не более 8 приложений, остальное — «и ещё N»
        shown = apps[:8]
        for a in shown:
            lbl = Gtk.Label(label=f"• {a.get('label', a['id'])}")
            lbl.set_xalign(0.0)
            lbl.add_css_class("dim-label")
            apps_box.append(lbl)
        if len(apps) > 8:
            lbl = Gtk.Label(label=f"  … и ещё {len(apps) - 8}")
            lbl.set_xalign(0.0)
            lbl.add_css_class("dim-label")
            apps_box.append(lbl)
        box.append(apps_box)

    # ── Расширения ────────────────────────────────────────────────────────────
    check_exts = Gtk.CheckButton(label=f"Расширения GNOME ({len(extensions)})")
    check_exts.set_active(bool(extensions))
    check_exts.set_sensitive(bool(extensions))
    box.append(check_exts)

    if extensions:
        exts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        exts_box.set_margin_start(20)
        exts_box.set_margin_bottom(4)
        shown_e = extensions[:5]
        for uuid in shown_e:
            lbl = Gtk.Label(label=f"• {uuid}")
            lbl.set_xalign(0.0)
            lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            lbl.add_css_class("dim-label")
            exts_box.append(lbl)
        if len(extensions) > 5:
            lbl = Gtk.Label(label=f"  … и ещё {len(extensions) - 5}")
            lbl.set_xalign(0.0)
            lbl.add_css_class("dim-label")
            exts_box.append(lbl)
        box.append(exts_box)

    # ── Настройки ─────────────────────────────────────────────────────────────
    check_settings = Gtk.CheckButton(label="Применить настройки оформления")
    check_settings.set_active(has_settings)
    check_settings.set_sensitive(has_settings)
    box.append(check_settings)

    if has_settings:
        gs = data.get("gsettings") or []
        if gs:
            gs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            gs_box.set_margin_start(20)
            gs_box.set_margin_bottom(4)
            for entry in gs:
                lbl = Gtk.Label(label=f"• {entry['key']}: {entry['value']}")
                lbl.set_xalign(0.0)
                lbl.add_css_class("dim-label")
                gs_box.append(lbl)
            box.append(gs_box)

    # ── Диалог ────────────────────────────────────────────────────────────────
    dlg = Adw.AlertDialog(
        heading=f"Применить пресет «{name}»",
        body="Выберите, что применить:",
    )
    dlg.set_extra_child(box)
    dlg.add_response("cancel", "Отмена")
    dlg.add_response("apply", "Применить")
    dlg.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("apply")
    dlg.set_close_response("cancel")

    def _on_response(_d, r):
        if r == "apply":
            on_apply(data, {
                "apps": check_apps.get_active(),
                "extensions": check_exts.get_active(),
                "settings": check_settings.get_active(),
            })

    dlg.connect("response", _on_response)
    dlg.present(parent)
