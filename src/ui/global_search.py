from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from core import config
from ui.common import _MODULES_DIR, load_module

OnSearchPick = Callable[[str, str | None], None]


@dataclass(frozen=True)
class GlobalSearchItem:
    """Пункт глобального поиска: вкладка и опционально фокус на строке."""

    tab_id: str
    title: str
    icon_name: str
    subtitle: str = ""
    keywords: tuple[str, ...] = ()
    # None — только переключить вкладку; иначе см. разбор в AltBoosterWindow._apply_search_focus
    focus_spec: str | None = None


# Дополнительные строки для подстрочного поиска (латиница + кириллица).
_TAB_KEYWORDS: dict[str, tuple[str, ...]] = {
    "setup": ("начало", "главная", "домой", "setup"),
    "apps": ("приложения", "установить", "каталог", "epm", "flatpak", "репозиторий"),
    "extensions": ("расширения", "gnome", "shell", "дополнения"),
    "flatpak": (
        "flatpak",
        "флатпак",
        "flathub",
        "флатхаб",
        "репозиторий",
        "repository",
        "зеркало",
        "mirror",
        "приложения flatpak",
    ),
    "terminal": ("терминал", "shell", "bash"),
    "amd": ("amd", "radeon", "видеокарта", "gpu"),
    "davinci": ("davinci", "resolve", "видеомонтаж"),
    "maintenance": ("обслуживание", "система", "nfs", "диск"),
    "tweaks": (
        "твики",
        "твик",
        "фиксы",
        "настройки системы",
        "ananicy",
        "ananicy-cpp",
        "scx",
        "scx-scheds",
        "lavd",
        "sched-ext",
        "планировщик cpu",
    ),
    "borg": (
        "timesync",
        "time sync",
        "резерв",
        "бэкап",
        "backup",
        "borg",
        "архив",
        "снимок",
        "снэпшот",
        "snapshot",
        "btrfs",
    ),
}


def tab_items_from_specs(
    main_tabs: Sequence[tuple],
    borg_tab: tuple,
) -> list[GlobalSearchItem]:
    """
    main_tabs: (name, title, icon_name, PageClass), …
    borg_tab: (name, title, icon_name, PageClass)
    """
    items: list[GlobalSearchItem] = []
    for name, title, icon_name, _ in main_tabs:
        kw = _TAB_KEYWORDS.get(name, ())
        items.append(
            GlobalSearchItem(
                tab_id=name,
                title=title,
                icon_name=icon_name,
                subtitle="Вкладка",
                keywords=kw,
                focus_spec=None,
            )
        )
    bn, bt, bi, _ = borg_tab
    items.append(
        GlobalSearchItem(
            tab_id=bn,
            title=bt,
            icon_name=bi,
            subtitle="Вкладка",
            keywords=_TAB_KEYWORDS.get("borg", ()),
            focus_spec=None,
        )
    )
    return items


