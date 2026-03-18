
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

from core import config
from core import backend
from tabs.setup import SetupPage
from tabs.apps import AppsPage
from tabs.extensions import ExtensionsPage
from tabs.terminal import TerminalPage
from tabs.davinci import DaVinciPage
from tabs.amd import AmdPage
from tabs.intel import IntelPage
from tabs.maintenance import MaintenancePage
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
        ("intel",       "Intel",           "processor-symbolic",           IntelPage),
        ("davinci",     "DaVinci Resolve", "davinci-symbolic",             DaVinciPage),
        ("maintenance", "Обслуживание",    "emblem-system-symbolic",       MaintenancePage),
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

        _src_base = _icons_base / "hicolor" / "scalable"
        _dst_hicolor = Path.home() / ".local" / "share" / "icons" / "hicolor"
        _dst_base = _dst_hicolor / "scalable"
        _icons_copied = False
        for _cat in ("apps", "devices"):
            _src_cat = _src_base / _cat
            _dst_cat = _dst_base / _cat
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

        self._pulse_timer_id = None
        self._reset_status_timer_id = None
        self._elapsed_timer_id = None
        self._progress_start_time = 0.0
        self._progress_message = ""
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

        self._pages = {}
        for name, title, icon, PageClass in [*self._MAIN_TABS, self._BORG_TAB]:
            page = PageClass(self._log)
            self._pages[name] = page
            p = self._stack.add_titled(page, name, title)
            p.set_icon_name(icon)

        self._setup = self._pages["setup"]
        self._maint = self._pages["maintenance"]
        self._borg  = self._pages["borg"]

        self._stack.set_vexpand(True)
        self._stack.connect("notify::visible-child", self._on_stack_child_changed)
        stack_overlay = Gtk.Overlay()
        stack_overlay.set_child(self._stack)
        stack_overlay.set_vexpand(True)

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
        section_about.append("Справка", "win.help")
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

        self._sidebar_widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._sidebar_widget.append(scroll)
        self._sidebar_widget.append(borg_list)
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
                    return

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
        self._status_label.set_label("Готов к работе")


    def _load_settings(self):
        try:
            with open(config.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _on_close(self, _):
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
        self._log_queue.put(None)
        return False


    def _check_for_updates(self, *_):
        if self._stack.get_visible_child_name() == "setup":
            if self._setup.dismiss_update_section():
                return
        self._stack.set_visible_child_name("setup")
        self._update_badge_dot.set_visible(False)
        self._setup.check_for_updates(manual=True, on_update_found=self._on_update_found_global)

    def _show_help(self, *_):
        from ui.help_dialog import HelpDialog
        HelpDialog(self)

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
        _cb = on_cancel if on_cancel is not None else backend.cancel_current

        def _do():
            if on_cancel is not None:
                self._progress_nesting = 1
            else:
                self._progress_nesting += 1
            self._on_cancel_cb = _cb
            self._progress_message = message
            self._progress_start_time = time.monotonic()
            self._status_label.set_label(message)
            self._progressbar.set_fraction(0.0)
            self._stop_btn.set_sensitive(True)
            self._stop_btn.set_visible(True)
            if self._pulse_timer_id:
                GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = GLib.timeout_add(100, self._pulse_progress)
            if self._reset_status_timer_id:
                GLib.source_remove(self._reset_status_timer_id)
                self._reset_status_timer_id = None
            if self._elapsed_timer_id:
                GLib.source_remove(self._elapsed_timer_id)
            self._elapsed_timer_id = GLib.timeout_add(1000, self._update_elapsed_label)

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

    def _update_elapsed_label(self):
        elapsed = int(time.monotonic() - self._progress_start_time)
        if elapsed < 60:
            suffix = f"{elapsed} с"
        else:
            m, s = divmod(elapsed, 60)
            suffix = f"{m}:{s:02d}"
        self._status_label.set_label(f"{self._progress_message} — {suffix}")
        return True

    def stop_progress(self, success: bool = True):
        def _do():
            if self._elapsed_timer_id:
                GLib.source_remove(self._elapsed_timer_id)
                self._elapsed_timer_id = None
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

