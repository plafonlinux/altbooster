
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from core import backend
from ui.common import load_module
from ui.widgets import make_button, make_icon, make_scrolled_page, scroll_child_into_view
from ui.rows import SettingRow, TaskRow
from core.sched_ext import has_sched_ext
from tabs.intel import ScxMeteorTweaksSection
from tabs.scx_sched_ext import SchedExtSupportSection
from tabs.system76_scheduler import System76SchedulerTweaksSection

_ANANICY_RULES_REPO = "https://github.com/CachyOS/ananicy-rules"
_ANANICY_RULES_DIR  = "/etc/ananicy.d/cachyos-rules"
_ALT_ZERO_GUIDE_URL = "https://plafon.gitbook.io/alt-zero"

_KERNEL_SCHED_SEARCH_IDS = frozenset({"sched_ext", "scx", "intel_scx_meteor"})
_USERSPACE_SCHED_SEARCH_IDS = frozenset({"ananicy", "system76_scheduler"})

# Pill badge (Sisyphus-only packages, orange) + red emphasis for irreversible migration warning row.
_tweak_page_css = Gtk.CssProvider()
_tweak_page_css.load_from_data(b"""
    .ab-tweak-sisyphus-badge {
        font-size: 0.72em;
        font-weight: 600;
        min-height: 0;
        padding: 2px 8px;
        border-radius: 999px;
        color: @warning_color;
        background-color: alpha(@warning_color, 0.22);
    }
    .ab-tweak-irreversible-row image,
    .ab-tweak-irreversible-row label {
        color: @error_color;
    }
    .ab-tweak-irreversible-row .dim-label {
        color: @error_color;
        opacity: 1;
    }
    .ab-tweak-branch-badge {
        font-size: 0.72em;
        font-weight: 600;
        min-height: 0;
        padding: 2px 8px;
        border-radius: 999px;
    }
    .ab-tweak-branch-badge-stable {
        color: @success_color;
        background-color: alpha(@success_color, 0.15);
    }
    .ab-tweak-branch-badge-rolling {
        color: @warning_color;
        background-color: alpha(@warning_color, 0.20);
    }
    .ab-tweak-branch-badge-unknown {
        color: alpha(currentColor, 0.55);
        background-color: alpha(currentColor, 0.08);
    }
    .ab-tweak-intel-only-badge {
        font-size: 0.72em;
        font-weight: 600;
        min-height: 0;
        padding: 2px 8px;
        border-radius: 999px;
        color: #62a0ea;
        background-color: alpha(#3584e4, 0.22);
    }
    .ab-tweak-experimental-badge {
        font-size: 0.72em;
        font-weight: 600;
        min-height: 0;
        padding: 2px 8px;
        border-radius: 999px;
        color: @error_color;
        background-color: alpha(@error_color, 0.18);
    }
    button.ab-alt-zero-guide-badge {
        font-size: 0.75em;
        font-weight: 600;
        min-height: 0;
        padding: 4px 12px;
        border-radius: 999px;
        color: @accent_color;
        background-color: alpha(@accent_color, 0.15);
    }
    button.ab-alt-zero-guide-badge:hover {
        background-color: alpha(@accent_color, 0.25);
    }
""")

def _is_sisyphus():
    for path in ["/etc/altlinux-release", "/etc/os-release"]:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    if "Sisyphus" in f.read():
                        return True
            except Exception:
                pass
    return False


def _detect_branch():
    try:
        for fname in sorted(os.listdir("/etc/apt/sources.list.d")):
            if not fname.endswith(".list"):
                continue
            with open(f"/etc/apt/sources.list.d/{fname}", encoding="utf-8") as f:
                for line in f:
                    if not line.startswith("rpm "):
                        continue
                    if "Sisyphus" in line:
                        return "sisyphus"
                    m = re.search(r"\bp(\d+)/branch\b", line)
                    if m:
                        return f"p{m.group(1)}"
    except Exception:
        pass
    return "unknown"


class TweaksPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _tweak_page_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.set_vexpand(True)
        self._search_focus_widgets: dict[str, Gtk.Widget] = {}

        self._sub_stack = Adw.ViewStack()
        self._sub_stack.set_vexpand(True)

        sub_switcher = Adw.ViewSwitcher()
        sub_switcher.set_stack(self._sub_stack)
        sub_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        sub_switcher.set_halign(Gtk.Align.CENTER)
        sub_switcher.set_margin_top(10)
        sub_switcher.set_margin_bottom(6)
        sub_switcher.set_margin_start(20)
        sub_switcher.set_margin_end(20)

        self.append(sub_switcher)
        self.append(self._sub_stack)

        scroll_general, body_general = make_scrolled_page()
        self._scroll_general = scroll_general
        self._build_platform_sisyphus_intro(body_general)
        self._build_sisyphus_group(body_general)
        self._build_fixes_group(body_general)
        self._sub_stack.add_titled_with_icon(
            scroll_general, "general", "Общие твики", "preferences-system-symbolic",
        )

        scroll_kernel, body_kernel = make_scrolled_page()
        self._scroll_kernel_scheduler = scroll_kernel
        self._sched_ext_section = SchedExtSupportSection(self._log, self)
        w_sched = self._sched_ext_section.append_to(body_kernel)
        self._search_focus_widgets["sched_ext"] = w_sched
        self._build_scx_ui(body_kernel)
        self._meteor_section = ScxMeteorTweaksSection(self._log, self)
        focus_meteor = self._meteor_section.append_to(body_kernel)
        self._search_focus_widgets["intel_scx_meteor"] = focus_meteor
        self._sched_ext_section.add_listener(self._sync_scx_sched_ext_dependents)
        self._sync_scx_sched_ext_dependents()
        self._sub_stack.add_titled_with_icon(
            scroll_kernel, "kernel_sched", "Планировщик ядра", "cpu-symbolic",
        )

        scroll_prio, body_prio = make_scrolled_page()
        self._scroll_userspace_priorities = scroll_prio
        self._build_userspace_priorities_tab_intro(body_prio)
        self._build_ananicy_group(body_prio)
        self._system76_section = System76SchedulerTweaksSection(self._log, self)
        w_s76 = self._system76_section.append_to(body_prio)
        self._search_focus_widgets["system76_scheduler"] = w_s76
        self._sub_stack.add_titled_with_icon(
            scroll_prio, "userspace_prio", "Приоритеты процессов", "system-run-symbolic",
        )

    def focus_row_by_id(self, row_id: str) -> bool:
        w = self._search_focus_widgets.get(row_id)
        if w is None:
            return False
        if row_id in _KERNEL_SCHED_SEARCH_IDS:
            self._sub_stack.set_visible_child_name("kernel_sched")
            scroll = self._scroll_kernel_scheduler
        elif row_id in _USERSPACE_SCHED_SEARCH_IDS:
            self._sub_stack.set_visible_child_name("userspace_prio")
            scroll = self._scroll_userspace_priorities
        else:
            self._sub_stack.set_visible_child_name("general")
            scroll = self._scroll_general
        scroll_child_into_view(scroll, w)
        GLib.idle_add(w.grab_focus)
        return True

    def _sync_scx_sched_ext_dependents(self) -> None:
        ok = has_sched_ext()
        self._apply_lavd_sched_ext_gating(ok)
        self._meteor_section.apply_sched_ext_gate(ok)

    def _apply_lavd_sched_ext_gating(self, sched_ok: bool) -> None:
        is_sis = _is_sisyphus()
        for row in (self._row_lavd_std, self._row_lavd_auto):
            row.set_sensitive(is_sis and sched_ok)
            if not is_sis:
                row.set_tooltip_text("Требуется репозиторий Sisyphus")
            elif not sched_ok:
                row.set_tooltip_text(
                    "Сначала подключите sched_ext в ядре (блок «Поддержка sched_ext в ядре», подвкладка «Планировщик ядра»)."
                )
            else:
                row.set_tooltip_text("")

    def _on_alt_zero_guide_clicked(self, _btn):
        try:
            Gio.AppInfo.launch_default_for_uri(_ALT_ZERO_GUIDE_URL, None)
        except GLib.Error:
            pass

    def _build_platform_sisyphus_intro(self, body):
        group = Adw.PreferencesGroup()
        row = Adw.ActionRow()
        row.set_title("Платформа (p10, p11, …) и Сизиф (Sisyphus)")
        row.set_subtitle(
            "Ветки вида p10, p11 — это стабильная платформа ALT: согласованный набор пакетов и обновлений "
            "в духе LTS, предсказуемый цикл обслуживания, ориентир на рабочие станции и корпоративное "
            "применение (буква p — «платформа»).\n\n"
            "Sisyphus (в обиходе — Сизиф) — основной rolling-репозиторий разработки: пакеты обновляются "
            "постоянно, в нём появляются новейшие версии ПО. От него собирают Regular и другие «живые» "
            "сборки; база свежее, но изменения и регрессии возможны чаще, чем на стабильной платформе.\n\n"
            "Переход с ветки p* на репозиторий Sisyphus (блок ниже) — это смена стабильной платформы на "
            "rolling: откат к прежней ветке без переустановки системы обычно невозможен."
        )
        row.set_activatable(False)
        row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))

        link_btn = Gtk.Button()
        link_btn.add_css_class("ab-alt-zero-guide-badge")
        link_btn.add_css_class("flat")
        link_btn.set_valign(Gtk.Align.CENTER)
        link_btn.set_tooltip_text("ALT Zero")
        link_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        link_inner.set_valign(Gtk.Align.CENTER)
        ext_icon = Gtk.Image.new_from_icon_name("adw-external-link-symbolic")
        ext_icon.set_pixel_size(14)
        link_inner.append(ext_icon)
        link_inner.append(Gtk.Label(label="ALT Zero"))
        link_btn.set_child(link_inner)
        link_btn.connect("clicked", self._on_alt_zero_guide_clicked)
        row.add_suffix(link_btn)

        group.add(row)
        body.append(group)

    def _build_userspace_priorities_tab_intro(self, body):
        group = Adw.PreferencesGroup()
        self._add_info_row(
            group,
            "dialog-information-symbolic",
            "Приоритеты и отзывчивость в userspace",
            "Здесь фоновые службы, которые работают поверх обычного планировщика ядра (как правило CFS): "
            "правила nice, I/O-приоритеты, иногда настройки латентностей CFS. Ядро само выстраивает "
            "очередь готовых потоков, но получает подсказки о важности процессов.\n\n"
            "ananicy-cpp (правила от CachyOS) и System76 Scheduler из Pop!_OS решают похожую задачу — "
            "не включайте их одновременно. Совместимость с LAVD/sched_ext возможна, но стек сложнее и "
            "поведение менее предсказуемо; обычно выбирают один основной механизм.",
        )
        body.append(group)

    def _add_info_row(
        self,
        group: Adw.PreferencesGroup,
        icon_name: str,
        title: str,
        subtitle: str,
        *,
        sisyphus_only_badge: bool = False,
        sisyphus_only_tooltip: str = "",
        error_emphasis: bool = False,
    ) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_activatable(False)
        if error_emphasis:
            row.add_css_class("ab-tweak-irreversible-row")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_valign(Gtk.Align.CENTER)
        row.add_prefix(icon)
        if sisyphus_only_badge:
            badge = Gtk.Label(label="Только Sisyphus")
            badge.add_css_class("ab-tweak-sisyphus-badge")
            badge.set_valign(Gtk.Align.CENTER)
            if sisyphus_only_tooltip:
                badge.set_tooltip_text(sisyphus_only_tooltip)
            row.add_suffix(badge)
        group.add(row)
        return row

    def _experimental_header_badge(self) -> Gtk.Widget:
        lbl = Gtk.Label(label="Экспериментально")
        lbl.add_css_class("ab-tweak-experimental-badge")
        lbl.set_valign(Gtk.Align.CENTER)
        lbl.set_tooltip_text(
            "Экспериментальная функция: возможны сбои и регрессии — используйте на свой страх и риск."
        )
        return lbl

    def _make_branch_badge_label(self, branch: str) -> Gtk.Widget:
        if branch == "sisyphus":
            text = "Текущая ветка: Sisyphus"
            variant = "ab-tweak-branch-badge-rolling"
        elif re.match(r"^p\d+$", branch):
            text = f"Текущая ветка: {branch}"
            variant = "ab-tweak-branch-badge-stable"
        elif branch == "unknown":
            text = "Текущая ветка: неизвестно"
            variant = "ab-tweak-branch-badge-unknown"
        else:
            text = f"Текущая ветка: {branch}"
            variant = "ab-tweak-branch-badge-unknown"
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("ab-tweak-branch-badge")
        lbl.add_css_class(variant)
        lbl.set_valign(Gtk.Align.CENTER)
        return lbl

    def _build_sisyphus_group(self, body):
        self._branch = _detect_branch()

        group = Adw.PreferencesGroup()
        group.set_title("Переход на Sisyphus")
        group.set_header_suffix(self._make_branch_badge_label(self._branch))
        body.append(group)

        if self._branch == "sisyphus":
            row = Adw.ActionRow()
            row.set_title("Уже на Sisyphus")
            row.set_subtitle("Система использует rolling release репозиторий")
            row.add_prefix(make_icon("emblem-ok-symbolic"))
            group.add(row)
            return

        self._add_info_row(
            group,
            "dialog-warning-symbolic",
            "Переход необратим",
            "Sisyphus — rolling release, пакеты обновляются каждый день. "
            "Откат к стабильной ветке без переустановки системы невозможен.",
            error_emphasis=True,
        )

        check_row = Adw.ActionRow()
        check_row.set_title("Анализ обновлений")
        check_row.set_subtitle("Переключает репозитории и симулирует dist-upgrade")
        check_row.add_prefix(make_icon("system-search-symbolic"))
        self._btn_check = make_button("Проверить", width=110)
        self._btn_check.set_valign(Gtk.Align.CENTER)
        self._btn_check.connect("clicked", self._on_check_clicked)
        check_row.add_suffix(self._btn_check)
        group.add(check_row)

        self._result_row = Adw.ActionRow()
        self._result_row.set_title("Результат")
        self._result_row.set_visible(False)
        group.add(self._result_row)

        self._row_sisy_warn2 = Adw.ActionRow()
        self._row_sisy_warn2.set_title("Это изменит всю систему")
        self._row_sisy_warn2.set_subtitle(
            "После обновления все пакеты перейдут на rolling release версии. "
            "Убедись, что сделал резервную копию важных данных."
        )
        self._row_sisy_warn2.set_activatable(False)
        w2 = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        w2.set_valign(Gtk.Align.CENTER)
        self._row_sisy_warn2.add_prefix(w2)
        self._row_sisy_warn2.set_visible(False)
        group.add(self._row_sisy_warn2)

        upgrade_row = Adw.ActionRow()
        upgrade_row.set_title("Обновление до Sisyphus")
        upgrade_row.set_subtitle("apt-get dist-upgrade по репозиторию Sisyphus")
        upgrade_row.add_prefix(make_icon("system-software-update-symbolic"))
        upgrade_row.set_visible(False)

        btn_box = Gtk.Box(spacing=8)
        btn_box.set_valign(Gtk.Align.CENTER)

        self._btn_revert = make_button("Откатить репозитории", width=175)
        self._btn_revert.connect("clicked", self._on_revert_clicked)
        btn_box.append(self._btn_revert)

        self._btn_upgrade = make_button("Обновить вопреки всему!", width=185)
        self._btn_upgrade.add_css_class("destructive-action")
        self._btn_upgrade.connect("clicked", self._on_upgrade_clicked)
        btn_box.append(self._btn_upgrade)

        upgrade_row.add_suffix(btn_box)
        group.add(upgrade_row)
        self._upgrade_row = upgrade_row

    def _on_check_clicked(self, btn):
        btn.set_sensitive(False)
        btn.set_label("…")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Анализ обновлений Sisyphus...")
        threading.Thread(target=self._do_check, daemon=True).start()

    def _do_check(self):
        self._log("\n▶  Переключаем репозитории на Sisyphus для анализа...\n")

        backup_cmd = (
            "mkdir -p /tmp/altbooster-repos-bak && "
            "cp /etc/apt/sources.list.d/*.list /tmp/altbooster-repos-bak/"
        )
        ok = backend.run_privileged_sync(["bash", "-c", backup_cmd], self._log)
        if not ok:
            GLib.idle_add(self._check_done_error, "Ошибка резервного копирования репозиториев")
            return

        switch_cmd = (
            r"sed -i 's/ p[0-9]*\/branch / Sisyphus /g' /etc/apt/sources.list.d/*.list"
        )
        ok = backend.run_privileged_sync(["bash", "-c", switch_cmd], self._log)
        if not ok:
            GLib.idle_add(self._check_done_error, "Ошибка переключения репозиториев")
            return

        self._log("▶  Обновляем индекс пакетов...\n")
        ok = backend.run_privileged_sync(["apt-get", "update"], self._log)
        if not ok:
            GLib.idle_add(self._check_done_error, "Ошибка обновления индекса")
            return

        self._log("▶  Симуляция dist-upgrade...\n")
        try:
            result = subprocess.run(
                ["apt-get", "--simulate", "dist-upgrade"],
                capture_output=True, text=True,
            )
            output = result.stdout + result.stderr
        except OSError as e:
            GLib.idle_add(self._check_done_error, f"Ошибка симуляции: {e}")
            return

        pkg_count = sum(1 for line in output.splitlines() if line.startswith("Inst "))
        size_str = ""
        m = re.search(r"Need to get ([0-9 ,.]+(?:kB|MB|GB))", output)
        if m:
            size_str = f" · ~{m.group(1)}"

        GLib.idle_add(self._check_done_ok, pkg_count, size_str)

    def _check_done_error(self, msg):
        self._btn_check.set_sensitive(True)
        self._btn_check.set_label("Повтор")
        self._log(f"✘  {msg}\n")
        win = self.get_root()
        if win and hasattr(win, "stop_progress"):
            win.stop_progress(False)

    def _check_done_ok(self, pkg_count, size_str):
        self._result_row.set_title(f"{pkg_count} пакетов к обновлению{size_str}")
        self._result_row.set_subtitle("Репозитории переключены на Sisyphus. Подтверди или откати.")
        self._result_row.set_visible(True)
        self._row_sisy_warn2.set_visible(True)
        self._upgrade_row.set_visible(True)
        self._btn_check.set_label("Обновлено")
        self._log(f"✔  Анализ завершён: {pkg_count} пакетов{size_str}\n")
        win = self.get_root()
        if win and hasattr(win, "stop_progress"):
            win.stop_progress(True)

    def _on_revert_clicked(self, btn):
        btn.set_sensitive(False)
        self._btn_upgrade.set_sensitive(False)
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Откат репозиториев...")

        revert_cmd = (
            "cp /tmp/altbooster-repos-bak/*.list /etc/apt/sources.list.d/ && "
            "rm -rf /tmp/altbooster-repos-bak && "
            "apt-get update"
        )

        def _on_done(ok):
            self._log("✔  Репозитории откачены\n" if ok else "✘  Ошибка отката\n")
            self._upgrade_row.set_visible(False)
            self._row_sisy_warn2.set_visible(False)
            self._result_row.set_visible(False)
            self._btn_check.set_label("Проверить")
            self._btn_check.set_sensitive(True)
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(["bash", "-c", revert_cmd], self._log, _on_done)

    def _on_upgrade_clicked(self, btn):
        btn.set_sensitive(False)
        self._btn_revert.set_sensitive(False)
        self._log("\n▶  Запуск dist-upgrade до Sisyphus...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Обновление системы до Sisyphus...")

        def _on_done(ok):
            if ok:
                self._log("✔  Система обновлена до Sisyphus!\n")
                self._result_row.set_subtitle("Обновление завершено успешно.")
            else:
                self._log("✘  Ошибка обновления\n")
                btn.set_sensitive(True)
                self._btn_revert.set_sensitive(True)
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(
            ["apt-get", "dist-upgrade", "-y"],
            self._log, _on_done,
        )

    def _build_scx_ui(self, body):
        is_sis = _is_sisyphus()
        group = Adw.PreferencesGroup()
        group.set_title("Планировщик AMD Ryzen (SCX LAVD)")
        group.set_header_suffix(self._experimental_header_badge())
        body.append(group)

        scx_intro = self._add_info_row(
            group,
            "dialog-information-symbolic",
            "scx-scheds",
            "LAVD (Latency-aware Virtual Deadline) — планировщик из линейки sched-ext "
            "(Igalia, Valve): переводит задачи на «виртуальные дедлайны», чтобы снизить "
            "задержки в играх и при скачках нагрузки на рабочем столе.\n\n"
            "Чтобы включить LAVD, нужны одновременно пакет scx-scheds из Sisyphus и уже "
            "загруженное ядро с каталогом /sys/kernel/sched_ext. Подготовку ядра выполняют "
            "в блоке «Поддержка sched_ext в ядре» на подвкладке «Планировщик ядра». "
            "Заголовок группы отражает типичный сценарий для Ryzen; на других x86_64 LAVD "
            "допустим при той же связке ядра и пакета.",
            sisyphus_only_badge=not is_sis,
            sisyphus_only_tooltip="Пакет scx-scheds доступен только в репозитории Sisyphus.",
        )
        self._search_focus_widgets["scx"] = scx_intro

        self._row_lavd_std = SettingRow(
            "utilities-terminal-symbolic", "LAVD (Performance)",
            "Оптимизация для игр и десктопа", "Включить",
            lambda r: self._enable_lavd(r, autopower=False),
            lambda: self._check_lavd_active(autopower=False),
            "amd_scx_lavd_std", "Активно",
            self._disable_lavd, "Выключить",
            help_text="LAVD (Latency-aware Virtual Deadline) — планировщик от Igalia/Valve. Снижает задержки в играх и улучшает отзывчивость системы."
        )
        group.add(self._row_lavd_std)

        self._row_lavd_auto = SettingRow(
            "battery-symbolic", "LAVD (Autopower)",
            "Режим для ноутбуков и мини-ПК", "Включить",
            lambda r: self._enable_lavd(r, autopower=True),
            lambda: self._check_lavd_active(autopower=True),
            "amd_scx_lavd_auto", "Активно",
            self._disable_lavd, "Выключить",
            help_text="Версия с флагом --autopower. Адаптирует частоты для экономии энергии при работе от батареи."
        )
        group.add(self._row_lavd_auto)

    def _check_scx_installed(self):
        return backend.check_app_installed({"check": ["rpm", "scx-scheds"]})

    def _check_lavd_active(self, autopower):
        if subprocess.run(["systemctl", "is-active", "scx_lavd"], capture_output=True).returncode != 0:
            return False
        try:
            with open("/etc/systemd/system/scx_lavd.service", encoding="utf-8") as f:
                content = f.read()
            return ("--autopower" in content) == autopower
        except Exception:
            return False

    def _enable_lavd(self, row, autopower):
        row.set_working()
        mode_str = "Autopower" if autopower else "Performance"
        self._log(f"\n▶  Включение LAVD ({mode_str})...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress(f"Включение LAVD ({mode_str})...")

        def _thread_worker():
            if not has_sched_ext():
                GLib.idle_add(
                    self._log,
                    "✘  В текущем ядре нет sched_ext. Установите ядро через блок "
                    "«Поддержка sched_ext в ядре» на подвкладке «Планировщик ядра» и перезагрузитесь.\n",
                )
                GLib.idle_add(row.set_done, False)
                if win and hasattr(win, "stop_progress"):
                    GLib.idle_add(win.stop_progress, False)
                return

            if not self._check_scx_installed():
                GLib.idle_add(self._log, "▶  Установка пакета scx-scheds...\n")
                ok_inst = backend.run_privileged_sync(["apt-get", "install", "-y", "scx-scheds"], self._log)
                if not ok_inst:
                    GLib.idle_add(self._log, "✘  Ошибка установки пакета scx-scheds\n")
                    GLib.idle_add(row.set_done, False)
                    if win and hasattr(win, "stop_progress"):
                        GLib.idle_add(win.stop_progress, False)
                    return

            exec_start = "/usr/bin/scx_lavd --autopower" if autopower else "/usr/bin/scx_lavd"
            service_content = f"""[Unit]
Description=SCX LAVD CPU Scheduler
Documentation=https://github.com/sched-ext/scx
After=multi-user.target
ConditionPathExists=/sys/kernel/sched_ext
[Service]
Type=simple
ExecStart={exec_start}
ExecStop=/bin/kill -SIGINT $MAINPID
Restart=on-failure
RestartSec=5
KillMode=mixed
[Install]
WantedBy=multi-user.target
"""
            try:
                with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
                    tmp.write(service_content)
                    tmp_path = tmp.name
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка подготовки файла: {e}\n")
                GLib.idle_add(row.set_done, False)
                if win and hasattr(win, "stop_progress"):
                    GLib.idle_add(win.stop_progress, False)
                return

            cmd = [
                "bash", "-c",
                f"mv {shlex.quote(tmp_path)} /etc/systemd/system/scx_lavd.service && "
                "chmod 644 /etc/systemd/system/scx_lavd.service && "
                "systemctl daemon-reload && "
                "systemctl enable scx_lavd && "
                "systemctl restart scx_lavd"
            ]
            ok_service = backend.run_privileged_sync(cmd, self._log)

            def _finish_ui():
                row.set_done(ok_service)
                other_row = self._row_lavd_std if autopower else self._row_lavd_auto
                other_row._refresh()
                if ok_service:
                    self._log(f"✔  LAVD ({mode_str}) активирован!\n")
                else:
                    self._log("✘  Ошибка включения LAVD\n")
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok_service)

            GLib.idle_add(_finish_ui)

        threading.Thread(target=_thread_worker, daemon=True).start()

    def _disable_lavd(self, row):
        row.set_working()
        self._log("\n▶  Отключение LAVD...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Отключение LAVD...")

        def _on_done(ok):
            row.set_undo_done(ok)
            GLib.idle_add(self._row_lavd_std._refresh)
            GLib.idle_add(self._row_lavd_auto._refresh)
            self._log("✔  LAVD отключён\n" if ok else "✘  Ошибка отключения\n")
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(
            ["systemctl", "disable", "--now", "scx_lavd"],
            self._log, _on_done,
        )

    def _build_ananicy_group(self, body):
        is_sis = _is_sisyphus()
        group = Adw.PreferencesGroup()
        group.set_title("Современный планировщик для Linux (от CachyOS)")
        body.append(group)

        an_intro = self._add_info_row(
            group,
            "dialog-information-symbolic",
            "ananicy-cpp",
            "Автоматически управляет приоритетами процессов по правилам. "
            "Правила CachyOS охватывают браузеры, Steam и игровые процессы — "
            "системный планировщик получает подсказки о важности каждого процесса.",
            sisyphus_only_badge=not is_sis,
            sisyphus_only_tooltip="Пакет ananicy-cpp доступен только в репозитории Sisyphus.",
        )
        self._search_focus_widgets["ananicy"] = an_intro

        self._row_ananicy_install = SettingRow(
            "system-run-symbolic",
            "ananicy-cpp",
            "Установка пакета и правил CachyOS из GitHub",
            "Установить",
            self._install_ananicy,
            lambda: shutil.which("ananicy-cpp") is not None,
            "ananicy_installed",
            done_label="Установлен",
            on_undo=self._uninstall_ananicy,
            undo_label="Удалить",
            undo_icon="user-trash-symbolic",
            help_text=(
                "Устанавливает ananicy-cpp через epm и клонирует правила CachyOS "
                f"в {_ANANICY_RULES_DIR}. Правила включают Steam и дочерние процессы."
            ),
        )
        group.add(self._row_ananicy_install)
        if not is_sis:
            self._row_ananicy_install.set_sensitive(False)
            self._row_ananicy_install.set_tooltip_text("Требуется репозиторий Sisyphus")

        self._row_ananicy_service = SettingRow(
            "media-playback-start-symbolic",
            "Автозапуск при загрузке",
            "systemd-сервис ananicy-cpp (enable + start)",
            "Включить",
            self._enable_ananicy_service,
            self._check_ananicy_service,
            "ananicy_service",
            done_label="Активен",
            on_undo=self._disable_ananicy_service,
            undo_label="Выключить",
            undo_icon="media-playback-stop-symbolic",
            help_text="Запускает ananicy-cpp при каждой загрузке системы через systemd.",
        )
        group.add(self._row_ananicy_service)
        if not is_sis:
            self._row_ananicy_service.set_sensitive(False)
            self._row_ananicy_service.set_tooltip_text("Требуется репозиторий Sisyphus")

    def _check_ananicy_service(self):
        try:
            r = subprocess.run(
                ["systemctl", "is-enabled", "ananicy-cpp"],
                capture_output=True, text=True,
            )
            return r.returncode == 0 and r.stdout.strip() in ("enabled", "static")
        except OSError:
            return False

    def _install_ananicy(self, row):
        row.set_working()
        self._log("\n▶  Установка ananicy-cpp...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Установка ananicy-cpp...")

        def _thread():
            ok = backend.run_privileged_sync(
                ["epm", "install", "-y", "ananicy-cpp", "git"],
                self._log,
            )
            if ok:
                clone_cmd = (
                    f"if [ ! -d {shlex.quote(_ANANICY_RULES_DIR)} ]; then "
                    f"git clone --depth=1 {_ANANICY_RULES_REPO} {shlex.quote(_ANANICY_RULES_DIR)}; "
                    f"else echo 'Правила уже установлены.'; fi"
                )
                ok = backend.run_privileged_sync(["bash", "-c", clone_cmd], self._log)

            def _finish():
                row.set_done(ok)
                if ok:
                    self._log("✔  ananicy-cpp установлен!\n")
                    self._row_ananicy_service._refresh()
                else:
                    self._log("✘  Ошибка установки ananicy-cpp\n")
                if win and hasattr(win, "stop_progress"):
                    win.stop_progress(ok)

            GLib.idle_add(_finish)

        threading.Thread(target=_thread, daemon=True).start()

    def _uninstall_ananicy(self, row):
        row.set_working()
        self._log("\n▶  Удаление ananicy-cpp...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Удаление ananicy-cpp...")

        cmd = [
            "bash", "-c",
            f"systemctl disable --now ananicy-cpp 2>/dev/null || true; "
            f"epm remove -y ananicy-cpp; "
            f"rm -rf {shlex.quote(_ANANICY_RULES_DIR)}",
        ]

        def _on_done(ok):
            row.set_undo_done(ok)
            GLib.idle_add(self._row_ananicy_service._refresh)
            self._log("✔  ananicy-cpp удалён\n" if ok else "✘  Ошибка удаления\n")
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(cmd, self._log, _on_done)

    def _enable_ananicy_service(self, row):
        row.set_working()
        self._log("\n▶  Включение сервиса ananicy-cpp...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Включение ananicy-cpp...")

        def _on_done(ok):
            row.set_done(ok)
            self._log(
                "✔  ananicy-cpp запущен и добавлен в автозагрузку\n" if ok
                else "✘  Ошибка включения сервиса\n"
            )
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(
            ["systemctl", "enable", "--now", "ananicy-cpp"],
            self._log, _on_done,
        )

    def _disable_ananicy_service(self, row):
        row.set_working()
        self._log("\n▶  Отключение сервиса ananicy-cpp...\n")
        win = self.get_root()
        if win and hasattr(win, "start_progress"):
            win.start_progress("Отключение ananicy-cpp...")

        def _on_done(ok):
            row.set_undo_done(ok)
            self._log(
                "✔  ananicy-cpp остановлен и убран из автозагрузки\n" if ok
                else "✘  Ошибка отключения\n"
            )
            if win and hasattr(win, "stop_progress"):
                win.stop_progress(ok)

        backend.run_privileged(
            ["systemctl", "disable", "--now", "ananicy-cpp"],
            self._log, _on_done,
        )

    def _build_fixes_group(self, body):
        try:
            data = load_module("maintenance")
            all_tasks = data.get("tasks", [])
        except (OSError, json.JSONDecodeError):
            all_tasks = []

        fix_ids = {"fix_gdm_usb", "fix_gsconnect", "disable_tracker"}
        fix_tasks = [t for t in all_tasks if t["id"] in fix_ids]

        if not fix_tasks:
            return

        group = Adw.PreferencesGroup()
        group.set_title("Различные баги и фиксы")
        body.append(group)

        for task in fix_tasks:
            row = TaskRow(task, self._log, None, btn_label="Применить")
            group.add(row)