def _setup_detail_items() -> list[GlobalSearchItem]:
    """Соответствует подпунктам вкладки «Начало» (tabs/setup.py)."""
    base_kw = ("начало", "настройка", "система", "alt")
    specs: list[tuple[str, str, str, str, tuple[str, ...]]] = [
        ("epm_install", "application-x-addon-symbolic", "Установить EPM", "Начало · Обновление и пакеты",
         ("eepm", "пакетный менеджер", "epm")),
        ("epm_update", "software-update-available-symbolic", "Обновить систему (EPM)", "Начало · Обновление и пакеты",
         ("обновление", "full-upgrade", "apt")),
        ("sudo", "security-high-symbolic", "Включить sudo", "Начало · Система",
         ("sudowheel", "pkexec", "control", "права", "wheel")),
        ("gnome_sw", "view-refresh-symbolic", "Автообновление GNOME Software", "Начало · Система",
         ("центр приложений", "фоновая загрузка", "gnome software")),
        ("trim", "media-flash-symbolic", "Автоматический TRIM", "Начало · Система",
         ("ssd", "fstrim", "диск")),
        ("journal", "document-open-recent-symbolic", "Лимиты журналов", "Начало · Система",
         ("journald", "журнал", "systemd")),
        ("scale", "video-display-symbolic", "Дробное масштабирование", "Начало · Система",
         ("fractional", "монитор", "4k")),
        ("papirus", "application-x-addon-symbolic", "Иконки Papirus", "Начало · Файловый менеджер Nautilus и иконки",
         ("papirus", "тема", "иконки")),
        ("nautilus", "system-file-manager-symbolic", "Настройки Nautilus", "Начало · Файловый менеджер Nautilus и иконки",
         ("файлы", "папки", "сортировка")),
        ("vm_dirty", "drive-harddisk-symbolic", "Индикатор копирования", "Начало · Файловый менеджер Nautilus и иконки",
         ("копирование", "прогресс", "vm.dirty", "nautilus")),
        ("nautilus_admin", "security-high-symbolic", "Запуск от администратора", "Начало · Файловый менеджер Nautilus и иконки",
         ("root", "администратор", "nautilus-admin")),
        ("sushi", "view-reveal-symbolic", "Предпросмотр (Sushi)", "Начало · Файловый менеджер Nautilus и иконки",
         ("пробел", "preview", "быстрый просмотр")),
        ("f3d", "image-x-generic-symbolic", "3D превью (f3d)", "Начало · Файловый менеджер Nautilus и иконки",
         ("модель", "3d", "sisyphus")),
        ("kbd_altshift", "input-keyboard-symbolic", "Alt + Shift", "Начало · Раскладка клавиатуры",
         ("раскладка", "переключение", "клавиатура")),
        ("kbd_caps", "input-keyboard-symbolic", "CapsLock", "Начало · Раскладка клавиатуры",
         ("раскладка", "caps", "клавиатура")),
        ("kbd_ctrlshift", "input-keyboard-symbolic", "Ctrl + Shift", "Начало · Раскладка клавиатуры",
         ("раскладка", "клавиатура")),
    ]
    out: list[GlobalSearchItem] = []
    for key, icon, title, subtitle, extra_kw in specs:
        out.append(
            GlobalSearchItem(
                tab_id="setup",
                title=title,
                icon_name=icon,
                subtitle=subtitle,
                keywords=base_kw + extra_kw,
                focus_spec=f"setup:{key}",
            )
        )
    return out


def _dynamic_module_items(module_file: str, tab_id: str, tab_title: str) -> list[GlobalSearchItem]:
    data = load_module(module_file)
    if not data or "groups" not in data:
        return []
    tab_kw = _TAB_KEYWORDS.get(tab_id, ())
    items: list[GlobalSearchItem] = []
    for group in data["groups"]:
        gtitle = group.get("title", "")
        for row in group.get("rows", []):
            rid = row.get("id")
            title = row.get("title", "")
            if not rid or not title:
                continue
            subtitle = row.get("subtitle", "")
            icon = row.get("icon", "preferences-system-symbolic")
            sec = f"{tab_title} · {gtitle}" if gtitle else tab_title
            kw = (subtitle, str(rid).replace("_", " ")) + tab_kw
            items.append(
                GlobalSearchItem(
                    tab_id=tab_id,
                    title=title,
                    icon_name=icon,
                    subtitle=sec,
                    keywords=kw,
                    focus_spec=f"d:{rid}",
                )
            )
    return items


def _load_apps_json_for_search() -> dict:
    user_path = config.CONFIG_DIR / "apps.json"
    system_path = _MODULES_DIR / "apps.json"
    path = user_path if user_path.exists() else system_path
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _apps_catalog_items() -> list[GlobalSearchItem]:
    """Пункты из списка приложений (apps.json), включая пользовательскую группу."""
    data = _load_apps_json_for_search()
    tab_kw = _TAB_KEYWORDS.get("apps", ())
    USER_GID = "user_apps"
    items: list[GlobalSearchItem] = []
    for g in data.get("groups", []):
        gid = g.get("id", "")
        gtitle = g.get("title", "")
        if gid == USER_GID:
            sec = "Приложения · пользовательский список"
            extra = ("избранное", "избранные", "мой список", "пользователь")
        else:
            sec = f"Приложения · {gtitle}" if gtitle else "Приложения"
            extra = ()
        for app in g.get("items", []):
            aid = app.get("id")
            label = app.get("label", "")
            if not aid or not label:
                continue
            desc = app.get("desc", "")
            kw = (desc, aid, aid.replace("_", " ")) + tab_kw + extra
            items.append(
                GlobalSearchItem(
                    tab_id="apps",
                    title=label,
                    icon_name="application-x-executable-symbolic",
                    subtitle=sec,
                    keywords=kw,
                    focus_spec=f"app:{aid}",
                )
            )
    return items


