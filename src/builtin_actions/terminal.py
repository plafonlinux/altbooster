"""Встроенные функции для вкладки «Терминал»."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import backend

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

_KEYBINDINGS_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
_ZSH_MARKER = "# === ALT Booster aliases ==="


def _add_custom_keybinding(index: int) -> None:
    path = f"'{_KEYBINDINGS_BASE}/custom{index}/'"
    arr_res = subprocess.run(
        ["dconf", "read", _KEYBINDINGS_BASE],
        capture_output=True, text=True,
    )
    arr = arr_res.stdout.strip()
    if path not in arr:
        if not arr or arr in ("@as []", "[]"):
            new_arr = f"[{path}]"
        else:
            new_arr = arr[:-1] + f", {path}]"
        subprocess.run(["dconf", "write", _KEYBINDINGS_BASE, new_arr])


def check_ptyxis_default(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(
        ["xdg-mime", "query", "default", "x-scheme-handler/terminal"],
        capture_output=True, text=True,
    )
    return "org.gnome.Ptyxis.desktop" in r.stdout


def set_ptyxis_default(page, _arg: Any) -> bool:
    r = subprocess.run(
        ["xdg-mime", "default", "org.gnome.Ptyxis.desktop", "x-scheme-handler/terminal"],
        capture_output=True,
    )
    ok = r.returncode == 0
    if page:
        page.log("\n✔  Ptyxis default!\n" if ok else "\n✘  Ошибка\n")
    return ok


def check_ptyxis_font(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(
        ["dconf", "read", "/org/gnome/Ptyxis/Profiles/default/font-name"],
        capture_output=True, text=True,
    )
    return "FiraCode Nerd Font" in r.stdout


def check_shortcut_1(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(
        ["dconf", "read", f"{_KEYBINDINGS_BASE}/custom0/command"],
        capture_output=True, text=True,
    )
    return "'ptyxis'" in r.stdout


def set_shortcut_1(page, _arg: Any) -> bool:
    base = f"{_KEYBINDINGS_BASE}/custom0"
    for args in [
        [f"{base}/name", "'Terminal 1'"],
        [f"{base}/command", "'ptyxis'"],
        [f"{base}/binding", "'<Primary><Alt>t'"],
    ]:
        subprocess.run(["dconf", "write"] + args, capture_output=True)
    _add_custom_keybinding(0)
    if page:
        page.log("\n✔  Ctrl+Alt+T назначен!\n")
    return True


def check_shortcut_2(_page: Any, _arg: Any) -> bool:
    r = subprocess.run(
        ["dconf", "read", f"{_KEYBINDINGS_BASE}/custom1/command"],
        capture_output=True, text=True,
    )
    return "'ptyxis'" in r.stdout


def set_shortcut_2(page, _arg: Any) -> bool:
    base = f"{_KEYBINDINGS_BASE}/custom1"
    for args in [
        [f"{base}/name", "'Terminal 2'"],
        [f"{base}/command", "'ptyxis'"],
        [f"{base}/binding", "'<Super>Return'"],
    ]:
        subprocess.run(["dconf", "write"] + args, capture_output=True)
    _add_custom_keybinding(1)
    if page:
        page.log("\n✔  Super+Enter назначен!\n")
    return True


def check_zsh_default(_page: Any, _arg: Any) -> bool:
    username = os.environ.get("USER", "")
    r = subprocess.run(["getent", "passwd", username], capture_output=True, text=True)
    return "/zsh" in r.stdout


def set_zsh_default(page, _arg: Any) -> bool:
    username = os.environ.get("USER", "")
    log_fn = page.log if page else lambda _: None
    ok = backend.run_privileged_sync(["chsh", "-s", "/bin/zsh", username], log_fn)
    if page:
        page.log("\n✔  ZSH shell по умолчанию!\n" if ok else "\n✘  Ошибка\n")
    return ok


def install_zplug(page, _arg: Any) -> bool:
    zplug_dir = os.path.expanduser("~/.zplug")
    if os.path.isdir(zplug_dir):
        if page:
            page.log("\nℹ  zplug уже установлен\n")
        return True
    r = subprocess.run(
        ["git", "clone", "https://github.com/zplug/zplug", zplug_dir],
        capture_output=True, text=True,
    )
    ok = r.returncode == 0
    if page:
        if r.stdout:
            page.log(r.stdout)
        if r.stderr:
            page.log(r.stderr)
        page.log("\n✔  zplug установлен!\n" if ok else "\n✘  Ошибка установки zplug\n")
    return ok


def install_fastfetch_config(page, _arg: Any) -> bool:
    cfg_dir = Path.home() / ".config" / "fastfetch"
    cfg_path = cfg_dir / "plafonfetch.jsonc"
    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(_FASTFETCH_CONFIG)
        if page:
            page.log(f"\n✔  Конфиг: {cfg_path}\n")
        return True
    except OSError as e:
        if page:
            page.log(f"\n✘  Ошибка: {e}\n")
        return False


def check_zsh_aliases(_page: Any, _arg: Any) -> bool:
    zshrc = Path.home() / ".zshrc"
    try:
        return _ZSH_MARKER in zshrc.read_text()
    except OSError:
        return False


def add_zsh_aliases(page, _arg: Any) -> bool:
    zshrc = Path.home() / ".zshrc"
    try:
        existing = zshrc.read_text() if zshrc.exists() else ""
        if _ZSH_MARKER in existing:
            if page:
                page.log("\nℹ  Алиасы уже добавлены\n")
            return True
        with open(zshrc, "a") as f:
            f.write(f"\n{_ZSH_MARKER}\n{_ZSH_ALIASES}")
        if page:
            page.log("\n✔  Алиасы добавлены в ~/.zshrc\n")
        return True
    except OSError as e:
        if page:
            page.log(f"\n✘  Ошибка: {e}\n")
        return False
