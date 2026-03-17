from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from core import config


OPTIONAL_EXCLUDES = [
    {
        "key": "steam_games",
        "title": "Игры Steam",
        "description": "steamapps/common — включите, чтобы сразу играть после восстановления",
        "paths": [
            "~/.local/share/Steam/steamapps/common",
            "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common",
        ],
    },
    {
        "key": "vm_images",
        "title": "Образы виртуальных машин",
        "description": "GNOME Boxes, libvirt, VirtualBox — образы дисков",
        "paths": [
            "~/.local/share/gnome-boxes/images",
            "~/.var/app/org.gnome.Boxes/data/gnome-boxes/images",
            "~/.local/share/libvirt/images",
            "~/.config/VirtualBox/*.vdi",
            "~/.config/VirtualBox/*.vmdk",
            "~/.config/VirtualBox/*.vhd",
        ],
    },
    {
        "key": "containers",
        "title": "Контейнеры Podman / Docker",
        "description": "~/.local/share/containers — образы и данные контейнеров",
        "paths": [
            "~/.local/share/containers",
        ],
    },
    {
        "key": "heroic_tools",
        "title": "Wine / Proton для Heroic Games",
        "description": "Дистрибутивы Wine и Proton — перекачиваются автоматически",
        "paths": [
            "~/.var/app/com.heroicgameslauncher.hgl/config/heroic/tools",
        ],
    },
]

_OPTIONAL_PATHS = {p for g in OPTIONAL_EXCLUDES for p in g["paths"]}

DEFAULT_EXCLUDES = [
    # системный кэш
    "~/.cache",
    "~/.var/app/*/cache",
    "~/.var/app/*/.cache",
    "~/.var/app/*/Cache",
    # браузеры — кэш и Service Worker
    "~/.config/google-chrome/*/Cache",
    "~/.config/google-chrome/*/Cache_Data",
    "~/.config/google-chrome/*/CacheStorage",
    "~/.config/google-chrome/*/GPUCache",
    "~/.config/google-chrome/*/Service Worker/CacheStorage",
    "~/.config/google-chrome/*/optimization_guide_model_store",
    "~/.config/yandex-browser/*/Cache",
    "~/.config/yandex-browser/*/Cache_Data",
    "~/.config/yandex-browser/*/GPUCache",
    "~/.config/yandex-browser/*/CacheStorage",
    "~/.config/yandex-browser/*/Service Worker/CacheStorage",
    "~/.config/yandex-browser/*/AsrSubtitles",
    "~/.config/yandex-browser/*/Safe Browsing",
    "~/.config/yandex-browser/*/component_crx_cache",
    "~/.config/yandex-browser/*/extensions_crx_cache",
    "~/.config/yandex-browser/*/Resources/extension/cache_*",
    # Chrome Flatpak
    "~/.var/app/com.google.Chrome/config/google-chrome/*/Cache",
    "~/.var/app/com.google.Chrome/config/google-chrome/*/Cache_Data",
    "~/.var/app/com.google.Chrome/config/google-chrome/*/GPUCache",
    "~/.var/app/com.google.Chrome/config/google-chrome/*/CacheStorage",
    "~/.var/app/com.google.Chrome/config/google-chrome/*/Service Worker/CacheStorage",
    "~/.var/app/com.google.Chrome/config/google-chrome/*/ShaderCache",
    "~/.var/app/com.google.Chrome/config/google-chrome/optimization_guide_model_store",
    "~/.var/app/com.google.Chrome/config/google-chrome/OnDeviceHeadSuggestModel",
    # VS Code — кэш и логи
    "~/.config/Code/Cache",
    "~/.config/Code/CachedData",
    "~/.config/Code/CachedExtensionVSIXs",
    "~/.config/Code/WebStorage",
    "~/.config/Code/logs",
    # контейнеры
    "~/.local/share/containers",
    # PortProton — дистрибутивы Wine/Proton и системные файлы префиксов
    "~/.var/app/ru.linux_gaming.PortProton/data/tmp",
    "~/.var/app/ru.linux_gaming.PortProton/data/dist",
    "~/.var/app/ru.linux_gaming.PortProton/data/prefixes/*/drive_c/windows",
    "~/.var/app/ru.linux_gaming.PortProton/data/prefixes/*/drive_c/Program Files",
    "~/.var/app/ru.linux_gaming.PortProton/data/prefixes/*/drive_c/Program Files (x86)",
    "~/.var/app/ru.linux_gaming.PortProton/data/prefixes/*/drive_c/users/*/AppData/Local/Temp",
    "~/.var/app/ru.linux_gaming.PortProton/data/prefixes/*/drive_c/users/*/AppData/Local/Microsoft/Windows/INetCache",
    # образы виртуальных машин и сохранённые состояния (suspend saves)
    "~/.local/share/gnome-boxes/images",
    "~/.var/app/org.gnome.Boxes/data/gnome-boxes/images",
    "~/.var/app/org.gnome.Boxes/config/libvirt/qemu/save",
    "~/.local/share/libvirt/images",
    "~/.config/libvirt/qemu/save",
    # Heroic Games Launcher — дистрибутивы Wine/Proton (re-downloadable)
    "~/.var/app/com.heroicgameslauncher.hgl/config/heroic/tools",
    # Warehouse (Flattool) — снимки flatpak-приложений
    "~/.var/app/io.github.flattool.Warehouse/data/Snapshots",
    # Telegram — медиакэш
    "~/.var/app/org.telegram.desktop/data/TelegramDesktop/tdata/user_data/media_cache",
    "~/.config/TelegramDesktop/tdata/user_data/media_cache",
    "~/.config/VirtualBox/*.vdi",
    "~/.config/VirtualBox/*.vmdk",
    "~/.config/VirtualBox/*.vhd",
    # ISO в папках загрузок
    "~/Загрузки/*.iso",
    "~/Downloads/*.iso",
    # Steam (native) — игры и runtime (перекачиваемое)
    "~/.local/share/Steam/steamapps/common",
    "~/.local/share/Steam/steamapps/shadercache",
    "~/.local/share/Steam/ubuntu12_32",
    "~/.local/share/Steam/ubuntu12_64",
    "~/.local/share/Steam/package",
    "~/.local/share/Steam/appcache",
    "~/.local/share/Steam/logs",
    # Steam (Flatpak) — игры и runtime (перекачиваемое), userdata/ сохраняется
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/shadercache",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/ubuntu12_32",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/ubuntu12_64",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/package",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/appcache",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/config/htmlcache",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/logs",
    # DaVinci Resolve — только кэш, не проекты
    "~/.local/share/DaVinciResolve/DVIP/Cache",
    "~/.local/share/DaVinciResolve/logs",
    # OrcaSlicer — системные профили (скачиваются автоматически)
    "~/.config/OrcaSlicer/system",
    # миниатюры
    "~/.local/share/thumbnails",
    # Python виртуальные окружения
    "**/venv",
    "**/.venv",
    # менеджеры пакетов — кэш
    "~/.npm",
    "~/.yarn/cache",
    "~/.gradle/caches",
    "~/.m2/repository",
    "~/.cargo/registry",
    # прочее
    "~/.local/share/gvfs-metadata",
    "~/.local/share/Trash",
    "**/.git",
    "**/node_modules",
    "**/__pycache__",
]


