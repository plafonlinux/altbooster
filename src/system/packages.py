"""
packages.py — функции для работы с пакетным менеджером.

Содержит:
- InstallPreview: dataclass с результатами симуляции установки
- get_install_preview(): запускает apt-get -s (через runner с root-правами)
  Для Flatpak: запускает flatpak --system remote-info без root
  Для epm play: возвращает базовую информацию (dry-run недоступен)

Почему нужен runner с root:
  ALT Linux использует apt-rpm, у которого apt-get -s требует
  запись в /var/cache/apt/ — то есть нужны root-права даже для
  режима симуляции (в отличие от Debian apt).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable


# Тип callable для запуска привилегированных команд (run_privileged_sync)
# Сигнатура: runner(cmd: list[str], on_line: callable) -> bool
PrivilegedRunner = Callable[[list[str], Callable[[str], None]], bool]


@dataclass
class InstallPreview:
    """Результат симуляции установки пакетов."""

    new_packages: list[str] = field(default_factory=list)
    upgraded_packages: list[str] = field(default_factory=list)
    removed_packages: list[str] = field(default_factory=list)
    kept_packages: list[str] = field(default_factory=list)   # "СОХРАНЕНЫ" / "kept back"
    download_size: str = ""     # напр. "15.2 МБ"
    disk_space: str = ""        # напр. "45.6 МБ"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run_failed: bool = False
    # "apt" | "flatpak" | "epm_play" | "script"
    source_type: str = "apt"
    package_names: list[str] = field(default_factory=list)
    # Дополнительные поля для Flatpak (одиночная установка)
    app_version: str = ""
    app_description: str = ""
    # Доступные обновления Flatpak (для системного обновления)
    flatpak_updates: list[str] = field(default_factory=list)
    # Ссылка (для epm play)
    app_url: str = ""


def _extract_pkg_names(cmd: list[str]) -> list[str]:
    """Извлекает имена пакетов из команды (аргументы без '-')."""
    start = 2 if len(cmd) > 1 else 1
    return [arg for arg in cmd[start:] if not arg.startswith("-")]


def _detect_source_type(cmd: list[str]) -> str:
    """Определяет тип источника по команде установки."""
    if not cmd:
        return "script"
    if cmd[0] == "flatpak":
        return "flatpak"
    if cmd[0] == "epm" and len(cmd) > 1 and cmd[1] == "play":
        return "epm_play"
    if cmd[0] == "bash":
        return "script"
    return "apt"


# ────────────────────────────────────────────────────────────────────────── #
#  Парсер вывода apt-get -s                                                   #
# ────────────────────────────────────────────────────────────────────────── #

def _parse_apt_simulate_output(
    lines: list[str],
    source_type: str,
    package_names: list[str],
    failed: bool,
) -> InstallPreview:
    """Парсит вывод apt-get -s (dist-upgrade или install).

    Поддерживает как английский (LC_ALL=C), так и русский вывод.
    apt-rpm на ALT Linux выводит заголовки секций на языке системы.
    """
    preview = InstallPreview(
        source_type=source_type,
        dry_run_failed=failed,
        package_names=package_names,
    )

    section: str | None = None

    for line in lines:
        stripped = line.strip()

        # ── Заголовки секций (английский и русский) ──────────────────────
        if any(x in line for x in (
            "The following NEW packages will be installed",
            "Следующие НОВЫЕ пакеты будут установлены",
        )):
            section = "new"
            continue
        if any(x in line for x in (
            "The following packages will be upgraded",
            "Следующие пакеты будут ОБНОВЛЕНЫ",
            "будут обновлены",
        )):
            section = "upgrade"
            continue
        if any(x in line for x in (
            "The following packages will be REMOVED",
            "Следующие пакеты будут УДАЛЕНЫ",
            "будут удалены",
        )):
            section = "removed"
            continue
        if any(x in line for x in (
            "The following packages have been kept back",
            "The following packages will be kept",
            "Следующие пакеты будут СОХРАНЕНЫ",
        )):
            section = "kept"
            continue
        if line.startswith("The following") or line.startswith("0 upgraded"):
            section = None

        # ── Строки с пакетами начинаются с пробела ───────────────────────
        if section and line.startswith(" ") and stripped:
            # Убираем версионные суффиксы вида name#version или name=version
            pkgs = [re.sub(r"[#=].*$", "", p) for p in stripped.split()]
            pkgs = [p for p in pkgs if p]
            if section == "new":
                preview.new_packages.extend(pkgs)
            elif section == "upgrade":
                preview.upgraded_packages.extend(pkgs)
            elif section == "removed":
                preview.removed_packages.extend(pkgs)
            elif section == "kept":
                preview.kept_packages.extend(pkgs)
            continue

        # ── Размеры (английский и русский) ───────────────────────────────
        # EN: "Need to get 15.2 MB of archives."
        # RU: "Необходимо получить 15.2MB архивов."
        m = re.search(
            r"(?:Need to get|Необходимо получить)\s+([^\s]+(?:\s+[^\s]+)?)\s+(?:of archives|архивов)",
            line,
        )
        if m:
            preview.download_size = m.group(1).strip()

        # EN: "After this operation, 45.6 MB of additional disk space will be used."
        # RU: "После распаковки потребуется дополнительно 94.0MB дискового пространства."
        m = re.search(
            r"(?:After this operation,?\s+|После распаковки потребуется дополнительно\s+)"
            r"([^\s]+(?:\s+[^\s]+)?)\s+(?:of additional|дискового)",
            line,
        )
        if m:
            preview.disk_space = m.group(1).strip()

        # ── Ошибки / предупреждения ───────────────────────────────────────
        if stripped.startswith("E:"):
            preview.errors.append(stripped[2:].strip())
        elif stripped.startswith("W:"):
            preview.warnings.append(stripped[2:].strip())

    return preview


# ────────────────────────────────────────────────────────────────────────── #
#  Flatpak                                                                    #
# ────────────────────────────────────────────────────────────────────────── #

def get_flatpak_system_updates() -> list[str]:
    """Возвращает список ID Flatpak-приложений с доступными обновлениями.

    Что делает в реальности:
    - Запускает `echo n | flatpak update` с LC_ALL=C
    - flatpak показывает список обновлений и при вводе "n" выходит без установки
    - Парсит строки вида " 1. [✓] org.telegram.desktop  x86_64  ..."
    - Работает без root, не устанавливает ничего
    - Таймаут 20 сек (поиск обновлений требует запроса к репозиторию)
    - Возвращает [] при любой ошибке или если обновлений нет
    """
    env = {"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    try:
        proc = subprocess.run(
            ["flatpak", "update"],
            input="n\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            env=env,
        )
        output = proc.stdout + proc.stderr
    except Exception:
        return []

    updates = []
    # Строки обновлений: "  1. [✓] org.telegram.desktop   x86_64  stable  flathub  90.6 MB"
    for line in output.splitlines():
        m = re.match(r"^\s+\d+\.\s+\[.?\]\s+(\S+)", line)
        if m:
            updates.append(m.group(1))
    return updates


def _get_flatpak_info(cmd: list[str]) -> InstallPreview:
    """Получает информацию о Flatpak-приложении через flatpak remote-info.

    Не требует root. Пробует --system, затем --user.
    """
    args = [a for a in cmd[2:] if not a.startswith("-")]
    if len(args) < 2:
        return InstallPreview(source_type="flatpak", package_names=args)

    remote, appid = args[0], args[1]
    env = {"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

    output = ""
    for scope in ("--system", "--user"):
        try:
            r = subprocess.run(
                ["flatpak", scope, "remote-info", remote, appid],
                capture_output=True, text=True, encoding="utf-8",
                timeout=10, env=env,
            )
            if r.returncode == 0:
                output = r.stdout
                break
        except Exception:
            pass

    preview = InstallPreview(source_type="flatpak", package_names=[appid])

    if not output:
        return preview

    lines = output.strip().splitlines()
    if lines:
        first = lines[0].strip()
        if " - " in first:
            preview.app_description = first.split(" - ", 1)[1].strip()

    for line in lines:
        # Убираем нечитаемые символы (non-breaking space и т.п.)
        s = re.sub(r"[^\x20-\x7E\u0400-\u04FF]", " ", line.strip()).strip()
        if s.startswith("Version:"):
            preview.app_version = s.split(":", 1)[1].strip()
        elif s.startswith("Download:"):
            preview.download_size = s.split(":", 1)[1].strip()
        elif s.startswith("Installed:"):
            preview.disk_space = s.split(":", 1)[1].strip()

    name_label = appid
    if preview.app_version:
        name_label += f"  {preview.app_version}"
    preview.new_packages = [name_label]
    return preview


# ────────────────────────────────────────────────────────────────────────── #
#  epm play                                                                   #
# ────────────────────────────────────────────────────────────────────────── #

def _get_epm_play_info(cmd: list[str]) -> InstallPreview:
    """Получает URL приложения через epm play --info. Dry-run недоступен."""
    pkg_names = _extract_pkg_names(cmd)
    preview = InstallPreview(source_type="epm_play", package_names=pkg_names)

    if not pkg_names:
        return preview

    pkg = pkg_names[0]
    try:
        r = subprocess.run(
            ["epm", "play", "--info", pkg],
            capture_output=True, text=True, encoding="utf-8", timeout=8,
        )
        for line in (r.stdout + r.stderr).splitlines():
            if line.lower().startswith("url:"):
                preview.app_url = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    preview.new_packages = [pkg]
    return preview


# ────────────────────────────────────────────────────────────────────────── #
#  Основная точка входа                                                       #
# ────────────────────────────────────────────────────────────────────────── #

def get_install_preview(
    cmd: list[str],
    runner: PrivilegedRunner | None = None,
) -> InstallPreview:
    """Симулирует установку и возвращает список изменений.

    Что делает в реальности:
    - Для flatpak: flatpak --system remote-info (без root)
    - Для epm play: epm play --info (без root, только URL)
    - Для apt/epm install: apt-get -s install <pkgs> через runner (с root)
    - Для apt-get dist-upgrade: apt-get -s dist-upgrade через runner (с root)

    runner — это backend.run_privileged_sync или backend.run_epm_sync.
    ALT Linux apt-rpm требует root даже для -s (simulate) режима.
    Если runner=None — пробуем без root (работает только на некоторых системах).
    Блокирующая функция — вызывать из фонового потока.
    """
    source_type = _detect_source_type(cmd)

    if source_type == "flatpak":
        return _get_flatpak_info(cmd)
    if source_type == "epm_play":
        return _get_epm_play_info(cmd)
    if source_type == "script":
        return InstallPreview(source_type="script", package_names=_extract_pkg_names(cmd))

    # ── APT / EPM ──────────────────────────────────────────────────────── #
    package_names = _extract_pkg_names(cmd)

    is_dist_upgrade = (
        len(cmd) > 1 and cmd[1] in ("dist-upgrade", "full-upgrade", "upgrade")
    ) or (
        cmd[0] == "epm" and len(cmd) > 1 and cmd[1] in ("full-upgrade", "upgrade")
    )

    # Команда для сухого прогона с LC_ALL=C для стабильного английского вывода
    if is_dist_upgrade:
        dry_cmd = ["env", "LC_ALL=C", "apt-get", "-s", "dist-upgrade"]
    elif package_names:
        dry_cmd = ["env", "LC_ALL=C", "apt-get", "-s", "install"] + package_names
    else:
        return InstallPreview(
            source_type=source_type, dry_run_failed=True, package_names=package_names
        )

    lines: list[str] = []
    failed = False

    if runner is not None:
        # Запускаем через sudo/pkexec (нужно на ALT Linux)
        try:
            ok = runner(dry_cmd, lambda line: lines.append(line))
            failed = not ok
        except Exception:
            failed = True
    else:
        # Fallback: без root (работает только если /var/cache/apt/ доступен)
        env = {"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        try:
            r = subprocess.run(
                dry_cmd[1:],  # убираем "env" — задаём env напрямую
                capture_output=True, text=True, encoding="utf-8",
                timeout=15, env=env,
            )
            lines = (r.stdout + r.stderr).splitlines(keepends=True)
            failed = r.returncode != 0
        except Exception:
            failed = True

    preview = _parse_apt_simulate_output(lines, source_type, package_names, failed)

    # Для системного обновления дополнительно проверяем Flatpak
    if is_dist_upgrade:
        preview.flatpak_updates = get_flatpak_system_updates()

    return preview
