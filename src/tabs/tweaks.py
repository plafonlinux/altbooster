
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
from gi.repository import Adw, Gdk, GLib, Gtk

from core import backend
from ui.common import load_module
from ui.widgets import make_button, make_icon, make_scrolled_page, scroll_child_into_view
from ui.rows import SettingRow, TaskRow

_ANANICY_RULES_REPO = "https://github.com/CachyOS/ananicy-rules"
_ANANICY_RULES_DIR  = "/etc/ananicy.d/cachyos-rules"

# Pill badge (Sisyphus-only packages) + red emphasis for irreversible migration warning row.
_tweak_page_css = Gtk.CssProvider()
_tweak_page_css.load_from_data(b"""
    .ab-tweak-sisyphus-badge {
        font-size: 0.72em;
        font-weight: 600;
        min-height: 0;
        padding: 2px 8px;
        border-radius: 999px;
        color: @error_color;
        background-color: alpha(@error_color, 0.18);
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

        scroll, body = make_scrolled_page()
        self._body = body
        self.append(scroll)
        self._search_focus_widgets: dict[str, Gtk.Widget] = {}

        self._build_sisyphus_group(body)
        self._build_scx_ui(body)
        self._build_ananicy_group(body)
        self._build_fixes_group(body)

    def focus_row_by_id(self, row_id: str) -> bool:
        w = self._search_focus_widgets.get(row_id)
        if w is None:
            return False
        scroll = self.get_first_child()
        if isinstance(scroll, Gtk.ScrolledWindow):
            scroll_child_into_view(scroll, w)
        GLib.idle_add(w.grab_focus)
        return True

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
        group.set_title("Планировщик CPU (SCX) — Экспериментально")
        body.append(group)

        scx_intro = self._add_info_row(
            group,
            "dialog-information-symbolic",
            "scx-scheds",
            "Экспериментальные планировщики sched-ext (LAVD) для игровых задач.",
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
        if not is_sis:
            self._row_lavd_std.set_sensitive(False)
            self._row_lavd_std.set_tooltip_text("Требуется репозиторий Sisyphus")

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
        if not is_sis:
            self._row_lavd_auto.set_sensitive(False)
            self._row_lavd_auto.set_tooltip_text("Требуется репозиторий Sisyphus")

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
        group.set_title("Приоритеты процессов")
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
