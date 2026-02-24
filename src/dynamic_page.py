"""
dynamic_page.py — универсальный движок Data-Driven UI для ALT Booster.

Архитектура:
  run_check(check)      — проверка статуса по JSON-описанию
  ActionDispatcher      — выполнение action из JSON в фоновом потоке
  RowFactory            — фабрика виджетов Adw из JSON-описания строки
  DynamicPage           — Gtk.Box, строит интерфейс из JSON-словаря

Поддерживаемые типы строк (row.type):
  command_row  — кнопка выполнения команды с индикатором статуса
  dropdown_row — выпадающий список + кнопка применения
  file_row     — выбор файла + кнопка применения

Поддерживаемые типы action:
  privileged   — sudo через backend.run_privileged
  epm          — epm через backend.run_epm
  shell        — subprocess без root
  gsettings    — backend.run_gsettings
  open_url     — Gio.AppInfo.launch_default_for_uri
  builtin      — вызов функции из BUILTIN_REGISTRY

Поддерживаемые типы check:
  rpm              — rpm -q <value>
  flatpak          — flatpak list | grep <value>
  which            — which <value>
  path             — os.path.exists(~/<value>)
  systemd          — systemctl is-enabled <value>
  gsettings        — gsettings get schema key == expected
  gsettings_contains — gsettings get schema key contains value
  builtin          — вызов check-функции из BUILTIN_REGISTRY
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

import backend
import config


# ─────────────────────────────────────────────────────────────────────────────
# Встроенные функции (builtin)
# Сигнатура: fn(page: DynamicPage | None, arg: Any) -> bool
# ─────────────────────────────────────────────────────────────────────────────

_FASTFETCH_CONFIG = """{
    "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
    "logo": { "type": "small" },
    "display": { "separator": "\\u001b[90m \\u2590 " },
    "modules": [
        {"type":"os","key":"","keyColor":"94","format":"{2}"},
        {"type":"kernel","key":"","keyColor":"39"},
        {"type":"packages","key":"\\uf017","keyColor":"33"},
        {"type":"shell","key":"","keyColor":"94","format":"{1}"},
        {"type":"terminal","key":"","keyColor":"39","format":"{1}"},
        "break",
        {"type":"wm","key":"\\udb81\\udd6e","keyColor":"34"},
        {"type":"wmtheme","key":"\\udb80\\udc7c","keyColor":"33"},
        {"type":"icons","key":"\\udb80\\udcf8","keyColor":"93"},
        "break",
        {"type":"host","key":"\\udb80\\udf42","keyColor":"92"},
        {"type":"display","key":"\\udb83\\ude51","keyColor":"32"},
        {"type":"cpu","key":"\\udb80\\udc4d","keyColor":"96"},
        {"type":"gpu","key":"\\udb83\\udc2e","keyColor":"96"},
        {"type":"memory","key":"","keyColor":"36"},
        {"type":"uptime","key":"\\udb84\\udca6","keyColor":"39"},
        "break",
        "colors"
    ]
}"""

_ZSH_ALIASES = """
# Timeshift
alias tm="sudo timeshift"
alias tmc="sudo timeshift --create"
alias tmd="sudo timeshift --delete"
alias tmda="sudo timeshift --delete-all"
alias tml="sudo timeshift --list"
# Fastfetch
alias n="fastfetch -c ~/.config/fastfetch/plafonfetch.jsonc"
alias k="uname -rs"
alias g="gnome-shell --version"
alias f="lsb_release -sd"
alias c="clear"
alias find="epmqa"
# Upgrade
alias up="epm update && epm full-upgrade"
alias cc="sudo apt-get clean && flatpak uninstall --unused -y && sudo journalctl --vacuum-time=1weeks"
# PC
alias son="sudo systemctl suspend"
alias reboot="systemctl reboot"
alias ls="ls --color"
# Flatpak
alias fli="flatpak install --noninteractive -y flathub"
alias flr="flatpak remove --noninteractive -y"
alias fr="flatpak repair"
alias fl="flatpak list"
# GRUB
alias upgrub="sudo update-grub"
alias grubup="sudo update-grub"
# Other
alias sn="sudo nautilus"
alias vmax="sudo sysctl -w vm.max_map_count=2147483642"
"""


def _b_apply_papirus_icons(page: DynamicPage | None, _arg: Any) -> bool:
    scheme = backend.gsettings_get("org.gnome.desktop.interface", "color-scheme")
    theme = "Papirus-Dark" if "dark" in scheme.lower() else "Papirus"
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Тема иконок: {theme}\n")
    return ok


def _b_apply_adwaita_theme(page: DynamicPage | None, _arg: Any) -> bool:
    ok = (
        backend.run_gsettings(["set", "org.gnome.desktop.interface", "gtk-theme", "Adwaita"])
        and backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", "Adwaita"])
    )
    if page:
        page.log("\n✔  Adwaita применена!\n" if ok else "\n✘  Ошибка\n")
    return ok


def _b_apply_folder_color(page: DynamicPage | None, color: str) -> bool:
    ok_d = subprocess.run(["papirus-folders", "-C", color, "--theme", "Papirus-Dark"],
                          capture_output=True).returncode == 0
    ok_l = subprocess.run(["papirus-folders", "-C", color, "--theme", "Papirus"],
                          capture_output=True).returncode == 0
    ok = ok_d or ok_l
    if ok:
        config.state_set("folder_color", color)
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Цвет папок: {color}\n")
    return ok


def _b_reset_folder_color(page: DynamicPage | None, _arg: Any) -> bool:
    subprocess.run(["papirus-folders", "-D", "--theme", "Papirus-Dark"], capture_output=True)
    subprocess.run(["papirus-folders", "-D", "--theme", "Papirus"], capture_output=True)
    config.state_set("folder_color", None)
    if page:
        page.log("\n✔  Папки сброшены на стандартный цвет\n")
    return True


def _b_check_ptyxis_default(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/terminal"],
                       capture_output=True, text=True)
    return "org.gnome.Ptyxis.desktop" in r.stdout


def _b_set_ptyxis_default(page: DynamicPage | None, _arg: Any) -> bool:
    r = subprocess.run(["xdg-mime", "default", "org.gnome.Ptyxis.desktop",
                        "x-scheme-handler/terminal"], capture_output=True)
    ok = r.returncode == 0
    if page:
        page.log("\n✔  Ptyxis default!\n" if ok else "\n✘  Ошибка\n")
    return ok


def _b_check_shortcut_1(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(["dconf", "read",
        "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/command"],
        capture_output=True, text=True)
    return "'ptyxis'" in r.stdout


def _b_set_shortcut_1(page: DynamicPage | None, _arg: Any) -> bool:
    cmds = [
        ["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/name", "'Terminal 1'"],
        ["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/command", "'ptyxis'"],
        ["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/binding", "'<Primary><Alt>t'"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True)
    # Добавляем в массив если нет
    path = "'/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/'"
    arr_res = subprocess.run(["dconf", "read", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"],
                             capture_output=True, text=True)
    arr = arr_res.stdout.strip()
    if path not in arr:
        new_arr = f"[{path}]" if not arr or arr in ("@as []", "[]") else arr[:-1] + f", {path}]"
        subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings", new_arr])
    if page:
        page.log("\n✔  Ctrl+Alt+T назначен!\n")
    return True


def _b_check_shortcut_2(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(["dconf", "read",
        "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/command"],
        capture_output=True, text=True)
    return "'ptyxis'" in r.stdout


def _b_set_shortcut_2(page: DynamicPage | None, _arg: Any) -> bool:
    cmds = [
        ["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/name", "'Terminal 2'"],
        ["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/command", "'ptyxis'"],
        ["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/binding", "'<Super>Return'"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True)
    path = "'/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/'"
    arr_res = subprocess.run(["dconf", "read", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"],
                             capture_output=True, text=True)
    arr = arr_res.stdout.strip()
    if path not in arr:
        new_arr = f"[{path}]" if not arr or arr in ("@as []", "[]") else arr[:-1] + f", {path}]"
        subprocess.run(["dconf", "write", "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings", new_arr])
    if page:
        page.log("\n✔  Super+Enter назначен!\n")
    return True


def _b_check_zsh_default(_page: Any, _arg: Any) -> bool:
    username = os.environ.get("USER", "")
    r = subprocess.run(["getent", "passwd", username], capture_output=True, text=True)
    return "/zsh" in r.stdout


def _b_set_zsh_default(page: DynamicPage | None, _arg: Any) -> bool:
    username = os.environ.get("USER", "")
    event, result = threading.Event(), [False]

    def _done(ok: bool) -> None:
        result[0] = ok
        event.set()

    backend.run_privileged(["chsh", "-s", "/bin/zsh", username],
                           page.log if page else lambda _: None, _done)
    event.wait()
    if page:
        page.log("\n✔  ZSH shell по умолчанию!\n" if result[0] else "\n✘  Ошибка\n")
    return result[0]


def _b_install_zplug(page: DynamicPage | None, _arg: Any) -> bool:
    zplug_dir = os.path.expanduser("~/.zplug")
    if os.path.isdir(zplug_dir):
        if page:
            page.log("\nℹ  zplug уже установлен\n")
        return True
    r = subprocess.run(["git", "clone", "https://github.com/zplug/zplug", zplug_dir],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    if page:
        if r.stdout:
            page.log(r.stdout)
        if r.stderr:
            page.log(r.stderr)
        page.log("\n✔  zplug установлен!\n" if ok else "\n✘  Ошибка установки zplug\n")
    return ok


def _b_check_ptyxis_font(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(["dconf", "read", "/org/gnome/Ptyxis/Profiles/default/font-name"],
                       capture_output=True, text=True)
    return "FiraCode Nerd Font" in r.stdout


def _b_install_fastfetch_config(page: DynamicPage | None, _arg: Any) -> bool:
    cfg_dir = os.path.expanduser("~/.config/fastfetch")
    cfg_path = os.path.join(cfg_dir, "plafonfetch.jsonc")
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        with open(cfg_path, "w") as f:
            f.write(_FASTFETCH_CONFIG)
        if page:
            page.log(f"\n✔  Конфиг: {cfg_path}\n")
        return True
    except OSError as e:
        if page:
            page.log(f"\n✘  Ошибка: {e}\n")
        return False


def _b_check_zsh_aliases(_page: Any, _arg: Any) -> bool:
    zshrc = os.path.expanduser("~/.zshrc")
    try:
        return "# === ALT Booster aliases ===" in open(zshrc).read()
    except OSError:
        return False


def _b_add_zsh_aliases(page: DynamicPage | None, _arg: Any) -> bool:
    zshrc = os.path.expanduser("~/.zshrc")
    marker = "# === ALT Booster aliases ==="
    try:
        existing = open(zshrc).read() if os.path.exists(zshrc) else ""
        if marker in existing:
            if page:
                page.log("\nℹ  Алиасы уже добавлены\n")
            return True
        with open(zshrc, "a") as f:
            f.write(f"\n{marker}\n{_ZSH_ALIASES}")
        if page:
            page.log("\n✔  Алиасы добавлены в ~/.zshrc\n")
        return True
    except OSError as e:
        if page:
            page.log(f"\n✘  Ошибка: {e}\n")
        return False


# AMD builtins
_OVERCLOCK_PARAMS = "amdgpu.ppfeaturemask=0xffffffff radeon.cik_support=0 amdgpu.cik_support=1"
_GRUB_CONF = "/etc/sysconfig/grub2"


def _b_check_overclock(_page: Any, _arg: Any) -> bool:
    try:
        return "amdgpu.ppfeaturemask=0xffffffff" in open(_GRUB_CONF).read()
    except OSError:
        return False


def _b_enable_overclock(page: DynamicPage | None, _arg: Any) -> bool:
    cmd = [
        "bash", "-c",
        f"set -e; CONF={_GRUB_CONF}; PARAMS=\"{_OVERCLOCK_PARAMS}\"; "
        "grep -q 'amdgpu.ppfeaturemask=0xffffffff' \"$CONF\" && exit 0; "
        "sed -i \"s|^\\(GRUB_CMDLINE_LINUX_DEFAULT='[^']*\\)'|\\1 $PARAMS'|\" \"$CONF\"",
    ]
    event, result = threading.Event(), [False]

    def _done(ok: bool) -> None:
        result[0] = ok
        event.set()

    backend.run_privileged(cmd, page.log if page else lambda _: None, _done)
    event.wait()
    if page:
        page.log("\n✔  Параметры разгона добавлены\n" if result[0] else "\n✘  Ошибка записи в GRUB\n")
    return result[0]


def _b_check_wheel(_page: Any, _arg: Any) -> bool:
    username = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
    r = subprocess.run(["id", "-nG", username], capture_output=True, text=True)
    return "wheel" in r.stdout.split()


def _b_setup_lact_wheel(page: DynamicPage | None, _arg: Any) -> bool:
    username = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
    if not username:
        if page:
            page.log("\n✘  Не удалось определить пользователя\n")
        return False
    cmd = [
        "bash", "-c",
        f"usermod -aG wheel {username} && "
        "sed -i 's|\"admin_group\":.*|\"admin_group\": \"wheel\",|' /etc/lact/config.json 2>/dev/null || true",
    ]
    event, result = threading.Event(), [False]

    def _done(ok: bool) -> None:
        result[0] = ok
        event.set()

    backend.run_privileged(cmd, page.log if page else lambda _: None, _done)
    event.wait()
    if page:
        page.log("\n✔  Для применения нужно перезайти в сессию\n" if result[0] else "\n✘  Ошибка\n")
    return result[0]


def _b_apply_lact_config(page: DynamicPage | None, src_path: str) -> bool:
    if not src_path or not os.path.exists(src_path):
        if page:
            page.log("\n✘  Файл не найден\n")
        return False
    cmd = ["bash", "-c",
           f"mkdir -p /etc/lact && cp '{src_path}' /etc/lact/config.json && "
           "systemctl restart lactd 2>/dev/null || true"]
    event, result = threading.Event(), [False]

    def _done(ok: bool) -> None:
        result[0] = ok
        event.set()

    backend.run_privileged(cmd, page.log if page else lambda _: None, _done)
    event.wait()
    if result[0]:
        config.state_set("lact_applied_conf", src_path)
    if page:
        page.log(f"\n✔  Конфиг применён: {os.path.basename(src_path)}\n" if result[0] else "\n✘  Ошибка\n")
    return result[0]


def _b_confirm_reboot(page: DynamicPage | None, _arg: Any) -> bool:
    if page:
        GLib.idle_add(page._show_reboot_dialog)
    return True


# ── Реестр builtin-функций ────────────────────────────────────────────────────

BUILTIN_REGISTRY: dict[str, Callable] = {
    # appearance
    "apply_papirus_icons":      _b_apply_papirus_icons,
    "apply_adwaita_theme":      _b_apply_adwaita_theme,
    "apply_folder_color":       _b_apply_folder_color,
    "reset_folder_color":       _b_reset_folder_color,
    # terminal
    "check_ptyxis_default":     _b_check_ptyxis_default,
    "set_ptyxis_default":       _b_set_ptyxis_default,
    "check_shortcut_1":         _b_check_shortcut_1,
    "set_shortcut_1":           _b_set_shortcut_1,
    "check_shortcut_2":         _b_check_shortcut_2,
    "set_shortcut_2":           _b_set_shortcut_2,
    "check_zsh_default":        _b_check_zsh_default,
    "set_zsh_default":          _b_set_zsh_default,
    "install_zplug":            _b_install_zplug,
    "check_ptyxis_font":        _b_check_ptyxis_font,
    "install_fastfetch_config": _b_install_fastfetch_config,
    "check_zsh_aliases":        _b_check_zsh_aliases,
    "add_zsh_aliases":          _b_add_zsh_aliases,
    # amd
    "check_overclock":          _b_check_overclock,
    "enable_overclock":         _b_enable_overclock,
    "check_wheel":              _b_check_wheel,
    "setup_lact_wheel":         _b_setup_lact_wheel,
    "apply_lact_config":        _b_apply_lact_config,
    "confirm_reboot":           _b_confirm_reboot,
}


# ─────────────────────────────────────────────────────────────────────────────
# run_check — проверка статуса строки
# ─────────────────────────────────────────────────────────────────────────────

def run_check(check: dict | None) -> bool:
    """Выполняет проверку статуса из JSON-описания check."""
    if not check:
        return False
    kind = check.get("type")

    if kind == "rpm":
        return subprocess.run(["rpm", "-q", check["value"]],
                               capture_output=True).returncode == 0

    if kind == "flatpak":
        r = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                           capture_output=True, text=True)
        return check["value"] in r.stdout

    if kind == "which":
        return subprocess.run(["which", check["value"]],
                               capture_output=True).returncode == 0

    if kind == "path":
        return os.path.exists(os.path.expanduser(check["value"]))

    if kind == "systemd":
        return subprocess.run(["systemctl", "is-enabled", check["value"]],
                               capture_output=True).returncode == 0

    if kind == "gsettings":
        value = backend.gsettings_get(check["schema"], check["key"])
        return check.get("expected", "") in value

    if kind == "gsettings_contains":
        value = backend.gsettings_get(check["schema"], check["key"])
        return check.get("value", "") in value

    if kind == "builtin":
        fn = BUILTIN_REGISTRY.get(check.get("fn", ""))
        if fn:
            try:
                return bool(fn(None, None))
            except Exception:  # noqa: BLE001
                return False

    return False


# ─────────────────────────────────────────────────────────────────────────────
# ActionDispatcher — выполнение action из JSON
# ─────────────────────────────────────────────────────────────────────────────

class ActionDispatcher:
    """Выполняет action из JSON в фоновом потоке."""

    def __init__(self, page: DynamicPage) -> None:
        self._page = page

    def dispatch(
        self,
        action: dict,
        on_done: Callable[[bool], None] | None = None,
        arg: Any = None,
    ) -> None:
        """Запускает action в фоновом потоке, on_done(ok) вызывается в главном."""
        threading.Thread(
            target=self._run,
            args=(action, on_done, arg),
            daemon=True,
        ).start()

    def _run(self, action: dict, on_done: Callable | None, arg: Any) -> None:
        ok = False
        kind = action.get("type")
        page = self._page

        try:
            if kind == "privileged":
                ok = self._sync(backend.run_privileged, action["cmd"])

            elif kind == "epm":
                ok = self._sync(backend.run_epm, action["cmd"])

            elif kind == "shell":
                r = subprocess.run(action["cmd"], capture_output=True, text=True)
                if r.stdout:
                    GLib.idle_add(page.log, r.stdout)
                if r.stderr:
                    GLib.idle_add(page.log, r.stderr)
                ok = r.returncode == 0

            elif kind == "gsettings":
                ok = backend.run_gsettings(action["args"])

            elif kind == "open_url":
                GLib.idle_add(Gio.AppInfo.launch_default_for_uri, action["url"], None)
                ok = True

            elif kind == "builtin":
                fn_name = action.get("fn", "")
                fn = BUILTIN_REGISTRY.get(fn_name)
                if fn:
                    effective_arg = arg if action.get("arg_from") == "selected_option" else None
                    ok = bool(fn(page, effective_arg))
                else:
                    GLib.idle_add(page.log, f"\n✘  Неизвестная builtin: {fn_name}\n")

        except Exception as exc:  # noqa: BLE001
            GLib.idle_add(page.log, f"\n✘  Ошибка: {exc}\n")

        if on_done:
            GLib.idle_add(on_done, ok)

    def _sync(self, run_fn: Callable, cmd: list) -> bool:
        """Блокирует поток до завершения run_fn (через Event)."""
        event, result = threading.Event(), [False]

        def _done(ok: bool) -> None:
            result[0] = ok
            event.set()

        run_fn(cmd, self._page.log, _done)
        event.wait()
        return result[0]


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные фабрики виджетов (те же что в ui.py, без дублирования)
# ─────────────────────────────────────────────────────────────────────────────

def _make_icon(name: str, size: int = 22) -> Gtk.Image:
    icon = Gtk.Image.new_from_icon_name(name)
    icon.set_pixel_size(size)
    return icon


def _make_button(label: str, width: int = 130, style: str = "suggested-action") -> Gtk.Button:
    btn = Gtk.Button(label=label)
    btn.set_size_request(width, -1)
    btn.add_css_class(style)
    btn.add_css_class("pill")
    return btn


def _make_status_icon() -> Gtk.Image:
    icon = Gtk.Image()
    icon.set_pixel_size(18)
    return icon


def _set_status_ok(icon: Gtk.Image) -> None:
    icon.set_from_icon_name("object-select-symbolic")
    icon.add_css_class("success")


def _set_status_error(icon: Gtk.Image) -> None:
    icon.set_from_icon_name("dialog-error-symbolic")
    icon.remove_css_class("success")


def _clear_status(icon: Gtk.Image) -> None:
    icon.clear()
    icon.remove_css_class("success")


def _make_suffix_box(*widgets: Gtk.Widget | None) -> Gtk.Box:
    box = Gtk.Box(spacing=10)
    box.set_valign(Gtk.Align.CENTER)
    for w in widgets:
        if w is not None:
            box.append(w)
    return box


# ─────────────────────────────────────────────────────────────────────────────
# RowFactory — строит Adw.ActionRow из JSON-описания
# ─────────────────────────────────────────────────────────────────────────────

class RowFactory:
    def __init__(self, page: DynamicPage) -> None:
        self._page = page
        self._dispatcher = ActionDispatcher(page)

    def build(self, rd: dict) -> Adw.ActionRow:
        row_type = rd.get("type", "command_row")
        if row_type == "command_row":
            return self._command_row(rd)
        if row_type == "dropdown_row":
            return self._dropdown_row(rd)
        if row_type == "file_row":
            return self._file_row(rd)
        # fallback
        row = Adw.ActionRow()
        row.set_title(rd.get("title", "?"))
        return row

    # ── command_row ───────────────────────────────────────────────────────────

    def _command_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", ""))
        if rd.get("icon"):
            row.add_prefix(_make_icon(rd["icon"]))

        status = _make_status_icon()
        style = rd.get("button_style", "suggested-action")
        btn = _make_button(rd.get("button_label", "Запустить"), style=style)
        btn.set_sensitive(False)

        done_label = rd.get("button_done_label")
        orig_label = rd.get("button_label", "Запустить")
        action = rd.get("action", {})

        def _on_click(_b: Gtk.Button) -> None:
            btn.set_sensitive(False)
            btn.set_label("…")
            self._page.log(f"\n▶  {rd.get('title', '')}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done)

        def _on_done(ok: bool) -> None:
            if ok:
                _set_status_ok(status)
                if done_label:
                    btn.set_label(done_label)
                    btn.set_sensitive(False)
                    btn.remove_css_class("suggested-action")
                    btn.add_css_class("flat")
                else:
                    btn.set_label(orig_label)
                    btn.set_sensitive(True)
            else:
                _set_status_error(status)
                btn.set_label("Повторить")
                btn.set_sensitive(True)

        btn.connect("clicked", _on_click)

        # Сохраняем метаданные для поллинга check
        row._dp_status = status
        row._dp_button = btn
        row._dp_done_label = done_label
        row._dp_orig_label = orig_label
        row._dp_check = rd.get("check")

        row.add_suffix(_make_suffix_box(status, btn))
        return row

    # ── dropdown_row ──────────────────────────────────────────────────────────

    def _dropdown_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", ""))
        if rd.get("icon"):
            row.add_prefix(_make_icon(rd["icon"]))

        options = rd.get("options", [])
        dropdown = Gtk.DropDown.new_from_strings(options)
        dropdown.set_valign(Gtk.Align.CENTER)

        state_key = rd.get("state_key")
        if state_key:
            saved = config.state_get(state_key)
            if saved and saved in options:
                dropdown.set_selected(options.index(saved))

        status = _make_status_icon()
        btn = _make_button(rd.get("button_label", "Применить"), width=120)
        action = rd.get("action", {})

        def _on_click(_b: Gtk.Button) -> None:
            idx = dropdown.get_selected()
            selected = options[idx] if idx < len(options) else ""
            btn.set_sensitive(False)
            btn.set_label("…")
            self._page.log(f"\n▶  {rd.get('title', '')}: {selected}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done, arg=selected)

        def _on_done(ok: bool) -> None:
            _set_status_ok(status) if ok else _set_status_error(status)
            btn.set_label(rd.get("button_label", "Применить"))
            btn.set_sensitive(True)

        btn.connect("clicked", _on_click)

        row._dp_status = status
        row._dp_button = btn
        row._dp_check = None  # dropdown не имеет check — проверяем state_key
        row._dp_state_key = state_key

        row.add_suffix(_make_suffix_box(dropdown, status, btn))
        return row

    # ── file_row ──────────────────────────────────────────────────────────────

    def _file_row(self, rd: dict) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(rd.get("title", ""))
        row.set_subtitle(rd.get("subtitle", "Файл не выбран"))
        if rd.get("icon"):
            row.add_prefix(_make_icon(rd["icon"]))

        status = _make_status_icon()
        pick_btn = _make_button(rd.get("pick_label", "Выбрать файл"), width=150, style="flat")
        apply_btn = _make_button(rd.get("apply_label", "Применить"), width=170)
        apply_btn.set_sensitive(False)

        action = rd.get("action", {})
        state_key = rd.get("state_key")
        selected_path: list[str] = [""]

        # Восстанавливаем сохранённый конфиг
        if state_key:
            saved = config.state_get(state_key)
            if saved and os.path.exists(saved):
                row.set_subtitle(f"Применён: {os.path.basename(saved)}")

        def _on_pick(_b: Gtk.Button) -> None:
            dialog = Gtk.FileDialog()
            dialog.set_title("Выберите файл")
            ff_data = rd.get("file_filter")
            if ff_data:
                ff = Gtk.FileFilter()
                ff.set_name(ff_data.get("name", "Files"))
                for pat in ff_data.get("patterns", []):
                    ff.add_pattern(pat)
                store = Gio.ListStore.new(Gtk.FileFilter)
                store.append(ff)
                dialog.set_filters(store)
            root = self._page.get_root()
            dialog.open(root, None, _on_picked)

        def _on_picked(dialog: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = dialog.open_finish(res)
                if f:
                    path = f.get_path()
                    selected_path[0] = path
                    row.set_subtitle(os.path.basename(path))
                    apply_btn.set_sensitive(True)
                    _clear_status(status)
            except GLib.Error:
                pass

        def _on_apply(_b: Gtk.Button) -> None:
            apply_btn.set_sensitive(False)
            apply_btn.set_label("…")
            self._page.log(f"\n▶  Применение: {os.path.basename(selected_path[0])}...\n")
            self._dispatcher.dispatch(action, on_done=_on_done, arg=selected_path[0])

        def _on_done(ok: bool) -> None:
            _set_status_ok(status) if ok else _set_status_error(status)
            apply_btn.set_label(rd.get("apply_label", "Применить"))
            if ok:
                apply_btn.set_sensitive(False)
                apply_btn.remove_css_class("suggested-action")
                apply_btn.add_css_class("flat")
                if state_key:
                    row.set_subtitle(f"Применён: {os.path.basename(selected_path[0])}")
            else:
                apply_btn.set_sensitive(True)

        pick_btn.connect("clicked", _on_pick)
        apply_btn.connect("clicked", _on_apply)

        row._dp_status = status
        row._dp_check = None
        row.add_suffix(_make_suffix_box(status, pick_btn, apply_btn))
        return row


# ─────────────────────────────────────────────────────────────────────────────
# DynamicPage — главный класс
# ─────────────────────────────────────────────────────────────────────────────

class DynamicPage(Gtk.Box):
    """
    Универсальная вкладка, строящая интерфейс из JSON-словаря.

    Параметры:
        page_data : dict — распарсенный JSON (из modules/*.json)
        log_fn    : Callable[[str], None] — функция вывода в лог
    """

    def __init__(self, page_data: dict, log_fn: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.log = log_fn
        self._page_data = page_data
        self._rows_with_checks: list[Adw.ActionRow] = []
        self._factory = RowFactory(self)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        body.set_margin_start(20)
        body.set_margin_end(20)

        scroll.set_child(body)
        self.append(scroll)

        self._build(body)

        # Одноразовый фоновый поллинг статусов
        threading.Thread(target=self._poll_checks, daemon=True).start()

    def _build(self, body: Gtk.Box) -> None:
        for group_data in self._page_data.get("groups", []):
            group = Adw.PreferencesGroup()
            group.set_title(group_data.get("title", ""))
            if group_data.get("description"):
                group.set_description(group_data["description"])
            body.append(group)

            for row_data in group_data.get("rows", []):
                row = self._factory.build(row_data)
                group.add(row)
                if hasattr(row, "_dp_check"):
                    self._rows_with_checks.append(row)

    def _poll_checks(self) -> None:
        """Проверяет статус каждой строки и обновляет UI через GLib.idle_add."""
        for row in self._rows_with_checks:
            check = getattr(row, "_dp_check", None)
            ok = run_check(check)
            GLib.idle_add(self._apply_check_result, row, ok)

    def _apply_check_result(self, row: Adw.ActionRow, ok: bool) -> None:
        status = getattr(row, "_dp_status", None)
        btn = getattr(row, "_dp_button", None)
        done_label = getattr(row, "_dp_done_label", None)
        orig_label = getattr(row, "_dp_orig_label", None)

        if ok:
            if status:
                _set_status_ok(status)
            if btn:
                if done_label:
                    btn.set_label(done_label)
                    btn.set_sensitive(False)
                    btn.remove_css_class("suggested-action")
                    btn.add_css_class("flat")
                else:
                    btn.set_sensitive(False)
        else:
            if status:
                _clear_status(status)
            if btn:
                btn.set_sensitive(True)
                if orig_label:
                    btn.set_label(orig_label)

    def _show_reboot_dialog(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Перезагрузить систему?",
            body="Все несохранённые данные будут потеряны.",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("reboot", "Перезагрузить")
        dialog.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _do(_d: Adw.AlertDialog, response: str) -> None:
            if response == "reboot":
                backend.run_privileged(["reboot"], self.log, lambda _: None)

        dialog.connect("response", _do)
        dialog.present(self.get_root())
