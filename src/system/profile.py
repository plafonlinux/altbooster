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
    try:
        r = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            capture_output=True, text=True, timeout=10,
        )
        uuids = [u.strip() for u in r.stdout.splitlines() if u.strip()]
        return [u for u in uuids if (_USER_EXT_DIR / u).exists()]
    except Exception:
        return []


def _get_gsettings_snapshot() -> list[dict]:
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
                    break
    return installed


def _safe_filename(name: str) -> str:
    safe = re.sub(r'[^\w\s\-]', '_', name, flags=re.UNICODE).strip()
    return safe or "preset"


def collect_profile(name: str, apps_catalog: dict) -> dict:
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
    config.PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(name)
    path = config.PRESETS_DIR / f"{safe}{PROFILE_EXT}"
    out = dict(data)
    out["name"] = name
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_preset(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Неверный формат файла пресета: {path.name}")
    return data


def list_presets() -> list[tuple[str, Path]]:
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
    candidates = []
    for search_dir in [Path.home() / "Downloads", Path.home()]:
        if search_dir.exists():
            for p in sorted(search_dir.glob(f"*{PROFILE_EXT}")):
                if p not in candidates:
                    candidates.append(p)
    return candidates


_THEME_PACKAGES: dict[str, str] = {
    "Papirus": "papirus-remix-icon-theme",
}


def theme_package(theme_name: str) -> str | None:
    for prefix, pkg in _THEME_PACKAGES.items():
        if theme_name.startswith(prefix):
            return pkg
    return None


def theme_exists(name: str) -> bool:
    for base in [Path.home() / ".local" / "share" / "icons", Path("/usr/share/icons")]:
        if (base / name).is_dir():
            return True
    return False


def apply_settings(data: dict) -> list[dict]:
    deferred: list[dict] = []

    for entry in data.get("gsettings", []):
        try:
            key = entry.get("key", "")
            value = entry.get("value", "")
            if key in ("icon-theme", "cursor-theme") and not theme_exists(value):
                deferred.append(entry)
                continue
            backend.run_gsettings(["set", entry["schema"], key, value])
        except Exception:
            pass

    return deferred