def _borg_exe() -> str:
    return shutil.which("borg") or shutil.which("borgbackup") or "borg"


def is_borg_installed() -> bool:
    return shutil.which("borg") is not None or shutil.which("borgbackup") is not None


def borg_version() -> str | None:
    exe = shutil.which("borg") or shutil.which("borgbackup")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "--version"],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def borg_ssh_key_path() -> Path:
    return config.CONFIG_DIR / "borg_id_ed25519"


def _borg_env(repo_path: str) -> dict:
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = config.state_get("borg_passphrase", "") or ""
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    ssh_key = borg_ssh_key_path()
    if ("@" in repo_path or repo_path.startswith("ssh://")) and ssh_key.exists():
        env["BORG_RSH"] = f"ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new"
    return env


def is_repo_initialized(repo_path: str) -> bool:
    if not repo_path:
        return False
    try:
        r = subprocess.run(
            [_borg_exe(), "info", "--json", repo_path],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            env=_borg_env(repo_path),
        )
        return r.returncode == 0
    except Exception:
        return False


def borg_repo_info(repo_path: str) -> dict | None:
    try:
        r = subprocess.run(
            [_borg_exe(), "info", "--json", repo_path],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            env=_borg_env(repo_path),
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def _run_borg_async(cmd: list, on_line, on_done, cwd: str | None = None, env: dict | None = None) -> None:
    def _worker():
        ok = False
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8",
                cwd=cwd, env=env,
            )
            for line in proc.stdout:
                GLib.idle_add(on_line, line)
            proc.wait()
            ok = proc.returncode in (0, 1)
        except Exception as e:
            GLib.idle_add(on_line, f"✘ Ошибка: {e}\n")
        GLib.idle_add(on_done, ok)

    threading.Thread(target=_worker, daemon=True).start()


def borg_init(repo_path: str, on_line, on_done) -> None:
    cmd = [_borg_exe(), "init", "--encryption=repokey", repo_path]
    env = _borg_env(repo_path)
    env["BORG_NEW_PASSPHRASE"] = env.get("BORG_PASSPHRASE", "")
    _run_borg_async(cmd, on_line, on_done, env=env)


_BORG_TRANSLATIONS = {
    "Saving files cache":                  "Сохранение кэша файлов",
    "Saving chunks cache":                 "Сохранение кэша блоков",
    "Saving cache config":                 "Сохранение конфигурации кэша",
    "Repository:":                         "Репозиторий:",
    "Archive name:":                       "Имя архива:",
    "Archive fingerprint:":                "Отпечаток архива:",
    "Time (start):":                       "Начало:",
    "Time (end):":                         "Конец:",
    "Duration:":                           "Длительность:",
    "Number of files:":                    "Количество файлов:",
    "Utilization of max. archive size:":   "Использование макс. размера архива:",
    "Original size":                       "Исходный размер",
    "Compressed size":                     "Сжатый размер",
    "Deduplicated size":                   "Дедуплицированный размер",
    "This archive:":                       "Этот архив:",
    "All archives:":                       "Все архивы:",
    "Unique chunks":                       "Уникальных блоков",
    "Total chunks":                        "Всего блоков",
    "Chunk index:":                        "Индекс блоков:",
    "minutes":                             "мин.",
    "seconds":                             "сек.",
    "minute":                              "мин.",
    "second":                              "сек.",
}


