
from __future__ import annotations

from typing import Any

import backend
import config

_COLOR_SUFFIX: dict[str, str] = {
    "adwaita":    "Adwaita",
    "black":      "Black",
    "blue":       "Blue",
    "breeze":     "Breeze",
    "brown":      "Brown",
    "carmine":    "Carmine",
    "cyan":       "Cyan",
    "darkcyan":   "DarkCyan",
    "green":      "Green",
    "grey":       "Grey",
    "indigo":     "Indigo",
    "magenta":    "Magenta",
    "nordic":     "Nordic",
    "orange":     "Orange",
    "paleorange": "PaleOrange",
    "pink":       "Pink",
    "red":        "Red",
    "teal":       "Teal",
    "violet":     "Violet",
    "white":      "White",
    "yellow":     "Yellow",
    "yaru":       "Yaru",
}


def _remix_base() -> str:
    scheme = backend.gsettings_get("org.gnome.desktop.interface", "color-scheme")
    return "Papirus-Dark" if "dark" in scheme.lower() else "Papirus-Light"


def apply_papirus_icons(page, _arg: Any) -> bool:
    theme = f"{_remix_base()}-Adwaita"
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Тема иконок: {theme}\n")
    return ok


def apply_alt_workstation_theme(page, _arg: Any) -> bool:
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
    suffix = _COLOR_SUFFIX.get(color)
    if not suffix:
        if page:
            page.log(f"\n✘  Неизвестный цвет: {color}\n")
        return False

    theme = f"{_remix_base()}-{suffix}"
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
    if ok:
        config.state_set("folder_color", color)
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Тема иконок: {theme}\n")
    return ok


def reset_folder_color(page, _arg: Any) -> bool:
    theme = f"{_remix_base()}-Adwaita"
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
    config.state_set("folder_color", None)
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Сброс цвета: {theme}\n")
    return ok

