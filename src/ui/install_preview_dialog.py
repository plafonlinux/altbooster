
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from system.packages import InstallPreview, get_install_preview

_MAX_PKG_ROWS = 20


class InstallPreviewDialog(Adw.Window):

    def __init__(
        self,
        parent: Gtk.Window,
        app_name: str,
        source_label: str,
        cmd: list[str],
        on_confirm,
        on_cancel,
        runner=None,
        empty_message: str = "Нет изменений — пакет уже установлен.",
    ):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(580, 520)
        self.set_resizable(True)
        self.set_title(f"Установка {app_name}")

        self._app_name = app_name
        self._source_label = source_label
        self._cmd = list(cmd)
        self._runner = runner
        self._empty_message = empty_message
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._confirmed = False

        self._confirm_btn = Gtk.Button(label="Продолжить установку")
        self._confirm_btn.add_css_class("suggested-action")
        self._confirm_btn.add_css_class("pill")
        self._confirm_btn.set_sensitive(False)
        self._confirm_btn.connect("clicked", self._on_confirm_clicked)

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root_box)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        root_box.append(header)

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root_box.append(self._scroll)

        root_box.append(self._build_bottom_bar())

        self._show_spinner()
        threading.Thread(target=self._run_preview_thread, daemon=True).start()

        self.connect("close-request", self._on_close_request)


    def _build_bottom_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_top(8)
        bar.set_margin_bottom(12)
        bar.set_margin_start(16)
        bar.set_margin_end(16)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        cancel_btn = Gtk.Button(label="Отмена")
        cancel_btn.add_css_class("flat")
        cancel_btn.add_css_class("pill")
        cancel_btn.connect("clicked", self._on_cancel_clicked)
        bar.append(cancel_btn)

        bar.append(self._confirm_btn)
        return bar

    def _show_spinner(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_top(40)
        box.set_margin_bottom(40)

        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        box.append(spinner)

        lbl = Gtk.Label(label="Получение информации о пакетах…")
        lbl.add_css_class("dim-label")
        box.append(lbl)

        self._scroll.set_child(box)

    def _run_preview_thread(self):
        preview = get_install_preview(self._cmd, runner=self._runner)
        GLib.idle_add(self._on_preview_ready, preview)

    def _on_preview_ready(self, preview: InstallPreview):
        self._build_content(preview)
        self._confirm_btn.set_sensitive(True)

        no_active = not any([
            preview.new_packages, preview.upgraded_packages,
            preview.removed_packages, preview.flatpak_updates,
            preview.dry_run_failed,
        ])
        if no_active:
            self._confirm_btn.set_label("Закрыть")
            self._confirm_btn.remove_css_class("suggested-action")
            self._confirm_btn.add_css_class("flat")
            self._no_active_changes = True
        else:
            self._no_active_changes = False

        return False

    def _build_content(self, preview: InstallPreview):
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(16)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)

        if preview.source_type == "script":
            content_box.append(self._build_script_view())
        elif preview.source_type == "epm_play":
            content_box.append(self._build_epm_play_view(preview))
        else:
            if preview.dry_run_failed:
                content_box.append(self._build_warning_banner(
                    "Не удалось получить список пакетов. "
                    "Вы всё равно можете продолжить установку."
                ))

            if preview.source_type == "flatpak" and preview.app_description:
                desc_lbl = Gtk.Label(label=preview.app_description)
                desc_lbl.add_css_class("dim-label")
                desc_lbl.set_wrap(True)
                desc_lbl.set_halign(Gtk.Align.START)
                content_box.append(desc_lbl)

            if preview.new_packages:
                icon = "package-x-generic-symbolic" if preview.source_type == "flatpak" else "list-add-symbolic"
                content_box.append(self._build_package_group(
                    f"Устанавливается ({len(preview.new_packages)})",
                    preview.new_packages,
                    icon,
                ))

            if preview.upgraded_packages:
                content_box.append(self._build_package_group(
                    f"Обновляется ({len(preview.upgraded_packages)})",
                    preview.upgraded_packages,
                    "software-update-available-symbolic",
                ))

            if preview.removed_packages:
                content_box.append(self._build_package_group(
                    f"Удаляется ({len(preview.removed_packages)})",
                    preview.removed_packages,
                    "user-trash-symbolic",
                ))

            no_active_changes = not any([
                preview.new_packages, preview.upgraded_packages,
                preview.removed_packages, preview.dry_run_failed,
            ])
            if no_active_changes:
                lbl = Gtk.Label(label=self._empty_message)
                lbl.add_css_class("dim-label")
                lbl.set_halign(Gtk.Align.CENTER)
                content_box.append(lbl)

            if preview.flatpak_updates:
                content_box.append(self._build_package_group(
                    f"Обновления Flatpak ({len(preview.flatpak_updates)})",
                    preview.flatpak_updates,
                    "package-x-generic-symbolic",
                ))

            if preview.download_size or preview.disk_space:
                content_box.append(self._build_sizes_group(preview))

            if preview.warnings:
                content_box.append(self._build_text_section(
                    "Предупреждения", preview.warnings, "warning"
                ))

            if preview.errors:
                content_box.append(self._build_text_section(
                    "Ошибки", preview.errors, "error"
                ))

            if preview.kept_packages:
                content_box.append(self._build_kept_expander(preview.kept_packages))

        self._scroll.set_child(content_box)

    def _build_epm_play_view(self, preview: InstallPreview) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        group.set_title("Установка через epm play")

        row = Adw.ActionRow()
        row.set_title(self._app_name)
        pkg = preview.package_names[0] if preview.package_names else ""
        if pkg and pkg != self._app_name:
            row.set_subtitle(pkg)
        try:
            row.set_icon_name("applications-games-symbolic")
        except Exception:
            pass
        group.add(row)

        if preview.app_url:
            url_row = Adw.ActionRow()
            url_row.set_title("Источник")
            url_row.set_subtitle(preview.app_url)
            try:
                url_row.set_icon_name("web-browser-symbolic")
            except Exception:
                pass
            group.add(url_row)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(group)

        note = Gtk.Label(
            label="epm play использует собственный скрипт — "
                  "точный список пакетов станет известен в процессе установки."
        )
        note.add_css_class("dim-label")
        note.set_wrap(True)
        note.set_halign(Gtk.Align.START)
        note.set_margin_top(4)
        box.append(note)
        return box

    def _build_script_view(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        group.set_title("Выполнение скрипта установки")

        row = Adw.ActionRow()
        row.set_title(self._app_name)
        try:
            row.set_icon_name("system-run-symbolic")
        except Exception:
            pass
        group.add(row)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(group)

        note = Gtk.Label(
            label="Детальная информация о пакетах недоступна для этого источника."
        )
        note.add_css_class("dim-label")
        note.set_wrap(True)
        note.set_halign(Gtk.Align.START)
        note.set_margin_top(4)
        box.append(note)
        return box

    def _build_package_group(
        self, title: str, packages: list[str], icon_name: str
    ) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_margin_start(4)
        outer.append(title_lbl)

        visible = packages[:_MAX_PKG_ROWS]
        hidden = len(packages) - len(visible)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("card")

        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(2)
        flow.set_min_children_per_line(2)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_row_spacing(0)
        flow.set_column_spacing(0)
        flow.set_margin_top(4)
        flow.set_margin_bottom(4)
        flow.set_margin_start(4)
        flow.set_margin_end(4)

        for pkg in visible:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            item.set_margin_top(4)
            item.set_margin_bottom(4)
            item.set_margin_start(8)
            item.set_margin_end(8)

            try:
                icon = Gtk.Image.new_from_icon_name(icon_name)
                icon.set_pixel_size(14)
                icon.add_css_class("dim-label")
                item.append(icon)
            except Exception:
                pass

            lbl = Gtk.Label(label=pkg)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(3)
            item.append(lbl)

            flow.append(item)

        card.append(flow)

        if hidden > 0:
            more_lbl = Gtk.Label(label=f"… и ещё {hidden} пакетов")
            more_lbl.add_css_class("dim-label")
            more_lbl.set_halign(Gtk.Align.CENTER)
            more_lbl.set_margin_top(2)
            more_lbl.set_margin_bottom(8)
            card.append(more_lbl)

        outer.append(card)
        return outer

    def _build_kept_expander(self, packages: list[str]) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()

        expander = Adw.ExpanderRow()
        expander.set_title(f"Пакеты заморожены пользователем ({len(packages)})")
        expander.set_expanded(False)

        visible = packages[:_MAX_PKG_ROWS]
        hidden = len(packages) - len(visible)

        flow_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(2)
        flow.set_min_children_per_line(2)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_row_spacing(0)
        flow.set_column_spacing(0)
        flow.set_margin_top(4)
        flow.set_margin_bottom(4)
        flow.set_margin_start(8)
        flow.set_margin_end(8)

        for pkg in visible:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            item.set_margin_top(4)
            item.set_margin_bottom(4)

            try:
                icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
                icon.set_pixel_size(14)
                icon.add_css_class("dim-label")
                item.append(icon)
            except Exception:
                pass

            lbl = Gtk.Label(label=pkg)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(3)
            item.append(lbl)

            flow.append(item)

        flow_box.append(flow)

        if hidden > 0:
            more_lbl = Gtk.Label(label=f"… и ещё {hidden} пакетов")
            more_lbl.add_css_class("dim-label")
            more_lbl.set_halign(Gtk.Align.CENTER)
            more_lbl.set_margin_bottom(8)
            flow_box.append(more_lbl)

        expander.add_row(flow_box)
        group.add(expander)
        return group

    def _build_sizes_group(self, preview: InstallPreview) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Использование ресурсов")

        if preview.download_size:
            row = Adw.ActionRow()
            row.set_title("Загрузка")
            row.set_subtitle(preview.download_size)
            try:
                row.set_icon_name("network-transmit-receive-symbolic")
            except Exception:
                pass
            group.add(row)

        if preview.disk_space:
            row = Adw.ActionRow()
            row.set_title("Место на диске")
            row.set_subtitle(preview.disk_space)
            try:
                row.set_icon_name("drive-harddisk-symbolic")
            except Exception:
                pass
            group.add(row)

        return group

    def _build_text_section(
        self, title: str, lines: list[str], style: str
    ) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        title_lbl = Gtk.Label(label=title)
        title_lbl.set_halign(Gtk.Align.START)
        attrs = title_lbl.get_attributes() or None
        title_lbl.add_css_class("heading")
        if style == "error":
            title_lbl.add_css_class("error")
        elif style == "warning":
            title_lbl.add_css_class("warning")
        box.append(title_lbl)

        for line in lines:
            lbl = Gtk.Label(label=line)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            lbl.set_selectable(True)
            lbl.add_css_class("dim-label")
            box.append(lbl)

        return box

    def _build_warning_banner(self, text: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("card")

        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_pixel_size(24)
        icon.add_css_class("warning")
        icon.set_margin_start(12)
        icon.set_margin_top(12)
        icon.set_margin_bottom(12)
        box.append(icon)

        lbl = Gtk.Label(label=text)
        lbl.set_wrap(True)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_hexpand(True)
        lbl.set_margin_top(12)
        lbl.set_margin_bottom(12)
        lbl.set_margin_end(12)
        box.append(lbl)

        return box


    def _on_confirm_clicked(self, _):
        if self._confirmed:
            return
        self._confirmed = True
        self.close()
        if getattr(self, "_no_active_changes", False):
            self._on_cancel()
        else:
            self._on_confirm()

    def _on_cancel_clicked(self, _):
        self.close()
        if not self._confirmed:
            self._on_cancel()

    def _on_close_request(self, _) -> bool:
        if not self._confirmed:
            self._on_cancel()
        return False