def _extension_catalog_items() -> list[GlobalSearchItem]:
    """Рекомендуемые и установленные расширения GNOME Shell (uuid, название, описание)."""
    from tabs.extensions import RECOMMENDED, _read_extensions_from, _SYSTEM_EXT_DIR, _USER_EXT_DIR

    tab_kw = _TAB_KEYWORDS.get("extensions", ())
    merged: dict[str, dict] = {}

    def _parts(m: dict) -> list[str]:
        return m.setdefault("_kw", [])

    for r in RECOMMENDED:
        uuid, name, desc = r[0], r[1], r[2]
        merged[uuid] = {"title": name, "_kw": [desc, uuid, name, "рекомендуемое"]}

    for uuid, name, desc in _read_extensions_from(_USER_EXT_DIR):
        if uuid not in merged:
            merged[uuid] = {"title": name, "_kw": [desc, uuid, name, "установленные"]}
        else:
            m = merged[uuid]
            m["title"] = name
            _parts(m).extend([desc, name, "установленные"])

    for uuid, name, desc in _read_extensions_from(_SYSTEM_EXT_DIR):
        if uuid not in merged:
            merged[uuid] = {"title": name, "_kw": [desc, uuid, name, "системные"]}
        else:
            m = merged[uuid]
            _parts(m).extend([desc, name, "системные"])

    items: list[GlobalSearchItem] = []
    for uuid, m in merged.items():
        title = m.get("title") or uuid
        parts = [p for p in _parts(m) if p]
        kw = tuple(parts) + tab_kw
        items.append(
            GlobalSearchItem(
                tab_id="extensions",
                title=title,
                icon_name="application-x-addon-symbolic",
                subtitle="Расширения · GNOME Shell",
                keywords=kw,
                focus_spec=f"ext:{uuid}",
            )
        )
    return items


def _maintenance_task_items() -> list[GlobalSearchItem]:
    data = load_module("maintenance")
    tasks = data.get("tasks", []) if data else []
    tab_kw = _TAB_KEYWORDS.get("maintenance", ())
    items: list[GlobalSearchItem] = []
    for t in tasks:
        tid = t.get("id")
        label = t.get("label", "")
        if not tid or not label:
            continue
        desc = t.get("desc", "")
        icon = t.get("icon", "preferences-system-symbolic")
        items.append(
            GlobalSearchItem(
                tab_id="maintenance",
                title=label,
                icon_name=icon,
                subtitle="Обслуживание · Задачи",
                keywords=(desc, tid, "задача") + tab_kw,
                focus_spec=f"m:{tid}",
            )
        )
    items.append(
        GlobalSearchItem(
            tab_id="maintenance",
            title="Очистка кэша",
            icon_name="user-trash-symbolic",
            subtitle="Обслуживание",
            keywords=("кэш", "thumbnails", "корзина", "flatpak") + tab_kw,
            focus_spec="m:cache",
        )
    )
    items.append(
        GlobalSearchItem(
            tab_id="maintenance",
            title="Добавить точки монтирования",
            icon_name="drive-harddisk-symbolic",
            subtitle="Обслуживание · Монтирование",
            keywords=("fstab", "nfs", "cifs", "монтирование", "сеть") + tab_kw,
            focus_spec="m:fstab",
        )
    )
    return items