def _translate_borg_line(line: str) -> str:
    for en, ru in _BORG_TRANSLATIONS.items():
        if en in line:
            line = line.replace(en, ru)
    return line


def borg_create(repo_path: str, archive_name: str, paths: list[str], excludes: list[str], on_line, on_done) -> None:
    def _on_line_ru(line: str):
        on_line(_translate_borg_line(line))

    cmd = [_borg_exe(), "create", "--stats", "--progress", f"{repo_path}::{archive_name}"] + paths
    for e in excludes:
        cmd += ["--exclude", os.path.expanduser(e)]
    _run_borg_async(cmd, _on_line_ru, on_done, env=_borg_env(repo_path))


def borg_list(repo_path: str) -> tuple[list[dict], str]:
    try:
        r = subprocess.run(
            [_borg_exe(), "list", "--json", repo_path],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env=_borg_env(repo_path),
        )
        if r.returncode in (0, 1):
            try:
                return json.loads(r.stdout).get("archives", []), ""
            except Exception:
                pass
        return [], (r.stderr or r.stdout or f"returncode={r.returncode}").strip()
    except Exception as e:
        return [], str(e)


def borg_list_archive(repo_path: str, archive_name: str) -> list[dict]:
    try:
        r = subprocess.run(
            [_borg_exe(), "list", "--json-lines", f"{repo_path}::{archive_name}"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env=_borg_env(repo_path),
        )
        if r.returncode == 0:
            result = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    try:
                        result.append(json.loads(line))
                    except Exception:
                        pass
            return result
    except Exception:
        pass
    return []


def borg_archive_info(repo_path: str, archive_name: str, on_done) -> None:
    def _worker():
        try:
            r = subprocess.run(
                [_borg_exe(), "info", "--json", f"{repo_path}::{archive_name}"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
                env=_borg_env(repo_path),
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                archives = data.get("archives", [])
                if archives:
                    GLib.idle_add(on_done, archives[0].get("stats"))
                    return
        except Exception:
            pass
        GLib.idle_add(on_done, None)

    threading.Thread(target=_worker, daemon=True).start()


def borg_extract(repo_path: str, archive_name: str, target_dir: str, paths: list[str], on_line, on_done) -> None:
    cmd = [_borg_exe(), "extract", "--progress", f"{repo_path}::{archive_name}"] + paths
    _run_borg_async(cmd, on_line, on_done, cwd=target_dir, env=_borg_env(repo_path))


def borg_check(repo_path: str, on_line, on_done) -> None:
    _run_borg_async(
        [_borg_exe(), "check", "--verify-data", repo_path],
        on_line, on_done, env=_borg_env(repo_path),
    )


def borg_prune(repo_path: str, keep_daily: int, keep_weekly: int, keep_monthly: int, on_line, on_done) -> None:
    cmd = [
        _borg_exe(), "prune", "--list",
        f"--keep-daily={keep_daily}",
        f"--keep-weekly={keep_weekly}",
        f"--keep-monthly={keep_monthly}",
        repo_path,
    ]
    _run_borg_async(cmd, on_line, on_done, env=_borg_env(repo_path))


def borg_delete_archive(repo_path: str, archive_name: str, on_line, on_done) -> None:
    _run_borg_async(
        [_borg_exe(), "delete", f"{repo_path}::{archive_name}"],
        on_line, on_done, env=_borg_env(repo_path),
    )


def borg_compact(repo_path: str, on_line, on_done) -> None:
    _run_borg_async(
        [_borg_exe(), "compact", repo_path],
        on_line, on_done, env=_borg_env(repo_path),
    )


def borg_export_tar(repo_path: str, target_dir: str, on_line, on_done) -> None:
    repo = Path(repo_path)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    tar_path = Path(target_dir) / f"borg-backup-{date_str}.tar"

    def _worker():
        try:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["tar", "-cf", str(tar_path), "-C", str(repo.parent), repo.name],
                capture_output=True, text=True, encoding="utf-8",
            )
            if r.stderr:
                GLib.idle_add(on_line, r.stderr)
            GLib.idle_add(on_line, f"   → {tar_path}\n")
            GLib.idle_add(on_done, r.returncode == 0)
        except Exception as e:
            GLib.idle_add(on_line, f"   ✘ {e}\n")
            GLib.idle_add(on_done, False)

    threading.Thread(target=_worker, daemon=True).start()


def borg_generate_ssh_key() -> bool:
    key_path = borg_ssh_key_path()
    if key_path.exists():
        return True
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "altbooster-borg"],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def borg_get_pubkey() -> str | None:
    pub = Path(str(borg_ssh_key_path()) + ".pub")
    try:
        if pub.exists():
            return pub.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None
