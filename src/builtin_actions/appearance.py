"""Встроенные функции для вкладки «Внешний вид»."""

from __future__ import annotations

from typing import Any

import backend
import config

# Соответствие: lowercase-имя цвета → суффикс темы Papirus-Remix
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
    """Возвращает базовое имя темы Papirus-Remix по текущей цветовой схеме."""
    scheme = backend.gsettings_get("org.gnome.desktop.interface", "color-scheme")
    return "Papirus-Dark" if "dark" in scheme.lower() else "Papirus-Light"


def apply_papirus_icons(page, _arg: Any) -> bool:
    """Применяет тему Papirus-Remix (Adwaita-цвет папок) по текущей схеме."""
    theme = f"{_remix_base()}-Adwaita"
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
    """Переключает icon-theme на Papirus-Remix-вариант с нужным цветом папок.

    Пакет papirus-remix-icon-theme устанавливает готовые темы вида
    Papirus-Dark-DeepOrange / Papirus-Light-DeepOrange — по одной на каждый цвет.
    Вместо модификации базовой темы через papirus-folders мы просто переключаем
    gsettings icon-theme на нужный вариант.  Root-права не нужны.
    """
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
    """Сбрасывает цвет папок на Adwaita (возвращает базовый Papirus-Remix-вариант)."""
    theme = f"{_remix_base()}-Adwaita"
    ok = backend.run_gsettings(["set", "org.gnome.desktop.interface", "icon-theme", theme])
    config.state_set("folder_color", None)
    if page:
        page.log(f"\n{'✔' if ok else '✘'}  Сброс цвета: {theme}\n")
    return ok
