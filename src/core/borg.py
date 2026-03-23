from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

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
    {
        "key": "build_artifacts",
        "title": "Сборочные артефакты (Rust, Node.js, Java...)",
        "description": "target/, build/, .gradle/ — первый бэкап может занять часы при большом объёме",
        "paths": [
            "**/target",
            "**/build",
            "**/.gradle",
            "**/.next",
            "**/.nuxt",
            "**/.svelte-kit",
            "**/.output",
            "~/.cargo/registry",
            "~/.cargo/git",
            "~/.rustup/toolchains",
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
    "**/.tox",
    # сборочные артефакты
    "**/target",
    "**/build",
    "**/.gradle",
    "**/.next",
    "**/.nuxt",
    "**/.svelte-kit",
    "**/.output",
    # менеджеры пакетов — кэш
    "~/.npm",
    "~/.yarn/cache",
    "~/.gradle/caches",
    "~/.m2/repository",
    "~/.cargo/registry",
    "~/.cargo/git",
    "~/.rustup/toolchains",
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


def _borg_env(repo_path: str) -> dict:
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = config.state_get("borg_passphrase", "") or ""
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    ssh_key = borg_ssh_key_path()
    if ("@" in repo_path or repo_path.startswith("ssh://")) and ssh_key.exists():
        try:
            if ssh_key.stat().st_mode & 0o077:
                os.chmod(ssh_key, 0o600)
        except OSError:
            pass
        env["BORG_RSH"] = f"ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new"
    return env


def is_repo_initialized(repo_path: str) -> bool:
    if not repo_path:
        return False
    p = Path(repo_path)
    if p.is_dir() and (p / "README").exists() and (p / "config").exists():
        return True
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
    _run_borg_async(cmd, lambda line: on_line(_translate_borg_line(line)), on_done, env=env)


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
    "IMPORTANT: you will need both KEY AND PASSPHRASE to access this repo!":
    "ВАЖНО: для доступа к этому хранилищу понадобятся И КЛЮЧ, И ПАРОЛЬНАЯ ФРАЗА!",
    "Key storage location depends on the mode:":
    "Место хранения ключа зависит от режима:",
    "- repokey modes: key is stored in the repository directory.":
    "- режимы repokey: ключ хранится в каталоге репозитория.",
    "- keyfile modes: key is stored in the home directory of this user.":
    "- режимы keyfile: ключ хранится в домашнем каталоге текущего пользователя.",
    "For any mode, you should:":
    "Для любого режима рекомендуется:",
    "1. Export the borg key and store the result at a safe place:":
    "1. Экспортируйте ключ borg и сохраните результат в безопасном месте:",
    "2. Write down the borg key passphrase and store it at safe place.":
    "2. Запишите парольную фразу ключа borg и храните её в безопасном месте.",
}


def _translate_borg_line(line: str) -> str:
    for en, ru in _BORG_TRANSLATIONS.items():
        if en in line:
            line = line.replace(en, ru)
    return line


def borg_create(repo_path: str, archive_name: str, paths: list[str], excludes: list[str], on_line, on_done, exclude_caches: bool = True) -> None:
    def _on_line_ru(line: str):
        on_line(_translate_borg_line(line))

    cmd = [_borg_exe(), "create", "--stats", "--progress", "--compression", "lz4"]
    if exclude_caches:
        cmd.append("--exclude-caches")
    cmd.append(f"{repo_path}::{archive_name}")
    cmd += paths
    for e in excludes:
        cmd += ["--exclude", os.path.expanduser(e)]
    _run_borg_async(cmd, _on_line_ru, on_done, env=_borg_env(repo_path))


def borg_estimate_create(
    repo_path: str,
    paths: list[str],
    excludes: list[str],
    exclude_caches: bool = True,
) -> dict | None:
    if not repo_path or not paths:
        return None
    archive_name = "altbooster-estimate"
    cmd = [_borg_exe(), "create", "--stats", "--dry-run", "--compression", "lz4"]
    if exclude_caches:
        cmd.append("--exclude-caches")
    cmd.append(f"{repo_path}::{archive_name}")
    cmd += paths
    for e in excludes:
        cmd += ["--exclude", os.path.expanduser(e)]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=_borg_env(repo_path),
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
        import re

        m = re.search(
            r"This archive:\s+([0-9][0-9.,]*\s+[A-Za-z]+)\s+([0-9][0-9.,]*\s+[A-Za-z]+)\s+([0-9][0-9.,]*\s+[A-Za-z]+)",
            text,
        )
        if m:
            return {
                "original": m.group(1).strip(),
                "compressed": m.group(2).strip(),
                "deduplicated": m.group(3).strip(),
            }
    except Exception:
        pass
    return None


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


def borg_ssh_key_path() -> Path:
    return config.CONFIG_DIR / "borg_id_ed25519"


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


def _gvfs_runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "gvfs"
    return Path(f"/run/user/{os.getuid()}/gvfs")


def _scan_gvfs_for_google(base: Path) -> str | None:
    if not base.is_dir():
        return None
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        n = entry.name.lower()
        if "google-drive" in n or "gdrive" in n:
            return str(entry.resolve())
    return None


def _line_looks_like_google_gvfs_mount(line: str) -> bool:
    low = line.lower()
    if "google-drive" in low or "google-drive:" in low:
        return True
    if "google" in low and "drive" in low:
        return True
    # Локализованные подписи (например «Диск Google»)
    if "google" in low and "диск" in low:
        return True
    return False


def _path_from_gio_mount_line(line: str) -> str | None:
    if " -> " not in line or "file://" not in line:
        return None
    if not _line_looks_like_google_gvfs_mount(line):
        return None
    uri_part = line.rsplit(" -> ", 1)[-1].strip()
    if not uri_part.startswith("file://"):
        return None
    raw = unquote(urlparse(uri_part).path).rstrip("/")
    if not raw:
        return None
    p = Path(raw)
    return str(p.resolve()) if p.is_dir() else None


def find_gvfs_google_drive() -> str | None:
    """
    Каталог GVFS для Google Drive: обычно
    /run/user/UID/gvfs/google-drive:host=…,user=…
    Точка появляется после входа в GOA и часто — после первого открытия диска в «Файлах».
    """
    base = _gvfs_runtime_dir()
    found = _scan_gvfs_for_google(base)
    if found:
        return found

    try:
        r = subprocess.run(
            ["gio", "mount", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
        )
        for line in r.stdout.splitlines():
            p = _path_from_gio_mount_line(line)
            if p:
                return p
        # После gio иногда успевает появиться каталог
        found = _scan_gvfs_for_google(base)
        if found:
            return found
    except Exception:
        pass

    try:
        r = subprocess.run(
            ["gio", "list", str(base)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        for line in r.stdout.splitlines():
            name = (line.strip().split(None, 1)[0] if line.strip() else "") or ""
            if not name or name in (".", ".."):
                continue
            nl = name.lower()
            if "google-drive" in nl or "gdrive" in nl:
                cand = base / name
                if cand.is_dir():
                    return str(cand.resolve())
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


def generate_system_meta(target_dir: Path, include_packages: bool = True) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        if include_packages:
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


def restore_packages_meta(meta_dir: Path, on_line, on_done, only_missing: bool = False) -> None:
    pkg_file = meta_dir / "packages.txt"
    if not pkg_file.exists():
        GLib.idle_add(on_done, False)
        return
    packages = pkg_file.read_text(encoding="utf-8").splitlines()
    packages = [p.strip() for p in packages if p.strip()]
    if not packages:
        GLib.idle_add(on_done, True)
        return

    if only_missing:
        try:
            r = subprocess.run(
                ["rpm", "-qa", "--queryformat", "%{NAME}\n"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            if r.returncode == 0:
                installed = {p.strip() for p in r.stdout.splitlines() if p.strip()}
                total = len(packages)
                packages = [p for p in packages if p not in installed]
                GLib.idle_add(
                    on_line,
                    f"▶  Системные пакеты: всего {total}, отсутствуют {len(packages)}\n",
                )
            else:
                GLib.idle_add(
                    on_line,
                    "⚠  Не удалось получить список установленных пакетов. Будет обычная установка.\n",
                )
        except Exception:
            GLib.idle_add(
                on_line,
                "⚠  Ошибка проверки установленных пакетов. Будет обычная установка.\n",
            )

    if not packages:
        GLib.idle_add(on_line, "✔  Все пакеты из резервной копии уже установлены.\n")
        GLib.idle_add(on_done, True)
        return

    from core import privileges
    GLib.idle_add(
        on_line,
        "▶  Установка отсутствующих RPM-пакетов...\n" if only_missing else "▶  Переустановка RPM-пакетов...\n",
    )
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


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


_BORG_ENV_FILE = config.CONFIG_DIR / "borg-env"


def _write_borg_env_file() -> bool:
    passphrase = config.state_get("borg_passphrase", "") or ""
    ssh_key = borg_ssh_key_path()
    content = (
        f"BORG_PASSPHRASE={passphrase}\n"
        f"BORG_RSH=ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new\n"
    )
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _BORG_ENV_FILE.write_text(content, encoding="utf-8")
        _BORG_ENV_FILE.chmod(0o600)
        return True
    except Exception:
        return False


def write_systemd_units(repo_path: str, paths: list[str], calendar_expr: str) -> bool:
    d = config.SYSTEMD_USER_DIR
    d.mkdir(parents=True, exist_ok=True)
    if not _write_borg_env_file():
        return False
    paths_str = " ".join(f'"{p}"' for p in paths)
    excludes_str = " ".join(
        f'--exclude "{os.path.expanduser(e)}"' for e in DEFAULT_EXCLUDES
    )
    borg_exe = _borg_exe()

    service_content = (
        "[Unit]\n"
        "Description=ALT Booster — резервное копирование\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"EnvironmentFile={_BORG_ENV_FILE}\n"
        "ExecStart=/bin/bash -c '"
        "mkdir -p /tmp/altbooster-backup-meta && "
        "flatpak list --app --columns=application > /tmp/altbooster-backup-meta/flatpak-apps.txt 2>/dev/null; "
        "flatpak remotes --columns=name,url > /tmp/altbooster-backup-meta/flatpak-remotes.txt 2>/dev/null; "
        "rpm -qa --queryformat \"%%{NAME}\\n\" | sort -u > /tmp/altbooster-backup-meta/packages.txt 2>/dev/null; "
        "dconf dump / > /tmp/altbooster-backup-meta/dconf-full.ini 2>/dev/null; "
        f'{borg_exe} create --stats --compression lz4 "{repo_path}::$(hostname)-$(date +%%Y-%%m-%%dT%%H-%%M)" '
        f"{paths_str} /tmp/altbooster-backup-meta {excludes_str}'\n"
    )

    timer_content = (
        "[Unit]\n"
        "Description=ALT Booster — таймер резервного копирования\n\n"
        "[Timer]\n"
        f"OnCalendar={calendar_expr}\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    service_file = d / "altbooster-backup.service"
    timer_file = d / "altbooster-backup.timer"
    try:
        service_file.write_text(service_content, encoding="utf-8")
        service_file.chmod(0o600)
        timer_file.write_text(timer_content, encoding="utf-8")
        return True
    except Exception:
        return False


def enable_systemd_timer() -> bool:
    try:
        _run_systemctl(["daemon-reload"])
        r = _run_systemctl(["enable", "--now", "altbooster-backup.timer"])
        return r.returncode == 0
    except Exception:
        return False


def disable_systemd_timer() -> bool:
    try:
        r = _run_systemctl(["disable", "--now", "altbooster-backup.timer"])
        return r.returncode == 0
    except Exception:
        return False


def is_timer_active() -> bool:
    try:
        r = _run_systemctl(["is-active", "altbooster-backup.timer"])
        return r.returncode == 0
    except Exception:
        return False


def get_timer_next_run() -> str | None:
    try:
        r = _run_systemctl(["show", "altbooster-backup.timer", "--property=NextElapseUSecRealtime"])
        for line in r.stdout.splitlines():
            if "=" in line:
                val = line.split("=", 1)[1].strip()
                if val and val != "0":
                    usec = int(val)
                    dt = datetime.datetime.fromtimestamp(usec / 1_000_000)
                    return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass
    return None
