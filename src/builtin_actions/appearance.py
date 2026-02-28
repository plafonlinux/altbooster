"""Встроенные функции для вкладки «Внешний вид»."""

from __future__ import annotations

import subprocess
from typing import Any

import backend
import config


def apply_papirus_icons(page, _arg: Any) -> bool:
    scheme = backend.gsettings_get("org.gnome.desktop.interface", "color-scheme")
    theme = "Papirus-Dark" if "dark" in scheme.lower() else "Papirus"
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Тема иконок: {theme}\n")
    return ok


def apply_alt_workstation_theme(page, _arg: Any) -> bool:
    """Применяет тему иконок alt-workstation."""
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", "alt-workstation"])
    if page:
        page.log("\n✔  Тема ALT Workstation применена!\n" if ok else "\n✘  Ошибка применения темы\n")
    return ok


def apply_adwaita_theme(page, _arg: Any) -> bool:
    ok = (
        backend.run_gsettings(["set", "org.gnome.desktop.interface", "gtk-theme", "Adwaita"])
        and backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", "Adwaita"])
    )
    if page:
        page.log("\n✔  Adwaita применена!\n" if ok else "\n✘  Ошибка\n")
    return ok


def apply_folder_color(page, color: str) -> bool:
    ok_d = subprocess.run(
        ["papirus-folders", "-C", color, "--theme", "Papirus-Dark"],
        capture_output=True,
    ).returncode == 0
    ok_l = subprocess.run(
        ["papirus-folders", "-C", color, "--theme", "Papirus"],
        capture_output=True,
    ).returncode == 0
    ok = ok_d or ok_l
    if ok:
        config.state_set("folder_color", color)
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Цвет папок: {color}\n")
    return ok


def reset_folder_color(page, _arg: Any) -> bool:
    subprocess.run(["papirus-folders", "-D", "--theme", "Papirus-Dark"], capture_output=True)
    subprocess.run(["papirus-folders", "-D", "--theme", "Papirus"], capture_output=True)
    config.state_set("folder_color", None)
    if page:
        page.log("\n✔  Папки сброшены на стандартный цвет\n")
    return True
