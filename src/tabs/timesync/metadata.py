from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib


def find_gvfs_google_drive() -> str | None:
    gvfs_base = Path(f"/run/user/{os.getuid()}/gvfs")
    if gvfs_base.exists():
        for entry in gvfs_base.iterdir():
            if "google-drive" in entry.name and entry.is_dir():
                return str(entry)
    try:
        r = subprocess.run(
            ["gio", "mount", "-l"],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
        for line in r.stdout.splitlines():
            if "google-drive" in line.lower():
                gvfs_base2 = Path(f"/run/user/{os.getuid()}/gvfs")
                for entry in gvfs_base2.iterdir():
                    if "google-drive" in entry.name:
                        return str(entry)
    except Exception:
        pass
    return None


def flatpak_apps_from_booster_list() -> list[tuple[str, str]]:
    paths_to_try = [
        Path.home() / ".config" / "altbooster" / "apps.json",
        Path(__file__).resolve().parent.parent / "modules" / "apps.json",
    ]
    for path in paths_to_try:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = []
        seen = set()
        for group in data.get("groups", []):
            for item in group.get("items", []):
                sources = item.get("sources", [])
                single = item.get("source")
                if single:
                    sources = [single] + sources
                for src in sources:
                    check = src.get("check", [])
                    if len(check) >= 2 and check[0] == "flatpak":
                        app_id = check[1]
                        if app_id not in seen:
                            seen.add(app_id)
                            result.append((item.get("label", app_id), app_id))
                        break
        return result
    return []


def generate_flatpak_meta(target_dir: Path, source_mode: int | None = 0) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        if source_mode is not None:
            r1 = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application"],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            installed_ids = {l.strip() for l in r1.stdout.splitlines() if l.strip()}
            if source_mode == 1:
                booster_ids = [app_id for _, app_id in flatpak_apps_from_booster_list()]
                all_ids = [app_id for app_id in booster_ids if app_id in installed_ids]
            else:
                all_ids = list(installed_ids)
            (target_dir / "flatpak-apps.txt").write_text(
                "\n".join(all_ids) + ("\n" if all_ids else ""), encoding="utf-8"
            )
        r2 = subprocess.run(
            ["flatpak", "remotes", "--columns=name,url"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        (target_dir / "flatpak-remotes.txt").write_text(r2.stdout, encoding="utf-8")
        return True
    except Exception:
        return False


def generate_extensions_meta(target_dir: Path) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        enabled = [u.strip() for u in r.stdout.splitlines() if u.strip()]
        ext_data: dict = {"extensions": enabled}
        r_dconf = subprocess.run(
            ["dconf", "dump", "/org/gnome/shell/extensions/"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        if r_dconf.returncode == 0:
            ext_data["extensions_dconf"] = r_dconf.stdout
        (target_dir / "extensions.json").write_text(
            json.dumps(ext_data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return True
    except Exception:
        return False


def generate_system_meta(target_dir: Path) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        r1 = subprocess.run(
            ["rpm", "-qa", "--queryformat", "%{NAME}\n"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if r1.returncode == 0:
            names = sorted(set(r1.stdout.splitlines()))
            (target_dir / "packages.txt").write_text("\n".join(names), encoding="utf-8")

        r2 = subprocess.run(
            ["dconf", "dump", "/"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        if r2.returncode == 0:
            (target_dir / "dconf-full.ini").write_text(r2.stdout, encoding="utf-8")
        return True
    except Exception:
        return False


def restore_packages_meta(meta_dir: Path, on_line, on_done) -> None:
    pkg_file = meta_dir / "packages.txt"
    if not pkg_file.exists():
        GLib.idle_add(on_done, False)
        return
    packages = pkg_file.read_text(encoding="utf-8").splitlines()
    packages = [p.strip() for p in packages if p.strip()]
    if not packages:
        GLib.idle_add(on_done, True)
        return

    from core import privileges
    GLib.idle_add(on_line, "▶  Переустановка RPM-пакетов...\n")
    privileges.run_privileged(["epm", "install", "-y", *packages], on_line, on_done)


def restore_flatpak_meta(meta_dir: Path, on_line, on_done) -> None:
    def _worker():
        ok = True
        remotes_file = meta_dir / "flatpak-remotes.txt"
        if remotes_file.exists():
            GLib.idle_add(on_line, "▶  Восстановление репозиториев Flatpak...\n")
            for line in remotes_file.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                name, url = parts[0].strip(), parts[1].strip()
                if not name or not url:
                    continue
                r = subprocess.run(
                    ["flatpak", "remote-add", "--user", "--if-not-exists", name, url],
                    capture_output=True, text=True, encoding="utf-8", timeout=30,
                )
                GLib.idle_add(on_line, f"   {'✔' if r.returncode == 0 else '⚠'} {name}\n")
        apps_file = meta_dir / "flatpak-apps.txt"
        if apps_file.exists():
            GLib.idle_add(on_line, "▶  Переустановка Flatpak-приложений...\n")
            for app_id in apps_file.read_text(encoding="utf-8").splitlines():
                app_id = app_id.strip()
                if not app_id:
                    continue
                r = subprocess.run(
                    ["flatpak", "install", "-y", "--user", app_id],
                    capture_output=True, text=True, encoding="utf-8", timeout=300,
                )
                GLib.idle_add(on_line, f"   {'✔' if r.returncode == 0 else '⚠'} {app_id}\n")
        GLib.idle_add(on_done, ok)

    threading.Thread(target=_worker, daemon=True).start()


def restore_dconf_meta(meta_dir: Path) -> bool:
    ini = meta_dir / "dconf-full.ini"
    if not ini.exists():
        return False
    try:
        r = subprocess.run(
            ["dconf", "load", "/"],
            input=ini.read_text(encoding="utf-8"),
            text=True, encoding="utf-8", timeout=30
        )
        return r.returncode == 0
    except Exception:
        return False