def _tweaks_section_items() -> list[GlobalSearchItem]:
    """Секции вкладки «Твики» — не из JSON-модуля."""
    tab_kw = _TAB_KEYWORDS.get("tweaks", ())
    _tw_k = "Твики · Планировщик ядра"
    _tw_p = "Твики · Приоритеты процессов"
    return [
        GlobalSearchItem(
            tab_id="tweaks",
            title="Поддержка sched_ext в ядре",
            icon_name="cpu-symbolic",
            subtitle=_tw_k,
            keywords=(
                "sched_ext",
                "sched ext",
                "sched-ext",
                "scx",
                "ядро",
                "kernel",
                "kernel-image",
                "CONFIG_SCHED_CLASS_EXT",
                "lavd",
                "meteor",
            )
            + tab_kw,
            focus_spec="d:sched_ext",
        ),
        GlobalSearchItem(
            tab_id="tweaks",
            title="Планировщик AMD Ryzen (SCX LAVD)",
            icon_name="cpu-symbolic",
            subtitle=_tw_k,
            keywords=(
                "scx",
                "scx-scheds",
                "scx_lavd",
                "lavd",
                "sched-ext",
                "sched_ext",
                "планировщик",
                "планировщик cpu",
                "cpu",
                "игры",
                "игровой",
                "valve",
                "igalia",
                "ryzen",
                "amd ryzen",
            )
            + tab_kw,
            focus_spec="d:scx",
        ),
        GlobalSearchItem(
            tab_id="tweaks",
            title="Современный планировщик (ananicy-cpp, от CachyOS)",
            icon_name="system-run-symbolic",
            subtitle=_tw_p,
            keywords=(
                "ananicy",
                "ananicy-cpp",
                "ananicy cpp",
                "приоритет",
                "приоритеты",
                "процесс",
                "процессы",
                "nice",
                "cachyos",
                "cachy",
                "правила",
                "современный планировщик",
                "планировщик linux",
                "приоритеты процессов",
            )
            + tab_kw,
            focus_spec="d:ananicy",
        ),
        GlobalSearchItem(
            tab_id="tweaks",
            title="System76 Scheduler (Pop!_OS)",
            icon_name="computer-symbolic",
            subtitle=_tw_p,
            keywords=(
                "system76",
                "system76-scheduler",
                "pop os",
                "popos",
                "pop!_os",
                "scheduler",
                "планировщик",
                "cfs",
                "nice",
                "pipewire",
            )
            + tab_kw,
            focus_spec="d:system76_scheduler",
        ),
        GlobalSearchItem(
            tab_id="tweaks",
            title="Планировщик Intel (SCX Meteor)",
            icon_name="processor-symbolic",
            subtitle=_tw_k,
            keywords=(
                "scx_meteor",
                "scx meteor",
                "meteor",
                "intel",
                "sched_ext",
                "lp-first",
                "toxblh",
                "гибрид",
                "процессор intel",
                "планировщик intel",
            )
            + tab_kw,
            focus_spec="d:intel_scx_meteor",
        ),
    ]


def _flatpak_section_items() -> list[GlobalSearchItem]:
    """Статические строки вкладки Flatpak (зеркало, подключение Flathub)."""
    tab_kw = _TAB_KEYWORDS.get("flatpak", ())
    return [
        GlobalSearchItem(
            tab_id="flatpak",
            title="Зеркало Flathub",
            icon_name="network-server-symbolic",
            subtitle="Flatpak · репозиторий",
            keywords=(
                "flathub",
                "флатхаб",
                "источник",
                "загрузка",
                "dl.flathub.org",
                "ustc",
                "sjtu",
                "sel.flathub",
            )
            + tab_kw,
            focus_spec="fp:mirror",
        ),
        GlobalSearchItem(
            tab_id="flatpak",
            title="Подключить Flathub",
            icon_name="application-x-addon-symbolic",
            subtitle="Flatpak",
            keywords=(
                "flathub",
                "флатхаб",
                "flatpak",
                "установить",
                "репозиторий",
                "flatpak-repo-flathub",
            )
            + tab_kw,
            focus_spec="fp:connect",
        ),
    ]


def _flatpak_installed_app_items() -> list[GlobalSearchItem]:
    """Установленные Flatpak-приложения (по id/имени, в т.ч. org.flathub.*)."""
    from tabs.flatpak import _list_flatpak_apps

    tab_kw = _TAB_KEYWORDS.get("flatpak", ())
    items: list[GlobalSearchItem] = []
    try:
        apps = _list_flatpak_apps()
    except Exception:
        apps = []
    for app in apps:
        aid = app.app_id.strip()
        name = app.name.strip() or aid
        ver = (app.version or "").strip()
        kw = (aid, name, ver, aid.replace(".", " "))
        items.append(
            GlobalSearchItem(
                tab_id="flatpak",
                title=name,
                icon_name="package-x-generic-symbolic",
                subtitle=f"Flatpak · {aid}",
                keywords=kw + tab_kw,
                focus_spec=f"fp:app:{aid}",
            )
        )
    return items


