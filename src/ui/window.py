
import datetime
import json
import os
import platform
import queue
import re
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

from core import backend
from core import config
from core.checks import invalidate_app_detection_caches

_ALT_ZERO_GUIDE_URL = "https://plafon.gitbook.io/alt-zero"

# borg create --progress: "2.88 GB O 1.70 GB C 1.60 GB D 14576 N path/to/file"
_BORG_CREATE_PROGRESS_RE = re.compile(
    r"^([\d.,]+\s+\S+)\s+O\s+([\d.,]+\s+\S+)\s+C\s+([\d.,]+\s+\S+)\s+D\s+"
    r"(?:[\d.,]+\s+%\s+)?"
    r"(\d+)\s+N\s*(.*)$"
)
from tabs.setup import SetupPage
from tabs.apps import AppsPage
from tabs.extensions import ExtensionsPage
from tabs.terminal import TerminalPage
from tabs.davinci import DaVinciPage
from tabs.amd import AmdPage
from tabs.maintenance import MaintenancePage
from tabs.tweaks import TweaksPage
from tabs.flatpak import FlatpakPage
from tabs.timesync import BorgPage


class AltBoosterWindow(Adw.ApplicationWindow):
    _MAIN_TABS = [
        ("setup",       "Начало",          "go-home-symbolic",             SetupPage),
        ("apps",        "Приложения",      "grid-large-symbolic",          AppsPage),
        ("extensions",  "Расширения",      "application-x-addon-symbolic", ExtensionsPage),
        ("flatpak",     "Flatpak",         "flatpak-symbolic",             FlatpakPage),
        ("terminal",    "Терминал",        "utilities-terminal-symbolic",  TerminalPage),
        ("amd",         "AMD Radeon",      "video-display-symbolic",       AmdPage),
        ("davinci",     "DaVinci Resolve", "davinci-symbolic",             DaVinciPage),
        ("maintenance", "Обслуживание",    "emblem-system-symbolic",       MaintenancePage),
        ("tweaks",      "Твики",           "applications-engineering-symbolic", TweaksPage),
    ]
    _BORG_TAB = ("borg", "TimeSync", "drive-harddisk-symbolic", BorgPage)

    def __init__(self, **kwargs):
        start_time = time.time()
        super().__init__(**kwargs)

        icon_theme = "Adwaita"
        if not os.path.exists("/usr/share/icons/Adwaita") and os.path.exists("/usr/share/icons/alt-workstation"):
            icon_theme = "alt-workstation"
        Gtk.Settings.get_default().set_property("gtk-icon-theme-name", icon_theme)

        _icons_base = Path(__file__).parent.parent.parent / "icons"

        _hicolor_src = _icons_base / "hicolor"
        _dst_hicolor = Path.home() / ".local" / "share" / "icons" / "hicolor"
        _icons_copied = False
        for _kind, _cat in (("scalable", "apps"), ("scalable", "devices"), ("symbolic", "devices")):
            _src_cat = _hicolor_src / _kind / _cat
            _dst_cat = _dst_hicolor / _kind / _cat
            if not _src_cat.exists():
                continue
            _dst_cat.mkdir(parents=True, exist_ok=True)
            for _svg in _src_cat.glob("*.svg"):
                _dst = _dst_cat / _svg.name
                try:
                    _src_st = _svg.stat()
                    _dst_st = _dst.stat() if _dst.exists() else None
                    if not _dst_st or _dst_st.st_size != _src_st.st_size or _dst_st.st_mtime < _src_st.st_mtime:
                        shutil.copy2(_svg, _dst)
                        _icons_copied = True
                except OSError:
                    pass

        if _icons_copied:
            def _update_icon_cache(_dst_hicolor=_dst_hicolor):
                try:
                    subprocess.run(
                        ["gtk-update-icon-cache", "-f", "-t", str(_dst_hicolor)],
                        capture_output=True, timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            threading.Thread(target=_update_icon_cache, daemon=True).start()

        _it = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        _it.add_search_path(str(_icons_base))

        self._reset_status_timer_id = None
        self._last_app_detection_cache_flush = 0.0
        self._progress_message = ""
        self._op_card_pct: float | None = None
        self._log_queue = queue.SimpleQueue()
        self._log_widget = self._build_log_panel()

        self.set_title("ALT Booster")
        settings = self._load_settings()

        self._log_file = config.CONFIG_DIR / "altbooster.log"
        threading.Thread(target=self._log_writer_loop, daemon=True).start()

        self.set_default_size(settings.get("width", 740), settings.get("height", 880))
        self.connect("close-request", self._on_close)
        self.connect("notify::is-active", self._on_window_is_active)


        header_widget = self._build_header()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("ab-main-content")
        root.append(self._build_update_banner())

        self._pages = {}
        for name, title, icon, PageClass in self._MAIN_TABS:
            page = PageClass(self._log)
            self._pages[name] = page
            p = self._stack.add_titled(page, name, title)
            p.set_icon_name(icon)

        borg_name, borg_title, borg_icon, BorgClass = self._BORG_TAB
        borg_page = BorgClass(self._log, self.start_progress, self.stop_progress)
        self._pages[borg_name] = borg_page
        p = self._stack.add_titled(borg_page, borg_name, borg_title)
        p.set_icon_name(borg_icon)

        self._setup = self._pages["setup"]
        self._maint = self._pages["maintenance"]
        self._borg  = self._pages["borg"]

        self._stack.set_vexpand(True)
        self._stack.connect("notify::visible-child", self._on_stack_child_changed)
        stack_overlay = Gtk.Overlay()
        stack_overlay.set_child(self._stack)
        stack_overlay.set_vexpand(True)

        self._global_search_btn = Gtk.Button()
        self._global_search_btn.set_icon_name("system-search-symbolic")
        self._global_search_btn.set_hexpand(False)
        self._global_search_btn.set_vexpand(False)
        self._global_search_btn.add_css_class("circular")
        self._global_search_btn.add_css_class("ab-global-search-btn")
        self._global_search_btn.set_tooltip_text("По утилите Ctrl + K")
        self._global_search_btn.connect("clicked", self._present_global_search)

        self._log_overlay_btn = Gtk.Button()
        self._log_overlay_btn.set_icon_name("terminal-log-symbolic")
        self._log_overlay_btn.set_hexpand(False)
        self._log_overlay_btn.set_vexpand(False)
        self._log_overlay_btn.add_css_class("circular")
        self._log_overlay_btn.add_css_class("ab-global-search-btn")
        self._log_overlay_btn.set_tooltip_text("Лог терминала")
        self._log_overlay_btn.connect("clicked", lambda *_: self._open_log_overlay())

        _fab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _fab_box.set_halign(Gtk.Align.END)
        _fab_box.set_valign(Gtk.Align.END)
        _fab_box.set_margin_end(16)
        _fab_box.set_margin_bottom(16)
        _fab_box.append(self._log_overlay_btn)
        _fab_box.append(self._global_search_btn)

        self._op_card = self._build_op_card()
        stack_overlay.add_overlay(_fab_box)
        stack_overlay.set_measure_overlay(_fab_box, False)
        stack_overlay.add_overlay(self._op_card)
        stack_overlay.set_measure_overlay(self._op_card, False)

        self._content_host_overlay = Gtk.Overlay()
        self._content_host_overlay.set_child(stack_overlay)
        self._content_host_overlay.set_vexpand(True)
        self._global_search_panel = None
        self._log_overlay_panel = None
        self._search_items_cache: list | None = None
        self._search_items_building = False
        self._search_items_built_at: float = 0.0

        root.append(self._content_host_overlay)

        self._split_view = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._split_view.set_start_child(self._build_sidebar())
        self._split_view.set_end_child(root)
        self._split_view.set_vexpand(True)
        self._split_view.set_shrink_start_child(False)
        self._split_view.set_resize_start_child(False)
        self._split_view.set_shrink_end_child(False)

        outer_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer_vbox.append(self._split_view)

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

        menu = Gio.Menu()

        section_diag = Gio.Menu()
        section_diag.append("Поиск по вкладкам", "win.global_search")
        section_diag.append("Посмотреть логи", "win.open_log")
        section_diag.append("Очистить лог", "win.clear_log")
        section_diag.append("Очистить кэш", "win.reset_state")
        menu.append_section(None, section_diag)

        section_reset = Gio.Menu()
        section_reset.append("Сброс настроек приложения", "win.reset_config")
        menu.append_section(None, section_reset)

        section_about = Gio.Menu()
        section_about.append("Справка", "win.help")
        section_about.append("Комбинации клавиш", "win.show-help-overlay")
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
            statuspage.compact scrolledwindow scrollbar {
                opacity: 0;
                min-width: 0;
                min-height: 0;
            }
            .ab-float-banner {
                background-color: alpha(@card_bg_color, 0.9);
                border-radius: 20px;
                padding: 5px 14px 5px 16px;
                border: 1px solid alpha(@borders, 0.4);
            }
            .ab-float-banner label {
                font-size: 0.82em;
            }
            .ab-main-content {
                box-shadow: inset 6px 0 10px -6px alpha(black, 0.3);
            }
            headerbar {
                box-shadow: 0 1px 6px alpha(black, 0.18);
            }
            /* Same column width as op card stretched the search button - fixed square */
            button.ab-global-search-btn {
                min-width: 42px;
                min-height: 42px;
                padding: 0;
            }
            button.ab-global-search-btn image {
                -gtk-icon-size: 22px;
            }
            .ab-log-terminal-panel {
                padding: 0;
            }
            expander.ab-log-expander-compact {
                margin: 0;
                padding: 0;
            }
            expander.ab-log-expander-compact > box > label {
                padding-top: 2px;
                padding-bottom: 2px;
            }
            .ab-icon-green { color: @success_color; }
            .ab-icon-red   { color: @error_color;   }
            .ab-op-floating-card {
                background-image: none;
                background-color: @theme_bg_color;
                border: 1px solid @borders;
                border-radius: 12px;
                box-shadow: 0 4px 16px alpha(black, 0.28);
                opacity: 1;
                padding: 12px 20px 14px 20px;
            }
            .ab-log-overlay-backdrop {
                background-color: alpha(black, 0.62);
            }
            .ab-log-overlay-card {
                background-color: @card_bg_color;
                border-radius: 16px;
                border: 1px solid alpha(@borders, 0.85);
                box-shadow: 0 8px 28px alpha(black, 0.22);
            }
            .ab-log-overlay-header {
                padding: 10px 10px 8px 16px;
                border-bottom: 1px solid alpha(@borders, 0.4);
            }
            .ab-log-overlay-card scrolledwindow {
                border-radius: 0 0 15px 15px;
            }
            .ab-log-overlay-card textview {
                background-color: @view_bg_color;
                border-radius: 0 0 15px 15px;
            }
            .ab-log-overlay-card textview > text {
                background-color: @view_bg_color;
                border-radius: 0 0 15px 15px;
            }
            /* TimeSync tabs: align icon + label in header */
            viewswitcher.ab-borg-viewswitcher {
                margin-top: 2px;
                margin-bottom: 2px;
            }
            viewswitcher.ab-borg-viewswitcher button.toggle > stack > box.wide {
                padding-top: 5px;
                padding-bottom: 5px;
                border-spacing: 8px;
            }
            viewswitcher.ab-borg-viewswitcher button.toggle > stack > box.wide > label {
                padding-top: 1px;
                padding-bottom: 1px;
            }
            viewswitcher.ab-borg-viewswitcher button.toggle > stack > box.wide > image {
                -gtk-icon-size: 18px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _dot_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        actions = [
            ("check_update",      self._check_for_updates),
            ("help",              self._show_help),
            ("about",             self._show_about),
            ("clear_log",         self._clear_log),
            ("reset_state",       self._reset_state),
            ("reset_config",      self._reset_config),
            ("open_log",          self._open_log_file),
            ("global_search",     self._present_global_search),
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
        app.set_accels_for_action("win.help", ["F1"])
        app.set_accels_for_action("win.about", ["<Ctrl>F1"])
        app.set_accels_for_action("win.show-help-overlay", ["<Ctrl>question"])
        app.set_accels_for_action("win.global_search", ["<Ctrl>k"])

        self.set_help_overlay(self._build_shortcuts_window())

        return header

    @staticmethod
    def _make_nav_row(name: str, title: str, icon_name: str):
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
        return row, img, lbl

    def _build_sidebar(self) -> Gtk.Widget:
        nav_list = Gtk.ListBox()
        nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        nav_list.add_css_class("navigation-sidebar")
        self._nav_list = nav_list
        self._nav_images: list[Gtk.Image] = []
        self._nav_labels: list[Gtk.Label] = []
        self._nav_rows: list[Gtk.ListBoxRow] = []
        self._nav_names: list[str] = []

        for name, title, icon_name, _ in self._MAIN_TABS:
            row, img, lbl = self._make_nav_row(name, title, icon_name)
            nav_list.append(row)
            self._nav_images.append(img)
            self._nav_labels.append(lbl)
            self._nav_rows.append(row)
            self._nav_names.append(name)

        nav_list.select_row(nav_list.get_row_at_index(0))
        nav_list.connect("row-selected", self._on_nav_row_selected)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(nav_list)
        scroll.set_vexpand(True)

        self._bottom_list_widget = self._build_sidebar_bottom()

        borg_list = Gtk.ListBox()
        borg_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        borg_list.add_css_class("navigation-sidebar")
        _bn, _bt, _bi, _ = self._BORG_TAB
        borg_row, borg_icon, borg_lbl = self._make_nav_row(_bn, _bt, _bi)
        borg_list.append(borg_row)
        self._nav_images.append(borg_icon)
        self._nav_labels.append(borg_lbl)
        self._borg_row = borg_row
        self._borg_list = borg_list

        def _on_borg_selected(_, row):
            if row is not None:
                self._stack.set_visible_child_name("borg")
                self._nav_list.unselect_all()

        def _on_nav_deselects_borg(*_):
            self._borg_list.unselect_all()

        borg_list.connect("row-selected", _on_borg_selected)
        nav_list.connect("row-selected", _on_nav_deselects_borg)

        guide_list = Gtk.ListBox()
        guide_list.set_selection_mode(Gtk.SelectionMode.NONE)
        guide_list.add_css_class("navigation-sidebar")
        g_row, g_img, g_lbl = self._make_nav_row(
            "alt_zero_guide",
            "ALT Zero",
            "alt-zero-book-symbolic",
        )
        g_row.set_activatable(True)
        guide_list.append(g_row)
        self._nav_images.append(g_img)
        self._nav_labels.append(g_lbl)
        guide_list.connect("row-activated", self._on_alt_zero_guide_sidebar_activated)

        self._sidebar_widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._sidebar_widget.append(scroll)
        self._sidebar_widget.append(borg_list)
        self._sidebar_widget.append(guide_list)
        self._sidebar_widget.append(self._bottom_list_widget)
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

    def _on_alt_zero_guide_sidebar_activated(self, _list, row):
        if row is None:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(_ALT_ZERO_GUIDE_URL, None)
        except GLib.Error:
            pass

    def _on_window_is_active(self, _win, _pspec):
        if not self.get_property("is-active"):
            return
        now = time.monotonic()
        if now - self._last_app_detection_cache_flush >= 5.0:
            self._last_app_detection_cache_flush = now
            invalidate_app_detection_caches()
        if self._stack.get_visible_child_name() != "flatpak":
            return
        page = self._pages.get("flatpak")
        if page is not None and hasattr(page, "on_window_is_active"):
            page.on_window_is_active()

    def _on_stack_child_changed(self, stack, _pspec):
        name = stack.get_visible_child_name()
        if name == "borg":
            self._nav_list.unselect_all()
            self._borg_list.select_row(self._borg_row)
        else:
            self._borg_list.unselect_all()
            for row in self._nav_rows:
                if row.get_name() == name:
                    self._nav_list.select_row(row)
                    break
        page = self._pages.get(name) if name else None
        if page is not None and hasattr(page, "on_tab_visible"):
            page.on_tab_visible()

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
        else:
            if not from_drag:
                self._sidebar_saved_width = self._split_view.get_position()
            self._sidebar_widget.set_size_request(self._SIDEBAR_ICONS_ONLY_WIDTH, -1)
            self._bottom_list_widget.set_size_request(self._SIDEBAR_ICONS_ONLY_WIDTH, -1)
            GLib.idle_add(self._split_view.set_position, self._SIDEBAR_ICONS_ONLY_WIDTH)

    def _on_sidebar_position_changed(self, paned, *_):
        pos = paned.get_position()
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
        self._log_container.set_vexpand(False)
        self._log_container.add_css_class("ab-log-terminal-panel")

        self._log_expander = Gtk.Expander(label="Лог терминала")
        self._log_expander.add_css_class("ab-log-expander-compact")
        self._log_expander.set_margin_start(8)
        self._log_expander.set_margin_top(2)
        self._log_expander.set_margin_end(8)
        self._log_expander.set_margin_bottom(2)

        self._log_scroll = Gtk.ScrolledWindow()
        self._log_scroll.set_vexpand(False)
        self._log_scroll.set_min_content_height(0)
        self._log_scroll.set_max_content_height(200)
        self._log_scroll.set_propagate_natural_height(False)

        self._tv = Gtk.TextView()
        self._tv.set_editable(False)
        self._tv.set_monospace(True)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.set_vexpand(False)
        self._tv.set_left_margin(10)
        self._tv.set_right_margin(10)
        self._tv.set_top_margin(10)
        self._tv.set_bottom_margin(10)
        self._buf = self._tv.get_buffer()
        self._log_scroll.set_child(self._tv)
        self._log_expander.set_child(self._log_scroll)
        self._log_expander.set_expanded(False)
        self._log_expander.connect("notify::expanded", self._on_log_expander_expanded)
        self._log_container.append(self._log_expander)

        return self._log_container

    def _on_log_expander_expanded(self, *_):
        if self._log_expander.get_expanded():
            self._log_scroll.set_min_content_height(160)
        else:
            self._log_scroll.set_min_content_height(0)

    def _build_log_overlay(self):
        panel = Gtk.Overlay()
        panel.set_hexpand(True)
        panel.set_vexpand(True)
        panel.set_halign(Gtk.Align.FILL)
        panel.set_valign(Gtk.Align.FILL)
        panel.set_visible(False)

        backdrop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        backdrop.add_css_class("ab-log-overlay-backdrop")
        backdrop.set_hexpand(True)
        backdrop.set_vexpand(True)
        bd_click = Gtk.GestureClick()
        bd_click.connect("pressed", lambda *_: self._close_log_overlay())
        backdrop.add_controller(bd_click)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("ab-log-overlay-card")
        card.set_hexpand(True)
        card.set_vexpand(True)
        card.set_halign(Gtk.Align.FILL)
        card.set_valign(Gtk.Align.FILL)
        card.set_margin_start(12)
        card.set_margin_end(12)
        card.set_margin_top(52)
        card.set_margin_bottom(52)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("ab-log-overlay-header")
        title = Gtk.Label(label="Лог терминала")
        title.add_css_class("heading")
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)
        self._log_overlay_close_btn = Gtk.Button()
        self._log_overlay_close_btn.set_icon_name("window-close-symbolic")
        self._log_overlay_close_btn.add_css_class("flat")
        self._log_overlay_close_btn.add_css_class("circular")
        self._log_overlay_close_btn.connect("clicked", lambda *_: self._close_log_overlay())
        header.append(title)
        header.append(self._log_overlay_close_btn)

        overlay_tv = Gtk.TextView()
        overlay_tv.set_buffer(self._buf)
        overlay_tv.set_editable(False)
        overlay_tv.set_monospace(True)
        overlay_tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        overlay_tv.set_left_margin(12)
        overlay_tv.set_right_margin(12)
        overlay_tv.set_top_margin(8)
        overlay_tv.set_bottom_margin(12)

        self._log_overlay_scroll = Gtk.ScrolledWindow()
        self._log_overlay_scroll.set_vexpand(True)
        self._log_overlay_scroll.set_child(overlay_tv)

        card.append(header)
        card.append(self._log_overlay_scroll)

        panel.set_child(backdrop)
        panel.add_overlay(card)
        panel.set_measure_overlay(card, False)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_log_overlay_key)
        panel.add_controller(key)

        return panel

    def _open_log_overlay(self):
        if self._log_overlay_panel is None:
            self._log_overlay_panel = self._build_log_overlay()
            self._content_host_overlay.add_overlay(self._log_overlay_panel)
            self._content_host_overlay.set_measure_overlay(self._log_overlay_panel, False)
        self._log_overlay_panel.set_visible(True)
        self._log_overlay_close_btn.grab_focus()
        GLib.idle_add(self._scroll_log_overlay_to_bottom)

    def _scroll_log_overlay_to_bottom(self):
        adj = self._log_overlay_scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def _close_log_overlay(self):
        if self._log_overlay_panel:
            self._log_overlay_panel.set_visible(False)

    def _on_log_overlay_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self._close_log_overlay()
            return True
        return False

    def _hide_op_card_if_idle(self) -> None:
        if self._progress_nesting == 0:
            self._op_card.set_visible(False)
            self._op_card.set_can_target(False)

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

        def _show_auth_overlay():
            self._op_card_title.set_label("Ожидание авторизации...")
            self._op_card_spinner.set_visible(True)
            self._op_card_spinner.set_spinning(True)
            self._op_card_stop_btn.set_visible(False)
            self._op_card_detail_box.set_visible(False)
            self._op_card.set_visible(True)
            self._op_card.set_can_target(False)

        GLib.idle_add(_show_auth_overlay)

        def _check():
            GLib.idle_add(self._log, "ℹ Инициализация pkexec...\n")
            ok, is_cancel = backend.start_pkexec_shell()
            if ok:
                GLib.idle_add(self._auth_ok)
            else:
                GLib.idle_add(self.get_application().quit)

        threading.Thread(target=_check, daemon=True).start()

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
        self.present()
        self._maint.set_sensitive_all(True)
        self._maint.refresh_checks()
        self._log("👋 Добро пожаловать в ALT Booster. С чего начнём?\n")
        self._hide_op_card_if_idle()
        if config.INITIAL_TAB and config.INITIAL_TAB in self._pages:
            GLib.idle_add(self._stack.set_visible_child_name, config.INITIAL_TAB)
        GLib.timeout_add(2000, self._warmup_search_cache)

    def _warmup_search_cache(self) -> bool:
        from ui.global_search import build_all_search_items
        if not self._search_items_building and self._search_items_cache is None:
            self._search_items_building = True
            main_tabs = self._MAIN_TABS
            borg_tab = self._BORG_TAB

            def _build():
                try:
                    result = build_all_search_items(main_tabs, borg_tab)
                    GLib.idle_add(self._on_search_items_ready, result)
                except Exception as e:
                    GLib.idle_add(self._on_search_items_build_failed, e)

            threading.Thread(target=_build, daemon=True).start()
        return False


    def _load_settings(self):
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _):
        if self._progress_nesting > 0:
            dialog = Adw.AlertDialog(
                heading="Операция выполняется",
                body=f"«{self._progress_message}» ещё не завершена. Закрытие сейчас может привести к незавершённым изменениям.",
            )
            dialog.add_response("cancel", "Отмена")
            dialog.add_response("close", "Всё равно закрыть")
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def _on_response(_d, response):
                if response == "close":
                    config.flush_pending_state()
                    self._log_queue.put(None)
                    self.destroy()

            dialog.connect("response", _on_response)
            dialog.present(self)
            return True

        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "width": self.get_width(),
                    "height": self.get_height(),
                    "sidebar_width": self._split_view.get_position(),
                }, f)
        except OSError:
            pass
        config.flush_pending_state()
        self._log_queue.put(None)
        return False


    def _check_for_updates(self, *_):
        if self._stack.get_visible_child_name() == "setup":
            if self._setup.dismiss_update_section():
                return
        self._stack.set_visible_child_name("setup")
        self._update_badge_dot.set_visible(False)
        self._setup.check_for_updates(manual=True, on_update_found=self._on_update_found_global)

    def _present_global_search(self, *_):
        from ui.global_search import GlobalSearchPanel, build_all_search_items

        if self._global_search_panel is None:
            self._global_search_panel = GlobalSearchPanel()
            self._content_host_overlay.add_overlay(self._global_search_panel)
            self._content_host_overlay.set_measure_overlay(self._global_search_panel, False)

        items = self._search_items_cache or []
        self._global_search_panel.open(items, self._on_global_search_pick)

        stale = (time.time() - self._search_items_built_at) > 60.0
        if not self._search_items_building and stale:
            self._search_items_building = True
            main_tabs = self._MAIN_TABS
            borg_tab = self._BORG_TAB

            def _build():
                try:
                    result = build_all_search_items(main_tabs, borg_tab)
                    GLib.idle_add(self._on_search_items_ready, result)
                except Exception as e:
                    GLib.idle_add(self._on_search_items_build_failed, e)

            threading.Thread(target=_build, daemon=True).start()

    def _on_search_items_ready(self, items: list) -> None:
        self._search_items_cache = items
        self._search_items_building = False
        self._search_items_built_at = time.time()
        if self._global_search_panel and self._global_search_panel.get_visible():
            self._global_search_panel.update_items(items)

    def _on_search_items_build_failed(self, err: Exception) -> None:
        self._search_items_building = False
        self._log(f"⚠  Ошибка сборки индекса поиска: {err}\n")

    def _on_global_search_pick(self, tab_id: str, focus_spec: str | None = None):
        self._stack.set_visible_child_name(tab_id)
        if focus_spec:
            GLib.idle_add(lambda: self._apply_search_focus(tab_id, focus_spec))

    def _apply_search_focus(self, tab_id: str, focus_spec: str) -> None:
        if focus_spec.startswith("setup:"):
            self._setup.focus_search_target(focus_spec[6:])
        elif focus_spec.startswith("m:"):
            self._maint.focus_search_target(focus_spec[2:])
        elif focus_spec.startswith("d:"):
            page = self._pages.get(tab_id)
            if page is not None and hasattr(page, "focus_row_by_id"):
                page.focus_row_by_id(focus_spec[2:])
        elif focus_spec.startswith("app:"):
            page = self._pages.get("apps")
            if page is not None and hasattr(page, "focus_app_by_id"):
                page.focus_app_by_id(focus_spec[4:])
        elif focus_spec.startswith("ext:"):
            page = self._pages.get("extensions")
            if page is not None and hasattr(page, "focus_extension_by_uuid"):
                page.focus_extension_by_uuid(focus_spec[4:])
        elif focus_spec.startswith("fp:"):
            page = self._pages.get("flatpak")
            if page is not None and hasattr(page, "focus_search_target"):
                page.focus_search_target(focus_spec[3:])

    def _show_help(self, *_):
        try:
            from ui.help_altbooster import show_help
            show_help(self)
        except Exception as e:
            self._log(f"✗ Справка: {e}\n")

    def _build_shortcuts_window(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="help_overlay">
    <property name="modal">1</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="title">ALT Booster</property>
        <property name="section-name">general</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Приложение</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">О приложении</property>
                <property name="accelerator">F1</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Комбинации клавиш</property>
                <property name="accelerator">&lt;ctrl&gt;question</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Поиск по вкладкам</property>
                <property name="accelerator">&lt;ctrl&gt;k</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>"""
        builder = Gtk.Builder.new_from_string(xml, -1)
        return builder.get_object("help_overlay")

    def _show_about(self, *_):
        d = Adw.AboutDialog()
        d.set_application_name("ALT Booster")
        d.set_application_icon("altbooster")
        d.set_developer_name("plafonlinux")
        d.set_version(config.VERSION)
        d.set_issue_url("https://github.com/plafonlinux/altbooster/issues")
        d.set_support_url("https://plafon.gitbook.io/alt-zero")
        d.set_comments("ALT Booster — утилита-компаньон для настройки ALT Рабочая станция (GNOME)")
        d.set_license_type(Gtk.License.MIT_X11)
        d.set_developers(
            [
                "plafonlinux (@plafonlinux)",
                "Aleksandr Shamaraev (@AlexanderShad)",
                "Anton Palgunov (@Toxblh)",
                "Yauhen Charniauski (@culler127)",
                "Vadim Totok (@VadimTotok)",
            ]
        )
        d.set_copyright("© 2026 PLAFON")
        d.add_link("📖 ALT Zero", "https://plafon.gitbook.io/alt-zero")
        d.add_link("💻 GitHub", "https://github.com/plafonlinux/altbooster")
        d.add_link("👤 plafonlinux", "https://github.com/plafonlinux")
        d.add_link("👥 AlexanderShad", "https://github.com/AlexanderShad")
        d.add_link("👥 Toxblh", "https://github.com/Toxblh")
        d.add_link("👥 culler127", "https://github.com/culler127")
        d.add_link("👥 VadimTotok", "https://github.com/VadimTotok")
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
                # Не закрываем окно автоматически: пользователь может продолжить работу.
                if hasattr(self, "_maint") and self._maint is not None:
                    self._maint.refresh_checks()

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


    def _build_op_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("ab-op-floating-card")
        card.set_margin_bottom(120)
        card.set_margin_end(16)
        card.set_halign(Gtk.Align.END)
        card.set_valign(Gtk.Align.END)
        card.set_size_request(360, -1)
        card.set_visible(False)
        card.set_can_target(False)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._op_card_spinner = Gtk.Spinner()
        self._op_card_spinner.set_spinning(True)
        self._op_card_spinner.set_size_request(16, 16)
        top.append(self._op_card_spinner)

        self._op_card_title = Gtk.Label()
        self._op_card_title.add_css_class("heading")
        self._op_card_title.set_halign(Gtk.Align.START)
        self._op_card_title.set_hexpand(True)
        self._op_card_title.set_wrap(True)
        self._op_card_title.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._op_card_title.set_xalign(0.0)
        top.append(self._op_card_title)

        self._op_card_stop_btn = Gtk.Button()
        self._op_card_stop_btn.set_icon_name("media-playback-stop-symbolic")
        self._op_card_stop_btn.add_css_class("flat")
        self._op_card_stop_btn.add_css_class("circular")
        self._op_card_stop_btn.set_tooltip_text("Отменить")
        self._op_card_stop_btn.set_size_request(24, 24)
        self._op_card_stop_btn.connect("clicked", self._on_stop_clicked)
        top.append(self._op_card_stop_btn)
        card.append(top)

        click = Gtk.GestureClick()
        click.connect("released", self._on_op_card_clicked)
        card.add_controller(click)
        card.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        self._op_card_detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._op_card_detail_box.set_halign(Gtk.Align.FILL)
        self._op_card_detail_box.set_hexpand(True)

        def _detail_lbl() -> Gtk.Label:
            lbl = Gtk.Label()
            lbl.add_css_class("caption")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_xalign(0.0)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_single_line_mode(True)
            lbl.set_selectable(False)
            return lbl

        self._op_card_detail_l1 = _detail_lbl()
        self._op_card_detail_l2 = _detail_lbl()
        self._op_card_detail_l3 = _detail_lbl()
        self._op_card_detail_l3.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._op_card_detail_box.append(self._op_card_detail_l1)
        self._op_card_detail_box.append(self._op_card_detail_l2)
        self._op_card_detail_box.append(self._op_card_detail_l3)
        card.append(self._op_card_detail_box)

        return card

    def start_progress(self, message: str, on_cancel=None):
        _cb = on_cancel if on_cancel is not None else backend.cancel_current

        def _do():
            if on_cancel is not None:
                self._progress_nesting = 1
            else:
                self._progress_nesting += 1
            self._on_cancel_cb = _cb
            self._progress_message = message
            self._op_card_pct = None
            self._op_card_title.set_label(message)
            self._op_card_spinner.set_visible(True)
            self._op_card_spinner.set_spinning(True)
            self._op_card_stop_btn.set_visible(True)
            self._op_card_stop_btn.set_sensitive(True)
            self._op_card_detail_box.set_visible(True)
            self._set_op_detail_lines("Запуск...", "", "")
            self._op_card.set_visible(True)
            self._op_card.set_can_target(True)
            if self._reset_status_timer_id:
                GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = None

        GLib.idle_add(_do)

    def _on_op_card_clicked(self, gesture, n_press, x, y):
        self._open_log_overlay()

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
                self._op_card_title.set_label("Отмена...")
                self._op_card_stop_btn.set_sensitive(False)
                self._on_cancel_cb()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def stop_progress(self, success: bool = True):
        def _do():
            self._progress_nesting = max(0, self._progress_nesting - 1)
            label = self._last_log_line or ("✔ Готово" if success else "✘ Ошибка")
            if self._progress_nesting == 0:
                self._on_cancel_cb = None
                self._op_card_title.set_label(label)
                self._op_card_spinner.set_spinning(False)
                self._op_card_spinner.set_visible(False)
                self._op_card_stop_btn.set_visible(False)
                self._op_card_stop_btn.set_sensitive(True)
                self._op_card_detail_box.set_visible(False)
                self._op_card.set_visible(True)
                self._op_card.set_can_target(True)
                if self._reset_status_timer_id:
                    GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = GLib.timeout_add(4000, self._reset_status_label)

        GLib.idle_add(_do)

    def _reset_status_label(self):
        self._reset_status_timer_id = None
        self._hide_op_card_if_idle()
        return False

    def _log(self, text):
        GLib.idle_add(self._log_internal, text)

    def _log_internal(self, text):
        stripped = text.strip()
        if stripped:
            self._last_log_line = stripped
            if self._op_card.get_visible() and self._progress_nesting > 0:
                self._parse_progress_line(stripped)

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

    def _set_op_detail_lines(self, l1: str = "", l2: str = "", l3: str = "") -> None:
        rows = (l1 or "").strip(), (l2 or "").strip(), (l3 or "").strip()
        for lbl, t in zip(
            (self._op_card_detail_l1, self._op_card_detail_l2, self._op_card_detail_l3),
            rows,
        ):
            lbl.set_visible(bool(t))
            lbl.set_label(t if t else "")

    @staticmethod
    def _normalize_borg_progress_path(path: str) -> str:
        p = (path or "").strip()
        if not p or p == "-":
            return ""
        if p.startswith("/"):
            return p
        if p.startswith(("home/", "var/", "usr/", "mnt/", "media/", "run/")):
            return "/" + p
        return p

    def _parse_progress_line(self, line: str):
        # borg create --progress: "2.88 GB O 1.70 GB C 1.60 GB D 14576 N path"
        bm = _BORG_CREATE_PROGRESS_RE.match(line.strip())
        if bm:
            o_s, c_s, d_s, n_raw, path_raw = bm.groups()
            try:
                n_fmt = f"{int(n_raw):,}".replace(",", " ")
            except ValueError:
                n_fmt = n_raw
            path = self._normalize_borg_progress_path(path_raw)
            self._set_op_detail_lines(
                f"Исходно {o_s}  ·  Сжато {c_s}  ·  В репозитории {d_s}",
                f"Файлов обработано: {n_fmt}",
                f"Сейчас: {path}" if path else "",
            )
            return
        # rsync --info=progress2:  "  1,234,567  67%  45.20MB/s    0:01:23 ..."
        m = re.search(r'(\d+)%\s+([\d.]+\s*[KMGTkm]?B/s)\s+(\d+:\d+:\d+)', line)
        if m:
            pct = int(m.group(1)) / 100.0
            speed = m.group(2)
            eta = m.group(3)
            self._op_card_pct = pct
            self._set_op_detail_lines(f"{int(pct * 100)}%  ·  {speed}  ·  осталось {eta}", "", "")
            return
        # borg create --progress: "2.34 GB O 1.23 GB C 456.78 MB D 78.9% N ..." (старые/другие сборки)
        m2 = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
        if m2:
            pct = float(m2.group(1)) / 100.0
            self._op_card_pct = pct
            eta_hint = ""
            meta = re.search(
                r"\b(?:ETA|осталось|remaining)\s*[:.]?\s*([^\n\r]{1,48})",
                line,
                re.IGNORECASE,
            )
            if meta:
                eta_hint = f"  ·  осталось ~{meta.group(1).strip()}"
            self._set_op_detail_lines(f"{int(pct * 100)}%{eta_hint}", "", "")
            return
        # btrfs send / generic: короткая строка — одна строка деталей
        if line and len(line) < 80:
            self._set_op_detail_lines(line, "", "")

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

