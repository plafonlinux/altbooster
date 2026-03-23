from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from core import backend
from ui.widgets import make_icon


def _fmt_size(size: int) -> str:
    if not isinstance(size, (int, float)) or size < 0:
        return ""
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} ПБ"


def _fmt_archive_date(name: str) -> str:
    parts = name.split("-", 1)
    if len(parts) == 2:
        ts = parts[1].replace("T", " ").replace("-", ".").replace(".", " ", 2)
        return ts
    return name


class BorgBackupSummaryDialog(Adw.Window):

    def __init__(self, parent, repo_path: str, opts: dict, on_confirm):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Сводка резервной копии")
        self.set_default_size(1100, 600)

        self._repo_path = repo_path
        self._opts = opts
        self._on_confirm = on_confirm

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        cancel_btn = Gtk.Button(label="Отмена")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        self._confirm_btn = Gtk.Button(label="Создать резервную копию")
        self._confirm_btn.add_css_class("suggested-action")
        self._confirm_btn.set_sensitive(False)
        self._confirm_btn.connect("clicked", self._on_confirm_clicked)
        header.pack_end(self._confirm_btn)

        self._spinner = Gtk.Spinner()
        self._spinner.start()
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_halign(Gtk.Align.CENTER)

        self._loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._loading_box.set_vexpand(True)
        self._loading_box.set_valign(Gtk.Align.CENTER)
        self._loading_box.set_halign(Gtk.Align.CENTER)
        self._loading_box.append(self._spinner)

        loading_label = Gtk.Label(label="Собираем данные. Ожидайте!")
        loading_label.add_css_class("dim-label")
        self._loading_box.append(loading_label)

        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self._content_box.set_margin_start(20)
        self._content_box.set_margin_end(20)
        self._content_box.set_margin_top(16)
        self._content_box.set_margin_bottom(20)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._content_box)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._loading_box, "loading")
        self._stack.add_named(scroll, "content")
        self._stack.set_visible_child_name("loading")

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root_box.append(header)
        root_box.append(self._stack)
        self.set_content(root_box)

        threading.Thread(target=self._load_data, daemon=True).start()

    @staticmethod
    def _du(path: str) -> str:
        return BorgBackupSummaryDialog._du_pair(path)[0]

    @staticmethod
    def _du_pair(path: str) -> tuple[str, int]:
        try:
            r = subprocess.run(
                ["du", "--apparent-size", "-sk", path],
                capture_output=True, text=True, encoding="utf-8", timeout=180,
            )
            line = (r.stdout or "").splitlines()
            if line:
                kb = int(line[0].split("\t")[0].strip())
                if kb >= 1024 * 1024:
                    return f"{kb / 1024 / 1024:.1f} ГБ", kb
                if kb >= 1024:
                    return f"{kb / 1024:.0f} МБ", kb
                return f"{kb} КБ", kb
        except Exception:
            pass
        return "", 0

    @staticmethod
    def _fmt_kb(kb: int) -> str:
        if kb >= 1024 * 1024:
            return f"{kb / 1024 / 1024:.1f} ГБ"
        if kb >= 1024:
            return f"{kb / 1024:.0f} МБ"
        return f"{kb} КБ"

    @staticmethod
    def _du_children(parent: str) -> dict[str, str]:
        try:
            r = subprocess.run(
                ["du", "--apparent-size", "-sh", "--max-depth=1", parent],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            result = {}
            for line in r.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    size = parts[0].strip()
                    name = Path(parts[1].strip()).name
                    if name and name != Path(parent).name:
                        result[name] = size
            return result
        except Exception:
            return {}

    @staticmethod
    def _fmt_bytes(kb_str: str) -> str:
        try:
            kb = int(kb_str)
            if kb >= 1024 * 1024:
                return f"{kb / 1024 / 1024:.1f} ГБ"
            if kb >= 1024:
                return f"{kb / 1024:.0f} МБ"
            return f"{kb} КБ"
        except Exception:
            return ""

    def _load_data(self):
        data = {}
        home = Path.home()
        total_kb = 0
        var_app_str = str(home / ".var" / "app")
        estimate_paths: list[str] = []
        du_kb_cache: dict[str, int] = {}
        excludes = [os.path.expanduser(p) for p in self._opts.get("excludes", [])]

        def _norm_path(p: str) -> str:
            try:
                return str(Path(p).resolve())
            except Exception:
                return os.path.abspath(p)

        normalized_excludes = [_norm_path(p) for p in excludes]

        def _du_kb_cached(p: str) -> int:
            np = _norm_path(p)
            if np not in du_kb_cache:
                _, kb = self._du_pair(np)
                du_kb_cache[np] = kb
            return du_kb_cache[np]

        def _effective_kb_for_path(path: str, base_kb: int) -> int:
            np = _norm_path(path)
            if not normalized_excludes:
                return base_kb
            prefix = np.rstrip("/") + "/"
            excluded_kb = 0
            for ex in normalized_excludes:
                if ex == np or ex.startswith(prefix):
                    excluded_kb += _du_kb_cached(ex)
            return max(0, base_kb - excluded_kb)

        raw_paths = list(self._opts.get("paths", []))
        estimate_paths.extend(raw_paths)
        raw_paths_normalized = [_norm_path(p) for p in raw_paths]
        var_app_norm = _norm_path(var_app_str)
        var_app_is_already_in_paths = any(
            var_app_norm == rp or var_app_norm.startswith(rp.rstrip("/") + "/")
            for rp in raw_paths_normalized
        )
        paths_with_size = []
        paths_kb = 0
        for p in raw_paths:
            s, kb = self._du_pair(p)
            effective_kb = _effective_kb_for_path(p, kb)
            display_size = self._fmt_kb(effective_kb)
            paths_with_size.append((p, display_size))
            if not p.startswith(var_app_str):
                paths_kb += effective_kb
                total_kb += effective_kb
        data["paths"] = paths_with_size
        data["paths_kb"] = paths_kb

        if self._opts.get("flatpak_apps"):
            source_mode = self._opts.get("flatpak_apps_source", 0)
            data["flatpak_apps_source"] = source_mode
            try:
                r = subprocess.run(
                    ["flatpak", "list", "--app", "--columns=name,application,size"],
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                if r.returncode != 0:
                    r = subprocess.run(
                        ["flatpak", "list", "--app", "--columns=name,application"],
                        capture_output=True, text=True, encoding="utf-8", timeout=15,
                    )
                installed = {}
                for line in r.stdout.splitlines():
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split("\t")]
                    name = parts[0] if len(parts) > 0 else ""
                    app_id = parts[1] if len(parts) > 1 else ""
                    size = parts[2] if len(parts) > 2 else ""
                    if app_id:
                        installed[app_id] = (name or app_id, size)
            except Exception:
                installed = {}

            if source_mode == 1:
                try:
                    raw = backend.flatpak_apps_from_booster_list()
                    apps = []
                    for name, app_id in raw:
                        if app_id in installed:
                            inst_name, size = installed[app_id]
                            apps.append((inst_name or name, app_id, size))
                    data["flatpak_apps"] = apps
                except Exception:
                    data["flatpak_apps"] = []
            else:
                data["flatpak_apps"] = [
                    (name, app_id, size) for app_id, (name, size) in installed.items()
                ]

        if self._opts.get("flatpak_remotes"):
            try:
                r = subprocess.run(
                    ["flatpak", "remotes", "--columns=name,url"],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                remotes = []
                for line in r.stdout.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        remotes.append((parts[0].strip(), parts[1].strip(), ""))
                    elif parts[0].strip():
                        remotes.append((parts[0].strip(), "", ""))
                data["flatpak_remotes"] = remotes
            except Exception:
                data["flatpak_remotes"] = []

        if self._opts.get("extensions"):
            try:
                r = subprocess.run(
                    ["gnome-extensions", "list", "--enabled"],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                data["extensions"] = [u.strip() for u in r.stdout.splitlines() if u.strip()]
            except Exception:
                data["extensions"] = []

        if self._opts.get("flatpak_data"):
            var_app = home / ".var" / "app"
            sizes = self._du_children(str(var_app))
            try:
                all_dirs = sorted(p.name for p in var_app.iterdir() if p.is_dir()) if var_app.exists() else []
            except Exception:
                all_dirs = []
            flt = self._opts.get("flatpak_data_filter")
            dirs = [d for d in all_dirs if d in flt] if flt is not None else all_dirs
            data["flatpak_data_dirs"] = [(d, sizes.get(d, "")) for d in dirs]
            if flt is None:
                flatpak_total_str, flatpak_kb = self._du_pair(str(var_app))
            else:
                flatpak_kb = 0
                for d in dirs:
                    _, kb = self._du_pair(str(var_app / d))
                    flatpak_kb += kb
                flatpak_total_str = self._fmt_kb(flatpak_kb) if flatpak_kb else ""
            data["flatpak_data_total"] = flatpak_total_str
            if not var_app_is_already_in_paths:
                total_kb += flatpak_kb

        if self._opts.get("home_dirs"):
            home_dirs = self._opts["home_dirs"]
            estimate_paths.extend(str(home / d) for d in home_dirs)
            home_pairs = [(d, *self._du_pair(str(home / d))) for d in home_dirs]
            data["home_dirs"] = [(d, s) for d, s, _ in home_pairs]
            home_kb = sum(kb for _, _, kb in home_pairs)
            data["home_dirs_kb"] = home_kb
            total_kb += home_kb

        if self._opts.get("custom_paths"):
            custom = self._opts["custom_paths"]
            estimate_paths.extend(custom)
            custom_pairs = [(p, *self._du_pair(p)) for p in custom]
            data["custom_paths"] = [(p, s) for p, s, _ in custom_pairs]
            total_kb += sum(kb for _, _, kb in custom_pairs)

        if self._opts.get("system_packages", True):
            try:
                r = subprocess.run(
                    ["rpm", "-qa", "--queryformat", "%{NAME}\n"],
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                data["packages_count"] = len([line for line in r.stdout.splitlines() if line.strip()])
            except Exception:
                data["packages_count"] = 0
        else:
            data["packages_count"] = 0

        data["borg_estimate"] = backend.borg_estimate_create(
            self._repo_path,
            estimate_paths,
            backend.DEFAULT_EXCLUDES,
            exclude_caches=True,
        )
        data["total_kb"] = total_kb
        GLib.idle_add(self._populate, data)

    def _make_card(self, title: str, subtitle: str = "", icon: str = "", size: str = "", chip: str = "") -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("card")
        card.set_hexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)

        if icon:
            ic = make_icon(icon, 16)
            ic.set_valign(Gtk.Align.CENTER)
            inner.append(ic)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        lbl = Gtk.Label(label=title)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(28)
        text_box.append(lbl)

        if subtitle:
            sub = Gtk.Label(label=subtitle)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            sub.set_halign(Gtk.Align.START)
            sub.set_ellipsize(Pango.EllipsizeMode.END)
            sub.set_max_width_chars(28)
            text_box.append(sub)

        inner.append(text_box)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        right.set_valign(Gtk.Align.CENTER)
        right.set_halign(Gtk.Align.END)

        if chip:
            chip_lbl = Gtk.Label(label=chip)
            chip_lbl.add_css_class("accent")
            chip_lbl.add_css_class("caption")
            chip_lbl.set_halign(Gtk.Align.END)
            right.append(chip_lbl)

        if size:
            size_lbl = Gtk.Label(label=size)
            size_lbl.add_css_class("dim-label")
            size_lbl.add_css_class("caption")
            size_lbl.set_halign(Gtk.Align.END)
            right.append(size_lbl)

        if chip or size:
            inner.append(right)

        card.append(inner)
        return card

    def _make_section(self, title: str, badge: str = "") -> tuple[Gtk.Expander, Gtk.FlowBox]:
        label_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        title_lbl.set_halign(Gtk.Align.START)
        label_box.append(title_lbl)

        if badge:
            badge_lbl = Gtk.Label(label=badge)
            badge_lbl.add_css_class("dim-label")
            badge_lbl.add_css_class("caption")
            badge_lbl.set_valign(Gtk.Align.CENTER)
            label_box.append(badge_lbl)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_homogeneous(True)
        flow.set_column_spacing(6)
        flow.set_row_spacing(6)

        flow_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        flow_wrap.set_margin_top(8)
        flow_wrap.append(flow)

        expander = Gtk.Expander()
        expander.set_label_widget(label_box)
        expander.set_expanded(True)
        expander.set_child(flow_wrap)

        return expander, flow

    def _add_cards(self, flow: Gtk.FlowBox, items: list, icon: str, limit: int = 200):
        for item in items[:limit]:
            if isinstance(item, tuple):
                title = item[0] or (item[1] if len(item) > 1 else "")
                subtitle = item[1] if len(item) > 1 and item[0] else ""
                size = item[2] if len(item) > 2 else ""
            else:
                title, subtitle, size = item, "", ""
            flow.append(self._make_card(title, subtitle, icon, size))
        if len(items) > limit:
            flow.append(self._make_card(f"… и ещё {len(items) - limit}", icon="view-more-symbolic"))

    def _populate(self, data: dict):
        note_row = Adw.ActionRow()
        note_row.set_title("Как читать размер")
        note_row.set_subtitle(
            "Оценка в сводке считается по логическому размеру (как Original size в Borg). "
            "Фактический объём в хранилище обычно меньше за счёт сжатия и дедупликации "
            "(Compressed/Deduplicated size)."
        )
        note_row.add_prefix(make_icon("dialog-information-symbolic"))
        self._content_box.append(note_row)

        total_kb = data.get("total_kb", 0)
        total_badge = f"Всего: {self._fmt_kb(total_kb)}" if total_kb else ""
        estimate = data.get("borg_estimate")
        if estimate:
            total_badge = (
                f"{total_badge}  ·  Borg dry-run O/C/D: "
                f"{estimate.get('original', '?')} / {estimate.get('compressed', '?')} / {estimate.get('deduplicated', '?')}"
            ).strip()
        repo_section, repo_flow = self._make_section("Хранилище", total_badge)
        repo_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        repo_card.add_css_class("card")
        repo_card.set_hexpand(True)
        repo_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        repo_inner.set_margin_start(12)
        repo_inner.set_margin_end(12)
        repo_inner.set_margin_top(10)
        repo_inner.set_margin_bottom(10)
        repo_inner.append(make_icon("drive-harddisk-symbolic", 16))
        repo_path_lbl = Gtk.Label(label=self._repo_path)
        repo_path_lbl.set_halign(Gtk.Align.START)
        repo_path_lbl.set_wrap(True)
        repo_path_lbl.set_wrap_mode(Pango.WrapMode.CHAR)
        repo_path_lbl.set_selectable(True)
        repo_inner.append(repo_path_lbl)
        repo_card.append(repo_inner)
        repo_flow.append(repo_card)
        self._content_box.append(repo_section)

        home_dirs = data.get("home_dirs")
        if home_dirs:
            home_kb = data.get("home_dirs_kb", 0)
            badge = f"{len(home_dirs)} папок"
            if home_kb:
                badge += f"  ·  {self._fmt_kb(home_kb)}"
            section, flow = self._make_section("Папки домашнего каталога", badge)
            self._add_cards(flow, [(f"~/{d[0]}", "", d[1]) for d in home_dirs], "folder-home-symbolic")
            self._content_box.append(section)

        var_app_str = str(Path.home() / ".var" / "app")
        paths = [(p, s) for p, s in data.get("paths", [])
                 if not p.startswith(var_app_str)]
        if paths:
            paths_kb = data.get("paths_kb", 0)
            badge = f"{len(paths)} источников"
            if paths_kb:
                badge += f"  ·  {self._fmt_kb(paths_kb)}"
            section, flow = self._make_section("Домашний каталог", badge)
            self._add_cards(flow, paths, "folder-symbolic")
            self._content_box.append(section)

        remotes = data.get("flatpak_remotes")
        if remotes is not None:
            section, flow = self._make_section("Репозитории Flatpak", f"{len(remotes)} источников")
            self._add_cards(flow, remotes, "network-server-symbolic")
            self._content_box.append(section)

        apps = data.get("flatpak_apps")
        flatpak_dirs = data.get("flatpak_data_dirs")
        if apps is not None:
            data_map = {d: s for d, s in flatpak_dirs} if flatpak_dirs is not None else {}
            with_data = sum(1 for _, app_id, _ in apps if app_id in data_map)
            total = data.get("flatpak_data_total", "")

            source_mode = data.get("flatpak_apps_source", 0)
            source_label = "ALT Booster" if source_mode == 1 else "установленные"
            badge = str(len(apps)) + f" приложений  ·  {source_label}"
            if flatpak_dirs is not None:
                badge += f"  ·  с данными: {with_data}"
                if total:
                    badge += f" ({total})"
            else:
                badge += "  ·  только список"

            section, flow = self._make_section("Приложения Flatpak", badge)
            for name, app_id, size in apps:
                actual_id = app_id or name
                if actual_id in data_map:
                    data_size = data_map[actual_id]
                    chip = f"+ данные{('  ' + data_size) if data_size else ''}"
                    flow.append(self._make_card(name, app_id, "application-x-executable-symbolic", size, chip))
                else:
                    flow.append(self._make_card(name, app_id, "application-x-executable-symbolic", size))
            if len(apps) > 200:
                flow.append(self._make_card(f"… и ещё {len(apps) - 200}", icon="view-more-symbolic"))
            self._content_box.append(section)
        elif flatpak_dirs is not None:
            total = data.get("flatpak_data_total", "")
            badge = str(len(flatpak_dirs)) + " приложений" + (f"  ·  {total}" if total else "")
            section, flow = self._make_section("Данные Flatpak (~/.var/app)", badge)
            self._add_cards(flow, flatpak_dirs, "folder-symbolic")
            self._content_box.append(section)

        exts = data.get("extensions")
        if exts is not None:
            section, flow = self._make_section("Расширения GNOME Shell", f"{len(exts)} включено")
            self._add_cards(flow, exts, "application-x-addon-symbolic")
            self._content_box.append(section)

        custom = data.get("custom_paths")
        if custom:
            section, flow = self._make_section("Дополнительные пути")
            self._add_cards(flow, custom, "folder-symbolic")
            self._content_box.append(section)

        packages_count = data.get("packages_count", 0)
        if packages_count > 0:
            section, flow = self._make_section("Системные пакеты", f"{packages_count} пакетов")
            flow.append(self._make_card(f"{packages_count} пакетов", "", "package-x-generic-symbolic"))
            self._content_box.append(section)

        self._stack.set_visible_child_name("content")
        self._confirm_btn.set_sensitive(True)

    def _on_confirm_clicked(self, _btn):
        self.close()
        self._on_confirm()
