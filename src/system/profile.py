"""
profile.py — сбор, сохранение и загрузка пресетов ALT Booster.

Пресет (.altbooster) — JSON-файл, описывающий:
  - список установленных приложений из каталога (apps)
  - UUID включённых расширений GNOME Shell (extensions)
  - dconf-настройки расширений (extensions_dconf) — конфиги Vitals, Blur и т.д.
  - копию state.json (state)
  - ключевые gsettings оформления (gsettings)
  - опционально изменённый apps.json (custom_apps)

Пресеты хранятся в ~/.config/altbooster/presets/*.altbooster.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path

import backend
import config

PROFILE_EXT = ".altbooster"


_USER_EXT_DIR   = Path.home() / ".local" / "share" / "gnome-shell" / "extensions"
_SYSTEM_EXT_DIR = Path("/usr/share/gnome-shell/extensions")


def _get_enabled_extensions() -> list[str]:
    """Возвращает UUID включённых расширений, установленных в пользовательской директории.

    Системные расширения (/usr/share/gnome-shell/extensions/) намеренно исключаются:
    они привязаны к ОС и не нуждаются в установке при восстановлении пресета.
    """
    try:
        r = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            capture_output=True, text=True, timeout=10,
        )
        uuids = [u.strip() for u in r.stdout.splitlines() if u.strip()]
        # Оставляем только пользовательские расширения
        return [u for u in uuids if (_USER_EXT_DIR / u).exists()]
    except Exception:
        return []


def _get_gsettings_snapshot() -> list[dict]:
    """Снимает ключевые gsettings оформления для включения в пресет."""
    keys = [
        ("org.gnome.desktop.interface", "icon-theme"),
        ("org.gnome.desktop.interface", "cursor-theme"),
    ]
    result = []
    for schema, key in keys:
        value = backend.gsettings_get(schema, key)
        if value and value != "''":
            result.append({"schema": schema, "key": key, "value": value})
    return result


def _get_installed_apps(apps_catalog: dict) -> list[dict]:
    """Проверяет каждое приложение из каталога — возвращает список установленных."""
    installed = []
    for group in apps_catalog.get("groups", []):
        for item in group.get("items", []):
            sources = item.get("sources") or (
                [item["source"]] if item.get("source") else []
            )
            for src in sources:
                if backend.check_app_installed(src):
                    installed.append({
                        "id": item["id"],
                        "label": item.get("label", item["id"]),
                        "source_label": src.get("label", ""),
                    })
                    break  # нашли — не проверяем другие источники
    return installed


def _safe_filename(name: str) -> str:
    """Преобразует произвольное имя в безопасное для файловой системы."""
    safe = re.sub(r'[^\w\s\-]', '_', name, flags=re.UNICODE).strip()
    return safe or "preset"


def collect_profile(name: str, apps_catalog: dict) -> dict:
    """Собирает текущее состояние системы в словарь пресета.

    Проверка установленных приложений может занять несколько секунд —
    вызывать из фонового потока.
    """
    # Проверяем, изменён ли пользовательский apps.json относительно системного
    user_apps_path = config.CONFIG_DIR / "apps.json"
    system_apps_path = Path(__file__).resolve().parent.parent / "modules" / "apps.json"
    custom_apps = None
    try:
        user_text = user_apps_path.read_text(encoding="utf-8")
        system_text = system_apps_path.read_text(encoding="utf-8")
        if user_text != system_text:
            custom_apps = json.loads(user_text)
    except Exception:
        pass

    # Снимаем dconf-настройки расширений — позволяет восстановить конфиги
    # Vitals, Blur my Shell и т.д. точно так же, как они были настроены
    extensions_dconf = ""
    try:
        r = subprocess.run(
            ["dconf", "dump", "/org/gnome/shell/extensions/"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            extensions_dconf = r.stdout
    except Exception:
        pass

    return {
        "format_version": 1,
        "name": name,
        "altbooster_version": config.VERSION,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "apps": _get_installed_apps(apps_catalog),
        "extensions": _get_enabled_extensions(),
        "extensions_dconf": extensions_dconf,
        "state": config.get_state_copy(),
        "gsettings": _get_gsettings_snapshot(),
        "custom_apps": custom_apps,
    }


def save_preset(data: dict, name: str) -> Path:
    """Сохраняет пресет в ~/.config/altbooster/presets/<name>.altbooster."""
    config.PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(name)
    path = config.PRESETS_DIR / f"{safe}{PROFILE_EXT}"
    out = dict(data)
    out["name"] = name
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_preset(path: Path) -> dict:
    """Загружает и минимально валидирует пресет из файла."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Неверный формат файла пресета: {path.name}")
    return data


def list_presets() -> list[tuple[str, Path]]:
    """Возвращает [(name, path), ...] всех пресетов из PRESETS_DIR, отсортированных по имени."""
    if not config.PRESETS_DIR.exists():
        return []
    result = []
    for p in sorted(config.PRESETS_DIR.glob(f"*{PROFILE_EXT}")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            name = data.get("name", p.stem)
        except Exception:
            name = p.stem
        result.append((name, p))
    return result


def find_import_candidates() -> list[Path]:
    """Ищет *.altbooster файлы в ~/Downloads и ~/ для предложения импорта."""
    candidates = []
    for search_dir in [Path.home() / "Downloads", Path.home()]:
        if search_dir.exists():
            for p in sorted(search_dir.glob(f"*{PROFILE_EXT}")):
                if p not in candidates:
                    candidates.append(p)
    return candidates


# Маппинг: префикс имени темы → имя пакета ALT Linux
_THEME_PACKAGES: dict[str, str] = {
    "Papirus": "papirus-remix-icon-theme",
}


def theme_package(theme_name: str) -> str | None:
    """Возвращает имя пакета для темы иконок по её имени, или None если неизвестно."""
    for prefix, pkg in _THEME_PACKAGES.items():
        if theme_name.startswith(prefix):
            return pkg
    return None


def theme_exists(name: str) -> bool:
    """Проверяет наличие директории темы иконок/курсора на диске."""
    for base in [Path.home() / ".local" / "share" / "icons", Path("/usr/share/icons")]:
        if (base / name).is_dir():
            return True
    return False


def apply_settings(data: dict) -> list[dict]:
    """Применяет gsettings и state из пресета немедленно (синхронно).

    Устанавливает только настройки — без установки приложений и расширений.
    Вызывается из GTK-потока (операции быстрые: дисковые и gsettings).

    Возвращает список gsettings-записей, которые не были применены,
    потому что тема иконок/курсора ещё не установлена — их нужно применить
    после установки соответствующего пакета.
    """
    deferred: list[dict] = []

    # GSettings
    for entry in data.get("gsettings", []):
        try:
            key = entry.get("key", "")
            value = entry.get("value", "")
            # Темы иконок/курсора применяем только если директория уже есть на диске.
            # Если нет — откладываем: вызывающий код установит пакет и применит потом.
            if key in ("icon-theme", "cursor-theme") and not theme_exists(value):
                deferred.append(entry)
                continue
            backend.run_gsettings(["set", entry["schema"], key, value])
        except Exception:
            pass

    return deferred