def build_all_search_items(
    main_tabs: Sequence[tuple],
    borg_tab: tuple,
) -> list[GlobalSearchItem]:
    items: list[GlobalSearchItem] = []
    items.extend(tab_items_from_specs(main_tabs, borg_tab))
    items.extend(_setup_detail_items())
    items.extend(_dynamic_module_items("terminal", "terminal", "Терминал"))
    items.extend(_dynamic_module_items("amd", "amd", "AMD Radeon"))
    items.extend(_maintenance_task_items())
    items.extend(_apps_catalog_items())
    items.extend(_extension_catalog_items())
    items.extend(_flatpak_section_items())
    items.extend(_flatpak_installed_app_items())
    items.extend(_tweaks_section_items())
    return items


def _normalize_blob(item: GlobalSearchItem) -> str:
    parts = [item.title, item.subtitle, *item.keywords]
    return " ".join(parts).lower()


def filter_items(items: Sequence[GlobalSearchItem], query: str) -> list[GlobalSearchItem]:
    q = query.strip().lower()
    if not q:
        return []
    fragments = [f for f in q.split() if f]
    if not fragments:
        return []
    out: list[GlobalSearchItem] = []
    for it in items:
        blob = _normalize_blob(it)
        if all(fr in blob for fr in fragments):
            out.append(it)
    return out


_KRUNNER_CSS = b"""
.ab-krunner-root {
  background-color: transparent;
}
.ab-krunner-floating-card {
  background-color: @card_bg_color;
  color: @card_fg_color;
  opacity: 1;
  border-radius: 16px;
  border: 1px solid alpha(@borders, 0.85);
  box-shadow: 0 8px 28px alpha(black, 0.22);
}
.ab-krunner-panel {
  padding: 18px 20px 10px 20px;
}
.ab-krunner-entry {
  font-size: 15pt;
  font-weight: 400;
  min-height: 52px;
  padding: 10px 14px;
  border-radius: 12px;
}
.ab-krunner-scroll {
  margin-top: 10px;
}
.ab-krunner-scroll list {
  background-color: @card_bg_color;
}
/* Non-transparent dimming: a fully clear backdrop lets widgets below show through and causes ghosting/double icons */
.ab-krunner-backdrop {
  background-color: alpha(black, 0.62);
}
"""

_krunner_css_installed = False


