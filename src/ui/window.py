
import datetime
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

import config
import backend
from system import profile as profile_module
from ui.profile_dialog import show_preset_save_dialog, show_preset_import_dialog
from ui.setup_page import SetupPage
from ui.apps_page import AppsPage
from ui.extensions_page import ExtensionsPage
from ui.terminal_page import TerminalPage
from ui.davinci_page import DaVinciPage
from ui.amd_page import AmdPage
from ui.intel_page import IntelPage
from ui.maintenance_page import MaintenancePage
from ui.flatpak_page import FlatpakPage


class AltBoosterWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        start_time = time.time()
        super().__init__(**kwargs)

        icon_theme = "Adwaita"
        if not os.path.exists("/usr/share/icons/Adwaita") and os.path.exists("/usr/share/icons/alt-workstation"):
            icon_theme = "alt-workstation"
        Gtk.Settings.get_default().set_property("gtk-icon-theme-name", icon_theme)

        _icons_base = Path(__file__).parent.parent.parent / "icons"

        _src_base = _icons_base / "hicolor" / "scalable"
        _dst_hicolor = Path.home() / ".local" / "share" / "icons" / "hicolor"
        _dst_base = _dst_hicolor / "scalable"
        for _cat in ("apps", "devices"):
            _src_cat = _src_base / _cat
            _dst_cat = _dst_base / _cat
            if not _src_cat.exists():
                continue
            _dst_cat.mkdir(parents=True, exist_ok=True)
            for _svg in _src_cat.glob("*.svg"):
                _dst = _dst_cat / _svg.name
                try:
                    if not _dst.exists() or _dst.read_bytes() != _svg.read_bytes():
                        shutil.copy2(_svg, _dst)
                except OSError:
                    pass

        try:
            subprocess.run(
                ["gtk-update-icon-cache", "-f", "-t", str(_dst_hicolor)],
                capture_output=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

        _it = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        _it.add_search_path(str(_icons_base))

        self._pulse_timer_id = None
        self._reset_status_timer_id = None
        self._log_queue = queue.SimpleQueue()
        self._log_widget = self._build_log_panel()

        self.set_title("ALT Booster")
        settings = self._load_settings()

        self._log_file = config.CONFIG_DIR / "altbooster.log"
        threading.Thread(target=self._log_writer_loop, daemon=True).start()

        self.set_default_size(settings.get("width", 740), settings.get("height", 880))
        self.connect("close-request", self._on_close)


        header_widget = self._build_header()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_update_banner())
        root.append(self._build_profile_banner())

        self._setup = SetupPage(self._log)
        self._apps = AppsPage(self._log)
        self._extensions = ExtensionsPage(self._log)
        self._terminal = TerminalPage(self._log)
        self._davinci = DaVinciPage(self._log)
        self._maint = MaintenancePage(self._log)

        self._amd = AmdPage(self._log)
        self._intel = IntelPage(self._log)
        self._flatpak = FlatpakPage(self._log)

        for widget, name, title, icon in [
            (self._setup,       "setup",       "Начало",          "go-home-symbolic"),
            (self._apps,        "apps",        "Приложения",      "grid-large-symbolic"),
            (self._extensions,  "extensions",  "Расширения",      "application-x-addon-symbolic"),
            (self._flatpak,     "flatpak",     "Flatpak",         "flatpak-symbolic"),
            (self._terminal,   "terminal",    "Терминал",        "utilities-terminal-symbolic"),
            (self._amd,        "amd",         "AMD Radeon",      "video-display-symbolic"),
            (self._intel,      "intel",       "Intel",           "processor-symbolic"),
            (self._davinci,    "davinci",     "DaVinci Resolve", "davinci-symbolic"),
            (self._maint,      "maintenance", "Обслуживание",    "emblem-system-symbolic"),
        ]:
            p = self._stack.add_titled(widget, name, title)
            p.set_icon_name(icon)

        self._stack.set_vexpand(True)
        stack_overlay = Gtk.Overlay()
        stack_overlay.set_child(self._stack)
        stack_overlay.set_vexpand(True)
        self._relogin_revealer = self._build_relogin_banner()
        stack_overlay.add_overlay(self._relogin_revealer)
        root.append(stack_overlay)

        self._split_view = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._split_view.set_start_child(self._build_sidebar())
        self._split_view.set_end_child(root)
        self._split_view.set_vexpand(True)
        self._split_view.set_shrink_start_child(False)
        self._split_view.set_resize_start_child(False)
        self._split_view.set_shrink_end_child(False)

        self._bottom_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._bottom_paned.set_start_child(self._bottom_list_widget)
        self._bottom_paned.set_end_child(self._log_widget)
        self._bottom_paned.set_shrink_start_child(False)
        self._bottom_paned.set_resize_start_child(False)
        self._bottom_paned.set_shrink_end_child(False)

        outer_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer_vbox.append(self._split_view)
        outer_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        outer_vbox.append(self._bottom_paned)

        sidebar_width = settings.get("sidebar_width")
        self._sidebar_saved_width = sidebar_width
        if sidebar_width is not None:
            GLib.idle_add(self._split_view.set_position, sidebar_width)
        self._split_view.connect("notify::position", self._on_sidebar_position_changed)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header_widget)
        toolbar_view.set_content(outer_vbox)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(toolbar_view)

        bp_bin = Adw.BreakpointBin()
        bp_bin.set_size_request(750, 200)
        bp_bin.set_child(self._toast_overlay)

        self.set_content(bp_bin)

        GLib.idle_add(self._apply_tab_label_visibility)

        startup_ms = (time.time() - start_time) * 1000
        self._log(f"ℹ Startup time: {startup_ms:.2f} ms\n")


    def _build_header(self):
        header = Adw.HeaderBar()
        self._header = header

        self._stack = Adw.ViewStack()

        self._window_title = Adw.WindowTitle()
        self._window_title.set_title("ALT Booster")
        header.set_title_widget(self._window_title)

        header.pack_start(self._build_preset_button())

        menu = Gio.Menu()

        section_diag = Gio.Menu()
        section_diag.append("Посмотреть логи", "win.open_log")
        section_diag.append("Очистить лог", "win.clear_log")
        section_diag.append("Очистить кэш", "win.reset_state")
        menu.append_section(None, section_diag)

        section_reset = Gio.Menu()
        section_reset.append("Сброс настроек приложения", "win.reset_config")
        menu.append_section(None, section_reset)

        section_about = Gio.Menu()
        section_about.append("О приложении", "win.about")
        menu.append_section(None, section_about)

        self._app_menu = menu

        _dot_css = Gtk.CssProvider()
        _dot_css.load_from_data(b"""
            .ab-update-dot {
                background-color: @accent_color;
                border-radius: 999px;
                min-width: 9px;
                min-height: 9px;
                border: 1.5px solid @window_bg_color;
                padding: 0;
                font-size: 0;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _dot_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        actions = [
            ("check_update",   self._check_for_updates),
            ("about",          self._show_about),
            ("clear_log",      self._clear_log),
            ("reset_state",    self._reset_state),
            ("reset_config",   self._reset_config),
            ("open_log",       self._open_log_file),
        ]
        for name, cb in actions:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)

        show_labels = config.state_get("show_tab_labels", False)
        a_labels = Gio.SimpleAction.new_stateful(
            "show_tab_labels", None, GLib.Variant.new_boolean(show_labels)
        )
        a_labels.connect("change-state", self._on_show_tab_labels_changed)
        self.add_action(a_labels)

        app = self.get_application()
        app.set_accels_for_action("win.about", ["F1"])

        return header

    def _build_sidebar(self) -> Gtk.Widget:
        nav_list = Gtk.ListBox()
        nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        nav_list.add_css_class("navigation-sidebar")
        self._nav_list = nav_list
        self._nav_images: list[Gtk.Image] = []
        self._nav_labels: list[Gtk.Label] = []

        for name, title, icon_name in [
            ("setup",       "Начало",          "go-home-symbolic"),
            ("apps",        "Приложения",      "grid-large-symbolic"),
            ("extensions",  "Расширения",      "application-x-addon-symbolic"),
            ("flatpak",     "Flatpak",         "flatpak-symbolic"),
            ("terminal",    "Терминал",        "utilities-terminal-symbolic"),
            ("amd",         "AMD Radeon",      "video-display-symbolic"),
            ("intel",       "Intel",           "processor-symbolic"),
            ("davinci",     "DaVinci Resolve", "davinci-symbolic"),
            ("maintenance", "Обслуживание",    "emblem-system-symbolic"),
        ]:
            row = Gtk.ListBoxRow()
            row.set_name(name)
            row.set_tooltip_text(title)
            box = Gtk.Box(spacing=7)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(7)
            box.set_margin_end(7)
            img = Gtk.Image.new_from_icon_name(icon_name)
            img.set_pixel_size(16)
            lbl = Gtk.Label(label=title)
            lbl.set_xalign(0.0)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(img)
            box.append(lbl)
            row.set_child(box)
            nav_list.append(row)
            self._nav_images.append(img)
            self._nav_labels.append(lbl)

        nav_list.select_row(nav_list.get_row_at_index(0))
        nav_list.connect("row-selected", self._on_nav_row_selected)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(nav_list)
        scroll.set_vexpand(True)

        self._bottom_list_widget = self._build_sidebar_bottom()

        self._sidebar_widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._sidebar_widget.append(scroll)
        return self._sidebar_widget

    def _build_sidebar_bottom(self) -> Gtk.Widget:
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._version_label_sidebar = Gtk.Label(label=f"v{config.VERSION}")
        self._version_label_sidebar.add_css_class("caption")
        self._version_label_sidebar.add_css_class("dim-label")
        self._version_label_sidebar.set_margin_top(6)
        self._version_label_sidebar.set_margin_bottom(2)
        container.append(self._version_label_sidebar)

        bottom_list = Gtk.ListBox()
        bottom_list.add_css_class("navigation-sidebar")
        bottom_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._bottom_images: list[Gtk.Image] = []
        self._bottom_labels: list[Gtk.Label] = []

        def _make_row_box():
            box = Gtk.Box(spacing=7)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(7)
            box.set_margin_end(7)
            return box

        upd_row = Gtk.ListBoxRow()
        upd_row.set_name("update")
        upd_row.set_activatable(True)
        upd_row.set_tooltip_text("Проверить обновления")
        upd_box = _make_row_box()

        upd_icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        upd_icon.set_pixel_size(16)

        upd_lbl = Gtk.Label(label="Обновления")
        upd_lbl.set_xalign(0.0)
        upd_lbl.set_hexpand(True)
        upd_lbl.set_ellipsize(Pango.EllipsizeMode.END)

        self._update_badge_dot = Gtk.Label(label="")
        self._update_badge_dot.add_css_class("ab-update-dot")
        self._update_badge_dot.set_valign(Gtk.Align.CENTER)
        self._update_badge_dot.set_visible(False)

        upd_box.append(upd_icon)
        upd_box.append(upd_lbl)
        upd_box.append(self._update_badge_dot)
        upd_row.set_child(upd_box)
        bottom_list.append(upd_row)
        self._bottom_images.append(upd_icon)
        self._bottom_labels.append(upd_lbl)

        menu_row = Gtk.ListBoxRow()
        menu_row.set_name("settings")
        menu_row.set_activatable(True)
        menu_row.set_tooltip_text("Настройки")

        mb_box = _make_row_box()
        mb_icon = Gtk.Image.new_from_icon_name("sliders-vertical-symbolic")
        mb_icon.set_pixel_size(16)
        mb_lbl = Gtk.Label(label="Настройки")
        mb_lbl.set_xalign(0.0)
        mb_lbl.set_hexpand(True)
        mb_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        mb_box.append(mb_icon)
        mb_box.append(mb_lbl)
        menu_row.set_child(mb_box)

        self._settings_popover = Gtk.PopoverMenu.new_from_model(self._app_menu)
        self._settings_popover.set_parent(menu_row)
        self._settings_popover.set_has_arrow(False)
        self._settings_popover.set_position(Gtk.PositionType.TOP)

        bottom_list.append(menu_row)
        self._bottom_images.append(mb_icon)
        self._bottom_labels.append(mb_lbl)

        bottom_list.connect("row-activated", self._on_bottom_row_activated)
        container.append(bottom_list)
        return container

    def _on_bottom_row_activated(self, _, row):
        name = row.get_name()
        if name == "update":
            self._check_for_updates()
        elif name == "settings":
            self._settings_popover.popup()

    def _on_nav_row_selected(self, _, row):
        if row is not None:
            self._stack.set_visible_child_name(row.get_name())

    _ICON_SIZE_WITH_LABELS = 16
    _ICON_SIZE_ICONS_ONLY  = 21
    _SIDEBAR_ICONS_ONLY_WIDTH = 44
    _SIDEBAR_LABELS_THRESHOLD = 110

    def _apply_tab_label_visibility(self, show: bool | None = None, from_drag: bool = False):
        if show is None:
            show = config.state_get("show_tab_labels", True)
        icon_size = self._ICON_SIZE_WITH_LABELS if show else self._ICON_SIZE_ICONS_ONLY

        for img, lbl in zip(
            self._nav_images + self._bottom_images,
            self._nav_labels + self._bottom_labels,
        ):
            img.set_pixel_size(icon_size)
            lbl.set_visible(show)

        self._version_label_sidebar.set_visible(show)

        if show:
            self._sidebar_widget.set_size_request(90, -1)
            self._bottom_list_widget.set_size_request(90, -1)
            if not from_drag:
                saved = getattr(self, "_sidebar_saved_width", None)
                if saved:
                    GLib.idle_add(self._split_view.set_position, saved)
                    GLib.idle_add(self._bottom_paned.set_position, saved)
        else:
            if not from_drag:
                self._sidebar_saved_width = self._split_view.get_position()
            self._sidebar_widget.set_size_request(self._SIDEBAR_ICONS_ONLY_WIDTH, -1)
            self._bottom_list_widget.set_size_request(self._SIDEBAR_ICONS_ONLY_WIDTH, -1)
            GLib.idle_add(self._split_view.set_position, self._SIDEBAR_ICONS_ONLY_WIDTH)
            GLib.idle_add(self._bottom_paned.set_position, self._SIDEBAR_ICONS_ONLY_WIDTH)

    def _on_sidebar_position_changed(self, paned, *_):
        pos = paned.get_position()
        self._bottom_paned.set_position(pos)
        currently_showing = bool(self._nav_labels and self._nav_labels[0].get_visible())
        should_show = pos > self._SIDEBAR_LABELS_THRESHOLD

        if should_show == currently_showing:
            return

        if not should_show:
            self._sidebar_saved_width = pos

        config.state_set("show_tab_labels", should_show)
        self._apply_tab_label_visibility(show=should_show, from_drag=True)

    def _on_show_tab_labels_changed(self, action, state):
        action.set_state(state)
        config.state_set("show_tab_labels", state.get_boolean())
        self._apply_tab_label_visibility()

    def _build_update_banner(self):
        outer = Gtk.Box()
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_top(6)
        outer.set_margin_bottom(4)
        outer.set_opacity(0.92)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("ab-float-banner")

        icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        icon.set_pixel_size(16)
        box.append(icon)

        self._update_banner_label = Gtk.Label()
        self._update_banner_label.set_xalign(0.0)
        box.append(self._update_banner_label)

        go_btn = Gtk.Button(label="Обновить")
        go_btn.add_css_class("suggested-action")
        go_btn.add_css_class("pill")
        go_btn.set_valign(Gtk.Align.CENTER)
        go_btn.connect("clicked", self._go_to_update)
        box.append(go_btn)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", lambda _: self._update_banner_revealer.set_reveal_child(False))
        box.append(close_btn)

        outer.append(box)

        self._update_banner_revealer = Gtk.Revealer()
        self._update_banner_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._update_banner_revealer.set_transition_duration(300)
        self._update_banner_revealer.set_child(outer)
        self._update_banner_revealer.set_reveal_child(False)
        return self._update_banner_revealer


    def _build_preset_button(self) -> Gtk.MenuButton:
        self._preset_btn = Gtk.MenuButton()
        self._preset_btn.add_css_class("flat")

        self._preset_popover = Gtk.Popover()
        self._preset_popover.set_has_arrow(False)
        self._preset_btn.set_popover(self._preset_popover)

        self._refresh_preset_menu()
        return self._preset_btn

    def _refresh_preset_menu(self):
        presets = profile_module.list_presets()
        active_name = config.state_get("active_preset")

        display_name = active_name or "Default"
        _btn_lbl = Gtk.Label(label=display_name)
        _btn_lbl.set_max_width_chars(9)
        _btn_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        _btn_lbl.set_tooltip_text(display_name if len(display_name) > 9 else None)
        self._preset_btn.set_child(_btn_lbl)
        self._preset_btn.set_always_show_arrow(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        def _flat_row(label: str, icon: str, cb, sensitive: bool = True) -> Gtk.Button:
            row = Gtk.Box(spacing=8)
            row.set_margin_start(4)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(16)
            row.append(img)
            lbl_w = Gtk.Label(label=label)
            lbl_w.set_xalign(0.0)
            lbl_w.set_hexpand(True)
            row.append(lbl_w)
            btn = Gtk.Button()
            btn.set_child(row)
            btn.add_css_class("flat")
            btn.set_sensitive(sensitive)
            btn.connect("clicked", cb)
            return btn

        if presets:
            for p_name, _ in presets:
                row = Gtk.Box(spacing=8)
                row.set_margin_start(4)
                check_img = Gtk.Image.new_from_icon_name("object-select-symbolic")
                check_img.set_pixel_size(16)
                check_img.set_opacity(1.0 if p_name == active_name else 0.0)
                row.append(check_img)
                lbl_w = Gtk.Label(label=p_name)
                lbl_w.set_xalign(0.0)
                lbl_w.set_hexpand(True)
                row.append(lbl_w)
                btn = Gtk.Button()
                btn.set_child(row)
                btn.add_css_class("flat")
                btn.connect("clicked", lambda _, n=p_name: self._on_preset_selected(n))
                box.append(btn)
        else:
            placeholder = Gtk.Label(label="Нет сохранённых пресетов")
            placeholder.add_css_class("dim-label")
            placeholder.set_margin_top(6)
            placeholder.set_margin_bottom(6)
            placeholder.set_margin_start(8)
            box.append(placeholder)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        has_active = bool(active_name and any(n == active_name for n, _ in presets))
        box.append(_flat_row("Сохранить как новый…",    "list-add-symbolic",        self._on_preset_save_new))
        box.append(_flat_row("Переименовать текущий…",  "document-edit-symbolic",   self._on_preset_rename,  has_active))
        box.append(_flat_row("Удалить текущий",         "user-trash-symbolic",      self._on_preset_delete,  has_active))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        box.append(_flat_row("Экспортировать в файл…",  "document-send-symbolic",   self._on_preset_export_file))
        box.append(_flat_row("Импортировать из файла…", "document-open-symbolic",   self._on_preset_import_file))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        box.append(_flat_row("Экспорт расширений…",    "application-x-addon-symbolic",       self._on_export_extensions))
        box.append(_flat_row("Экспорт приложений…",    "application-x-executable-symbolic",  self._on_export_apps))

        self._preset_popover.set_child(box)

    def _on_preset_selected(self, name: str):
        self._preset_popover.popdown()
        for p_name, path in profile_module.list_presets():
            if p_name == name:
                try:
                    data = profile_module.load_preset(path)
                    show_preset_import_dialog(self, data, self._do_apply_preset)
                except Exception as e:
                    self._log(f"✘ Ошибка загрузки пресета: {e}\n")
                return

    def _on_preset_save_new(self, *_):
        self._preset_popover.popdown()
        existing = [n for n, _ in profile_module.list_presets()]
        show_preset_save_dialog(self, existing, self._do_save_preset)

    def _on_preset_rename(self, *_):
        self._preset_popover.popdown()
        active_name = config.state_get("active_preset")
        if not active_name:
            return
        existing = [n for n, _ in profile_module.list_presets() if n != active_name]

        def _do_rename(new_name: str):
            for p_name, path in profile_module.list_presets():
                if p_name == active_name:
                    try:
                        data = profile_module.load_preset(path)
                        new_path = profile_module.save_preset(data, new_name)
                        if new_path != path:
                            path.unlink(missing_ok=True)
                        config.state_set("active_preset", new_name)
                        self._refresh_preset_menu()
                        self._log(f"✔ Пресет переименован: «{active_name}» → «{new_name}»\n")
                    except Exception as e:
                        self._log(f"✘ Ошибка переименования: {e}\n")
                    return

        show_preset_save_dialog(self, existing, _do_rename)

    def _on_preset_delete(self, *_):
        self._preset_popover.popdown()
        active_name = config.state_get("active_preset")
        if not active_name:
            return

        d = Adw.AlertDialog(
            heading=f"Удалить пресет «{active_name}»?",
            body="Файл пресета будет удалён. Это действие нельзя отменить.",
        )
        d.add_response("cancel", "Отмена")
        d.add_response("delete", "Удалить")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def _on_resp(_, r):
            if r != "delete":
                return
            for p_name, path in profile_module.list_presets():
                if p_name == active_name:
                    try:
                        path.unlink(missing_ok=True)
                        config.state_set("active_preset", None)
                        self._refresh_preset_menu()
                        self._log(f"🗑 Пресет «{active_name}» удалён.\n")
                    except Exception as e:
                        self._log(f"✘ Ошибка удаления: {e}\n")
                    return

        d.connect("response", _on_resp)
        d.present(self)

    def _on_preset_export_file(self, *_):
        self._preset_popover.popdown()
        active_name = config.state_get("active_preset") or "Default"

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in active_name).strip()
        filename = f"{safe_name}-{date_str}.altbooster"

        dialog = Gtk.FileDialog()
        dialog.set_title("Экспортировать пресет")
        dialog.set_initial_name(filename)

        def _on_save(d, res):
            try:
                file = d.save_finish(res)
                if not file:
                    return
                for p_name, path in profile_module.list_presets():
                    if p_name == active_name:
                        shutil.copy2(path, file.get_path())
                        self._log(f"✔ Пресет «{active_name}» экспортирован в {file.get_path()}\n")
                        self.add_toast(Adw.Toast(title="Пресет экспортирован"))
                        return
                dest = file.get_path()
                self._log(f"💾 Собираю текущее состояние для экспорта...\n")

                def _collect():
                    try:
                        data = profile_module.collect_profile(active_name, self._apps._data)
                        Path(dest).write_text(
                            json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        GLib.idle_add(self._log, f"✔ Пресет экспортирован в {dest}\n")
                        GLib.idle_add(self.add_toast, Adw.Toast(title="Пресет экспортирован"))
                    except Exception as e:
                        GLib.idle_add(self._log, f"✘ Ошибка экспорта: {e}\n")

                threading.Thread(target=_collect, daemon=True).start()
            except GLib.Error as e:
                if e.code != 2:
                    self._log(f"✘ Ошибка экспорта: {e}\n")

        self._file_dialog = dialog
        dialog.save(self, None, _on_save)

    def _on_preset_import_file(self, *_):
        self._preset_popover.popdown()
        dialog = Gtk.FileDialog()
        dialog.set_title("Импортировать пресет")

        f_all = Gtk.FileFilter()
        f_all.set_name("Файлы ALT Booster (*.altbooster, *.json)")
        f_all.add_pattern("*.altbooster")
        f_all.add_pattern("*.json")

        f_preset = Gtk.FileFilter()
        f_preset.set_name("Пресеты ALT Booster (*.altbooster)")
        f_preset.add_pattern("*.altbooster")

        f_json = Gtk.FileFilter()
        f_json.set_name("JSON-экспорт (*.json)")
        f_json.add_pattern("*.json")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f_all)
        filters.append(f_preset)
        filters.append(f_json)
        dialog.set_filters(filters)

        def _on_open(d, res):
            try:
                file = d.open_finish(res)
                if file:
                    self._load_and_show_preset(file.get_path())
            except GLib.Error as e:
                if e.code != 2:
                    self._log(f"✘ Ошибка выбора файла: {e}\n")

        self._file_dialog = dialog
        dialog.open(self, None, _on_open)

    def _on_export_extensions(self, *_):
        self._preset_popover.popdown()

        d = Adw.AlertDialog(
            heading="Экспорт расширений",
            body="Что включить в файл экспорта?",
        )
        d.add_response("list",   "Только список")
        d.add_response("full",   "Список + настройки")
        d.add_response("cancel", "Отмена")
        d.set_default_response("full")
        d.set_close_response("cancel")

        def _on_choice(_, response):
            if response == "cancel":
                return

            include_dconf = (response == "full")

            def _collect_and_save():
                try:
                    r_list = subprocess.run(
                        ["gnome-extensions", "list", "--enabled"],
                        capture_output=True, text=True, timeout=10,
                    )
                    enabled = [u.strip() for u in r_list.stdout.splitlines() if u.strip()]

                    ext_data: dict = {"altbooster_extensions_backup": True, "extensions": enabled}

                    if include_dconf:
                        r_dconf = subprocess.run(
                            ["dconf", "dump", "/org/gnome/shell/extensions/"],
                            capture_output=True, text=True, timeout=10,
                        )
                        ext_data["extensions_dconf"] = r_dconf.stdout if r_dconf.returncode == 0 else ""

                    GLib.idle_add(_show_save_dialog, ext_data)
                except Exception as e:
                    GLib.idle_add(self._log, f"✘ Ошибка сбора данных расширений: {e}\n")

            def _show_save_dialog(ext_data):
                date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                suffix = "-full" if include_dconf else "-list"
                filename = f"extensions{suffix}-{date_str}.json"

                fdialog = Gtk.FileDialog()
                fdialog.set_title("Экспорт расширений")
                fdialog.set_initial_name(filename)

                flt = Gtk.FileFilter()
                flt.set_name("JSON (*.json)")
                flt.add_pattern("*.json")
                filters = Gio.ListStore.new(Gtk.FileFilter)
                filters.append(flt)
                fdialog.set_filters(filters)

                def _on_save(fd, res):
                    try:
                        file = fd.save_finish(res)
                        if not file:
                            return
                        Path(file.get_path()).write_text(
                            json.dumps(ext_data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        self._log(f"✔ Расширения экспортированы в {file.get_path()}\n")
                        self.add_toast(Adw.Toast(title="Расширения экспортированы"))
                    except GLib.Error as e:
                        if e.code != 2:
                            self._log(f"✘ Ошибка сохранения: {e}\n")

                self._file_dialog = fdialog
                fdialog.save(self, None, _on_save)

            threading.Thread(target=_collect_and_save, daemon=True).start()

        d.connect("response", _on_choice)
        d.present(self)

    def _on_export_apps(self, *_):
        self._preset_popover.popdown()

        self._log("💾 Собираю список установленных приложений...\n")

        def _collect():
            try:
                apps = profile_module._get_installed_apps(self._apps._data)
                apps_data = {
                    "altbooster_apps_backup": True,
                    "apps": apps,
                }
                GLib.idle_add(_show_save_dialog, apps_data)
            except Exception as e:
                GLib.idle_add(self._log, f"✘ Ошибка сбора приложений: {e}\n")

        def _show_save_dialog(apps_data):
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")

            fdialog = Gtk.FileDialog()
            fdialog.set_title("Экспорт приложений")
            fdialog.set_initial_name(f"apps-{date_str}.json")

            flt = Gtk.FileFilter()
            flt.set_name("JSON (*.json)")
            flt.add_pattern("*.json")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(flt)
            fdialog.set_filters(filters)

            def _on_save(fd, res):
                try:
                    file = fd.save_finish(res)
                    if not file:
                        return
                    Path(file.get_path()).write_text(
                        json.dumps(apps_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    count = len(apps_data["apps"])
                    self._log(f"✔ Приложения экспортированы ({count} шт.) в {file.get_path()}\n")
                    self.add_toast(Adw.Toast(title=f"Приложения экспортированы ({count})"))
                except GLib.Error as e:
                    if e.code != 2:
                        self._log(f"✘ Ошибка сохранения: {e}\n")

            self._file_dialog = fdialog
            fdialog.save(self, None, _on_save)

        threading.Thread(target=_collect, daemon=True).start()

    def _load_and_show_preset(self, path_str: str):
        try:
            data = profile_module.load_preset(Path(path_str))
            show_preset_import_dialog(self, data, self._do_apply_preset)
        except Exception as e:
            self._log(f"✘ Ошибка чтения пресета: {e}\n")

    def _do_save_preset(self, name: str):
        self._log(f"💾 Сохраняю пресет «{name}»...\n")

        def _worker():
            try:
                data = profile_module.collect_profile(name, self._apps._data)
                profile_module.save_preset(data, name)
                config.state_set("active_preset", name)
                GLib.idle_add(self._refresh_preset_menu)
                GLib.idle_add(self._log, f"✔ Пресет «{name}» сохранён.\n")
                GLib.idle_add(self.add_toast, Adw.Toast(title=f"Пресет «{name}» сохранён"))
            except Exception as e:
                GLib.idle_add(self._log, f"✘ Ошибка сохранения пресета: {e}\n")

        threading.Thread(target=_worker, daemon=True).start()

    def _find_app_install_cmd(self, app_info: dict) -> list | None:
        app_id = app_info.get("id")
        source_label = app_info.get("source_label", "")
        for group in self._apps._data.get("groups", []):
            for item in group.get("items", []):
                if item.get("id") != app_id:
                    continue
                sources = item.get("sources") or (
                    [item["source"]] if item.get("source") else []
                )
                for src in sources:
                    if not source_label or src.get("label") == source_label:
                        return src.get("cmd")
                if sources:
                    return sources[0].get("cmd")
        return None

    def _do_apply_preset(self, data: dict, flags: dict):
        name = data.get("name", "Пресет")

        deferred_settings: list[dict] = []
        if flags.get("settings"):
            deferred_settings = profile_module.apply_settings(data)
            self._log("✔ Настройки из пресета применены.\n")
            if data.get("custom_apps"):
                GLib.idle_add(self._apps._load_and_build)

        config.state_set("active_preset", name)
        GLib.idle_add(self._refresh_preset_menu)

        cmds: list[tuple[str, list, str]] = []

        if flags.get("apps"):
            for app_info in data.get("apps") or []:
                cmd = self._find_app_install_cmd(app_info)
                if cmd:
                    kind = "epm" if cmd and cmd[0] == "epm" else "privileged"
                    cmds.append((app_info.get("label", app_info["id"]), cmd, kind))

        for entry in deferred_settings:
            pkg = profile_module.theme_package(entry.get("value", ""))
            if pkg:
                cmds.append((entry["value"], ["apt-get", "install", "-y", pkg], "privileged"))

        if flags.get("extensions"):
            _system_ext_dir = Path("/usr/share/gnome-shell/extensions")
            gext = shutil.which("gext") or str(
                Path.home() / ".local" / "bin" / "gext"
            )

            installed_uuids = set()
            try:
                r_list = subprocess.run(["gnome-extensions", "list"], capture_output=True, text=True)
                installed_uuids = set(line.strip() for line in r_list.stdout.splitlines() if line.strip())
            except Exception:
                pass

            for uuid in data.get("extensions") or []:
                if uuid in installed_uuids or (_system_ext_dir / uuid).exists():
                    continue
                cmds.append((uuid, [gext, "install", uuid], "shell"))

        if not cmds:
            self.add_toast(Adw.Toast(title=f"Пресет «{name}» применён"))
            return

        total = len(cmds)
        self._log(f"▶ Применяю пресет «{name}»: {total} операций...\n")
        self.start_progress(f"Применяю пресет «{name}»")

        def _worker():
            ok_count = 0

            has_flatpak_src = any(
                cmd and cmd[0] == "flatpak"
                for _, cmd, _ in cmds
            )
            if has_flatpak_src and not shutil.which("flatpak"):
                GLib.idle_add(self._log, "▶ Flatpak не найден, устанавливаю...\n")
                backend.run_epm_sync(["epm", "-i", "-y", "flatpak"], lambda l: GLib.idle_add(self._log, l))

            for label, cmd, kind in cmds:
                GLib.idle_add(self._log, f"📦 {label}...\n")
                if kind == "epm":
                    ok = backend.run_epm_sync(cmd, lambda l: GLib.idle_add(self._log, l))
                elif kind == "shell":
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    if r.stdout:
                        GLib.idle_add(self._log, r.stdout)
                    ok = r.returncode == 0
                else:
                    ok = backend.run_privileged_sync(cmd, lambda l: GLib.idle_add(self._log, l))
                if ok:
                    ok_count += 1

            for entry in deferred_settings:
                if profile_module.theme_exists(entry.get("value", "")):
                    backend.run_gsettings(["set", entry["schema"], entry["key"], entry["value"]])

            dconf_text = data.get("extensions_dconf", "")
            if dconf_text and flags.get("extensions"):
                GLib.idle_add(self._log, "▶ Восстанавливаю конфиги расширений...\n")
                try:
                    proc = subprocess.run(
                        ["dconf", "load", "/org/gnome/shell/extensions/"],
                        input=dconf_text, text=True, capture_output=True,
                    )
                    if proc.returncode == 0:
                        GLib.idle_add(self._log, "✔ Конфиги расширений восстановлены.\n")
                    else:
                        GLib.idle_add(self._log, f"⚠ dconf load: {proc.stderr.strip()}\n")
                except Exception as e:
                    GLib.idle_add(self._log, f"⚠ dconf load: {e}\n")

            GLib.idle_add(self.stop_progress, ok_count == total)
            GLib.idle_add(
                self.add_toast,
                Adw.Toast(title=f"Пресет «{name}» применён ({ok_count}/{total})"),
            )

        threading.Thread(target=_worker, daemon=True).start()


    def _build_profile_banner(self) -> Gtk.Revealer:
        outer = Gtk.Box()
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_top(4)
        outer.set_margin_bottom(2)
        outer.set_opacity(0.92)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("ab-float-banner")

        icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        icon.set_pixel_size(16)
        box.append(icon)

        self._profile_banner_label = Gtk.Label()
        self._profile_banner_label.set_xalign(0.0)
        box.append(self._profile_banner_label)

        import_btn = Gtk.Button(label="Импортировать")
        import_btn.add_css_class("suggested-action")
        import_btn.add_css_class("pill")
        import_btn.set_valign(Gtk.Align.CENTER)
        import_btn.connect("clicked", self._on_profile_banner_import)
        box.append(import_btn)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", self._on_profile_banner_dismiss)
        box.append(close_btn)

        outer.append(box)

        self._profile_banner_revealer = Gtk.Revealer()
        self._profile_banner_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._profile_banner_revealer.set_transition_duration(300)
        self._profile_banner_revealer.set_child(outer)
        self._profile_banner_revealer.set_reveal_child(False)
        self._profile_banner_path = None
        return self._profile_banner_revealer


    def _build_relogin_banner(self) -> Gtk.Revealer:
        btn = Gtk.Button()
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name("system-log-out-symbolic")
        icon.set_pixel_size(16)
        btn_box.append(icon)
        btn_box.append(Gtk.Label(label="Перезайти в сессию"))
        btn.set_child(btn_box)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_margin_end(16)
        btn.set_margin_bottom(16)
        btn.set_halign(Gtk.Align.END)
        btn.set_valign(Gtk.Align.END)
        btn.connect("clicked", self._on_relogin_clicked)

        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        revealer.set_transition_duration(300)
        revealer.set_child(btn)
        revealer.set_reveal_child(False)
        revealer.set_halign(Gtk.Align.END)
        revealer.set_valign(Gtk.Align.END)
        revealer.set_can_target(False)
        return revealer

    def show_relogin_banner(self):
        self._relogin_revealer.set_can_target(True)
        self._relogin_revealer.set_reveal_child(True)

    def _on_relogin_clicked(self, _btn):
        self._relogin_revealer.set_reveal_child(False)
        self._relogin_revealer.set_can_target(False)
        subprocess.Popen(["gnome-session-quit", "--logout", "--no-prompt"])

    def _check_for_import_candidates(self):
        def _find():
            candidates = profile_module.find_import_candidates()
            dismissed = set(config.state_get("dismissed_profiles") or [])
            for path in candidates:
                if str(path) not in dismissed:
                    GLib.idle_add(self._show_profile_banner, path)
                    return

        threading.Thread(target=_find, daemon=True).start()

    def _show_profile_banner(self, path):
        self._profile_banner_path = path
        self._profile_banner_label.set_text(f"Найден пресет: {path.name}")
        self._profile_banner_revealer.set_reveal_child(True)

    def _on_profile_banner_import(self, *_):
        self._profile_banner_revealer.set_reveal_child(False)
        if self._profile_banner_path:
            self._load_and_show_preset(str(self._profile_banner_path))

    def _on_profile_banner_dismiss(self, *_):
        self._profile_banner_revealer.set_reveal_child(False)
        if self._profile_banner_path:
            dismissed = list(config.state_get("dismissed_profiles") or [])
            key = str(self._profile_banner_path)
            if key not in dismissed:
                dismissed.append(key)
                config.state_set("dismissed_profiles", dismissed)

    def _on_update_found_global(self, version):
        self._update_banner_label.set_text(f"Доступна новая версия {version}")
        self._update_banner_revealer.set_reveal_child(True)
        self._update_badge_dot.set_visible(True)

    def _go_to_update(self, *_):
        self._update_banner_revealer.set_reveal_child(False)
        self._stack.set_visible_child_name("setup")


    def _build_log_panel(self):
        self._last_log_line = ""
        self._progress_nesting = 0
        self._on_cancel_cb = None
        self._log_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._status_label = Gtk.Label(label="Ожидание авторизации...")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_margin_start(12)
        self._status_label.set_margin_top(12)
        self._status_label.set_margin_bottom(6)
        self._status_label.add_css_class("heading")
        self._log_container.append(self._status_label)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.set_margin_start(12)
        hbox.set_margin_end(12)
        hbox.set_margin_bottom(12)

        self._progressbar = Gtk.ProgressBar()
        self._progressbar.set_hexpand(True)
        self._progressbar.set_valign(Gtk.Align.CENTER)
        hbox.append(self._progressbar)

        self._stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self._stop_btn.add_css_class("flat")
        self._stop_btn.add_css_class("circular")
        self._stop_btn.set_tooltip_text("Отменить")
        self._stop_btn.set_sensitive(False)
        self._stop_btn.set_visible(False)
        self._stop_btn.connect("clicked", self._on_stop_clicked)
        hbox.append(self._stop_btn)

        self._log_container.append(hbox)

        self._log_expander = Gtk.Expander(label="Лог терминала")
        self._log_expander.set_margin_start(12)
        self._log_expander.set_margin_end(12)
        self._log_expander.set_margin_bottom(12)

        self._log_scroll = Gtk.ScrolledWindow()
        self._log_scroll.set_vexpand(False)
        self._log_scroll.set_size_request(-1, 250)
        self._log_scroll.set_min_content_height(50)

        self._tv = Gtk.TextView()
        self._tv.set_editable(False)
        self._tv.set_monospace(True)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.set_left_margin(10)
        self._tv.set_right_margin(10)
        self._tv.set_top_margin(10)
        self._tv.set_bottom_margin(10)
        self._buf = self._tv.get_buffer()
        self._log_scroll.set_child(self._tv)
        self._log_expander.set_child(self._log_scroll)
        self._log_container.append(self._log_expander)

        return self._log_container


    def _setup_logging(self):
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            if self._log_file.exists() and self._log_file.stat().st_size > 2 * 1024 * 1024:
                shutil.move(self._log_file, self._log_file.with_suffix(".log.old"))

            sys_info = [f"v{config.VERSION}"]
            try:
                sys_info.append(f"Kernel: {platform.release()}")
                sys_info.append(f"DE: {os.environ.get('XDG_CURRENT_DESKTOP', 'Unknown')}")
                with open("/proc/meminfo") as f:
                    for line in f:
                        if "MemTotal" in line:
                            sys_info.append(f"Mem: {line.split(':')[1].strip()}")
                            break
            except Exception:
                pass

            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(f"\n=== Session started {datetime.datetime.now()} [{' | '.join(sys_info)}] ===\n")
        except Exception as e:
            print(f"Log setup failed: {e}")


    def ask_password(self):
        self._maint.set_sensitive_all(False)

        def _check():
            if not shutil.which("sudo"):
                GLib.idle_add(self._log, "ℹ Sudo не найден. Включен режим pkexec.\n")
                GLib.idle_add(self._use_pkexec_auth)
                return

            try:
                if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=1).returncode == 0:
                    backend.set_sudo_nopass(True)
                    GLib.idle_add(self._auth_ok)
                    return
            except Exception:
                pass

            GLib.idle_add(self._log, "ℹ Инициализация pkexec...\n")
            backend.set_pkexec_mode(True)
            ok, is_cancel = backend.start_pkexec_shell()
            if ok:
                GLib.idle_add(self._auth_ok)
            elif is_cancel:
                GLib.idle_add(self._log, "⚠ Аутентификация отменена пользователем.\n")
                GLib.idle_add(self.close)
            else:
                GLib.idle_add(self._log, "⚠ pkexec недоступен. Закрытие приложения.\n")
                GLib.idle_add(self.close)

        threading.Thread(target=_check, daemon=True).start()

    def _use_pkexec_auth(self):
        backend.set_pkexec_mode(True)
        self._log("🔑 Используется pkexec (polkit) для привилегированных команд.\n")
        self._auth_ok()

    def _restart_app(self):
        GLib.timeout_add(600, self._do_restart)

    def _do_restart(self):
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            subprocess.Popen([sys.executable] + sys.argv)
            self.get_application().quit()
        return False

    def _auth_ok(self):
        self._maint.set_sensitive_all(True)
        self._maint.refresh_checks()
        self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")
        self._status_label.set_label("Готов к работе")
        GLib.idle_add(self._check_for_import_candidates)


    def _load_settings(self):
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _):
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE, "w") as f:
                json.dump({
                    "width": self.get_width(),
                    "height": self.get_height(),
                    "sidebar_width": self._split_view.get_position(),
                }, f)
        except OSError:
            pass
        self._log_queue.put(None)
        return False


    def _check_for_updates(self, *_):
        if self._stack.get_visible_child_name() == "setup":
            if self._setup.dismiss_update_section():
                return
        self._stack.set_visible_child_name("setup")
        self._update_badge_dot.set_visible(False)
        self._setup.check_for_updates(manual=True, on_update_found=self._on_update_found_global)

    def _show_about(self, *_):
        d = Adw.AboutDialog()
        d.set_application_name("ALT Booster")
        d.set_application_icon("altbooster")
        d.set_developer_name("PLAFON")
        d.set_version(config.VERSION)
        d.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        d.set_comments("ALT Booster — утилита-компаньон для настройки ALT Рабочая станция (GNOME)")
        d.set_license_type(Gtk.License.MIT_X11)
        d.set_developers(["PLAFON"])
        d.set_copyright("© 2026 PLAFON")
        d.add_link("📖 ALT Zero", "https://plafon.gitbook.io/alt-zero")
        d.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        d.add_link("✈ Telegram", "https://t.me/plafonyoutube")
        d.add_link("✈ Чат", "https://t.me/plafonchat")
        d.present(self)

    def _clear_log(self, *_):
        self._buf.set_text("")
        self._last_log_line = ""

    def _reset_state(self, *_):
        d = Adw.AlertDialog(
            heading="Очистить кэш?",
            body="Все сохранённые статусы проверок будут удалены.\n"
                 "Утилита заново опросит систему при следующем запуске.",
        )
        d.add_response("cancel", "Отмена")
        d.add_response("reset", "Очистить")
        d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def _on_response(_d, r):
            if r == "reset":
                config.reset_state()
                self._log("🔄 Кэш статусов очищен.\n")
                GLib.timeout_add(1500, self.close)

        d.connect("response", _on_response)
        d.present(self)

    def _reset_config(self, *_):
        dialog = Adw.AlertDialog(
            heading="Сброс настроек приложения?",
            body="Внимание! Это действие удалит все ваши настройки, списки приложений и кэш.\n"
                 "Приложение будет перезапущено в состоянии «как после установки».",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reset", "Сбросить")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "reset":
                self._log("▶  Сброс конфигурации...\n")
                try:
                    if os.path.exists(config.CONFIG_DIR):
                        shutil.rmtree(config.CONFIG_DIR)
                    self._log("✔  Конфигурация удалена. Перезапуск...\n")
                    os.execl(sys.executable, sys.executable, *sys.argv)
                except Exception as e:
                    self._log(f"✘  Ошибка сброса: {e}\n")
            else:
                self._log("ℹ  Пользователь отменил действие.\n")

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _open_log_file(self, *_):
        if not self._log_file.exists():
            self.add_toast(Adw.Toast(title="Файл логов еще не создан"))
            return

        path = str(self._log_file)
        cmd = []

        if shutil.which("gnome-text-editor"):
            cmd = ["gnome-text-editor", path]
        elif shutil.which("gedit"):
            cmd = ["gedit", path]
        elif shutil.which("nano"):
            term = shutil.which("ptyxis") or shutil.which("gnome-terminal") or shutil.which("kgx")
            if term:
                cmd = [term, "--", "nano", path]

        if cmd:
            try:
                subprocess.Popen(cmd)
                return
            except Exception:
                pass

        Gio.AppInfo.launch_default_for_uri(self._log_file.as_uri(), None)

    def add_toast(self, toast):
        self._toast_overlay.add_toast(toast)


    def start_progress(self, message: str, on_cancel=None):
        if on_cancel is not None:
            self._on_cancel_cb = on_cancel

        def _do():
            if on_cancel is not None:
                self._progress_nesting = 1
            else:
                self._progress_nesting += 1
            self._status_label.set_label(message)
            self._progressbar.set_fraction(0.0)
            self._stop_btn.set_sensitive(bool(self._on_cancel_cb))
            self._stop_btn.set_visible(bool(self._on_cancel_cb))
            if self._pulse_timer_id:
                GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = GLib.timeout_add(100, self._pulse_progress)
            if self._reset_status_timer_id:
                GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = None

        GLib.idle_add(_do)

    def _on_stop_clicked(self, _):
        if not self._on_cancel_cb:
            return

        dialog = Adw.AlertDialog(
            heading="Остановить операцию?",
            body="Текущий процесс будет прерван. Это может привести к незавершенным изменениям.",
        )
        dialog.add_response("cancel", "Нет")
        dialog.add_response("stop", "Да, остановить")
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "stop" and self._on_cancel_cb:
                self._status_label.set_label("Отмена...")
                self._stop_btn.set_sensitive(False)
                self._on_cancel_cb()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _pulse_progress(self):
        self._progressbar.pulse()
        return True

    def stop_progress(self, success: bool = True):
        def _do():
            if self._pulse_timer_id:
                GLib.source_remove(self._pulse_timer_id)
                self._pulse_timer_id = None
            self._progress_nesting = max(0, self._progress_nesting - 1)
            self._progressbar.set_fraction(1.0)
            label = self._last_log_line or ("✔ Готово" if success else "✘ Ошибка")
            self._status_label.set_label(label)
            if self._progress_nesting == 0:
                self._stop_btn.set_sensitive(False)
                self._stop_btn.set_visible(False)
                self._on_cancel_cb = None
                if self._reset_status_timer_id:
                    GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = GLib.timeout_add(4000, self._reset_status_label)

        GLib.idle_add(_do)

    def _reset_status_label(self):
        self._reset_status_timer_id = None
        self._status_label.set_label("Готов к работе")
        return False


    def _log(self, text):
        GLib.idle_add(self._log_internal, text)

    def _log_internal(self, text):
        stripped = text.strip()
        if stripped:
            self._last_log_line = stripped

        self._log_queue.put(text)

        end = self._buf.get_end_iter()
        self._buf.insert(end, text)
        end = self._buf.get_end_iter()
        mark = self._buf.get_mark("log_end")
        if mark is None:
            mark = self._buf.create_mark("log_end", end, False)
        else:
            self._buf.move_mark(mark, end)
        self._tv.scroll_mark_onscreen(mark)

    def _log_writer_loop(self):
        self._setup_logging()
        try:
            log_f = open(self._log_file, "a", encoding="utf-8")
        except Exception:
            log_f = None

        while True:
            text = self._log_queue.get()
            if text is None:
                break
            if log_f is None:
                continue
            try:
                log_f.write(text)
                log_f.flush()
            except Exception:
                pass

        if log_f:
            try:
                log_f.close()
            except Exception:
                pass