def _ensure_krunner_css() -> None:
    global _krunner_css_installed
    if _krunner_css_installed:
        return
    css = Gtk.CssProvider()
    css.load_from_data(_KRUNNER_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _krunner_css_installed = True


class GlobalSearchPanel(Gtk.Overlay):
    """Полноэкранный слой поверх стека вкладок: карточка по центру, клик по полю вокруг закрывает."""

    def __init__(self):
        super().__init__()
        self._items: list[GlobalSearchItem] = []
        self._on_pick: OnSearchPick = lambda *_: None
        self._filtered: list[GlobalSearchItem] = []

        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.add_css_class("ab-krunner-root")

        _ensure_krunner_css()

        self._entry = Gtk.SearchEntry()
        self._entry.set_placeholder_text("Искать")
        self._entry.add_css_class("ab-krunner-entry")
        self._entry.set_hexpand(True)
        self._entry.connect("notify::text", self._on_entry_text_changed)
        self._entry.connect("activate", self._on_entry_activate)
        entry_keys = Gtk.EventControllerKey()
        entry_keys.connect("key-pressed", self._on_entry_key_pressed)
        self._entry.add_controller(entry_keys)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("boxed-list")
        self._list.connect("row-activated", self._on_row_activated)

        self._result_scroll = Gtk.ScrolledWindow()
        self._result_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._result_scroll.set_vexpand(True)
        self._result_scroll.set_max_content_height(320)
        self._result_scroll.add_css_class("ab-krunner-scroll")
        self._result_scroll.set_child(self._list)
        self._result_scroll.set_visible(False)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.add_css_class("ab-krunner-panel")
        inner.append(self._entry)
        inner.append(self._result_scroll)

        self._floating_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._floating_card.add_css_class("ab-krunner-floating-card")
        self._floating_card.set_size_request(520, -1)
        self._floating_card.set_halign(Gtk.Align.CENTER)
        self._floating_card.set_valign(Gtk.Align.CENTER)
        self._floating_card.append(inner)

        backdrop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        backdrop.add_css_class("ab-krunner-backdrop")
        backdrop.set_hexpand(True)
        backdrop.set_vexpand(True)
        bd_gesture = Gtk.GestureClick()
        bd_gesture.connect("pressed", self._on_backdrop_pressed)
        backdrop.add_controller(bd_gesture)

        self.set_child(backdrop)
        self.add_overlay(self._floating_card)
        self.set_measure_overlay(self._floating_card, False)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

        list_keys = Gtk.EventControllerKey()
        list_keys.connect("key-pressed", self._on_key_pressed)
        self._list.add_controller(list_keys)

        self._rebuild_rows()
        self.set_visible(False)

    def open(self, items: Sequence[GlobalSearchItem], on_pick: OnSearchPick) -> None:
        self._items = list(items)
        self._on_pick = on_pick
        self._entry.set_text("")
        self._filtered = []
        self._rebuild_rows()
        self.set_visible(True)
        GLib.idle_add(self._entry.grab_focus)

    def update_items(self, items: Sequence[GlobalSearchItem]) -> None:
        self._items = list(items)
        if self._entry.get_text().strip():
            self._filtered = filter_items(self._items, self._entry.get_text())
            self._rebuild_rows()

    def dismiss(self) -> None:
        self.set_visible(False)

    def _on_backdrop_pressed(self, _gesture, _n_press: int, _x: float, _y: float) -> None:
        self.dismiss()

    def _on_entry_key_pressed(self, _ctrl, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def _on_key_pressed(self, _ctrl, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def _on_entry_text_changed(self, *_args):
        self._filtered = filter_items(self._items, self._entry.get_text())
        self._rebuild_rows()

    def _on_entry_activate(self, _entry):
        if not _entry.get_text().strip():
            self.dismiss()
            return
        row = self._list.get_selected_row()
        if row is None:
            first = self._list.get_row_at_index(0)
            if first is not None:
                self._list.select_row(first)
                row = first
        if row is not None:
            idx = row.get_index()
            if 0 <= idx < len(self._filtered):
                self._activate_item(self._filtered[idx])

    def _on_row_activated(self, _list, row: Gtk.ListBoxRow):
        idx = row.get_index()
        if 0 <= idx < len(self._filtered):
            self._activate_item(self._filtered[idx])

    def _activate_item(self, item: GlobalSearchItem):
        self._on_pick(item.tab_id, item.focus_spec)
        self.dismiss()

    def _rebuild_rows(self):
        while True:
            row = self._list.get_row_at_index(0)
            if row is None:
                break
            self._list.remove(row)

        query = self._entry.get_text().strip()

        if not query:
            self._result_scroll.set_visible(False)
            return

        self._result_scroll.set_visible(True)

        if not self._filtered:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            lbl = Gtk.Label(label="Ничего не найдено")
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(20)
            lbl.set_margin_bottom(20)
            lbl.set_margin_start(8)
            row.set_child(lbl)
            self._list.append(row)
            return

        for it in self._filtered:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            hbox.set_margin_top(10)
            hbox.set_margin_bottom(10)
            hbox.set_margin_start(12)
            hbox.set_margin_end(12)

            img = Gtk.Image.new_from_icon_name(it.icon_name)
            img.set_pixel_size(24)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vbox.set_hexpand(True)
            t = Gtk.Label(label=it.title)
            t.add_css_class("heading")
            t.set_halign(Gtk.Align.START)
            t.set_ellipsize(Pango.EllipsizeMode.END)
            vbox.append(t)
            if it.subtitle:
                s = Gtk.Label(label=it.subtitle)
                s.add_css_class("dim-label")
                s.set_halign(Gtk.Align.START)
                s.set_ellipsize(Pango.EllipsizeMode.END)
                vbox.append(s)

            hbox.append(img)
            hbox.append(vbox)
            row.set_child(hbox)
            self._list.append(row)

        first = self._list.get_row_at_index(0)
        if first is not None and first.get_selectable():
            self._list.select_row(first)

