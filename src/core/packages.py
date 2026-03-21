from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable


PrivilegedRunner = Callable[[list[str], Callable[[str], None]], bool]


@dataclass
class InstallPreview:
    new_packages: list[str] = field(default_factory=list)
    upgraded_packages: list[str] = field(default_factory=list)
    removed_packages: list[str] = field(default_factory=list)
    kept_packages: list[str] = field(default_factory=list)
    download_size: str = ""
    disk_space: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run_failed: bool = False
    source_type: str = "apt"
    package_names: list[str] = field(default_factory=list)
    app_version: str = ""
    app_description: str = ""
    flatpak_updates: list[str] = field(default_factory=list)
    app_url: str = ""


def _extract_pkg_names(cmd: list[str]) -> list[str]:
    start = 2 if len(cmd) > 1 else 1
    return [arg for arg in cmd[start:] if not arg.startswith("-")]


def _detect_source_type(cmd: list[str]) -> str:
    if not cmd:
        return "script"
    if cmd[0] == "flatpak":
        return "flatpak"
    if cmd[0] == "epm" and len(cmd) > 1 and cmd[1] == "play":
        return "epm_play"
    if cmd[0] == "bash":
        return "script"
    return "apt"


def _parse_apt_simulate_output(
    lines: list[str],
    source_type: str,
    package_names: list[str],
    failed: bool,
) -> InstallPreview:
    preview = InstallPreview(
        source_type=source_type,
        dry_run_failed=failed,
        package_names=package_names,
    )

    section: str | None = None

    for line in lines:
        stripped = line.strip()

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

        if section and line.startswith(" ") and stripped:
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

        m = re.search(
            r"(?:Need to get|Необходимо получить)\s+([^\s]+(?:\s+[^\s]+)?)\s+(?:of archives|архивов)",
            line,
        )
        if m:
            preview.download_size = m.group(1).strip()

        m = re.search(
            r"(?:After this operation,?\s+|После распаковки потребуется дополнительно\s+)"
            r"([^\s]+(?:\s+[^\s]+)?)\s+(?:of additional|дискового)",
            line,
        )
        if m:
            preview.disk_space = m.group(1).strip()

        if stripped.startswith("E:"):
            preview.errors.append(stripped[2:].strip())
        elif stripped.startswith("W:"):
            preview.warnings.append(stripped[2:].strip())

    return preview


def get_flatpak_system_updates() -> list[str]:
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
    for line in output.splitlines():
        m = re.match(r"^\s+\d+\.\s+\[.?\]\s+(\S+)", line)
        if m:
            updates.append(m.group(1))
    return updates


def _get_flatpak_info(cmd: list[str]) -> InstallPreview:
    args = [a for a in cmd[2:] if not a.startswith("-")]
    if len(args) < 2:
        return InstallPreview(source_type="flatpak", package_names=args)

    remote, appid = args[0], args[1]
    env = {"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

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


def _get_epm_play_info(cmd: list[str]) -> InstallPreview:
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


def get_install_preview(
    cmd: list[str],
    runner: PrivilegedRunner | None = None,
) -> InstallPreview:
    source_type = _detect_source_type(cmd)

    if source_type == "flatpak":
        return _get_flatpak_info(cmd)
    if source_type == "epm_play":
        return _get_epm_play_info(cmd)
    if source_type == "script":
        return InstallPreview(source_type="script", package_names=_extract_pkg_names(cmd))

    package_names = _extract_pkg_names(cmd)

    is_dist_upgrade = (
        len(cmd) > 1 and cmd[1] in ("dist-upgrade", "full-upgrade", "upgrade")
    ) or (
        cmd[0] == "epm" and len(cmd) > 1 and cmd[1] in ("full-upgrade", "upgrade")
    )

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
        try:
            ok = runner(dry_cmd, lambda line: lines.append(line))
            failed = not ok
        except Exception:
            failed = True
    else:
        env = {"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        try:
            r = subprocess.run(
                dry_cmd[1:],
                capture_output=True, text=True, encoding="utf-8",
                timeout=15, env=env,
            )
            lines = (r.stdout + r.stderr).splitlines(keepends=True)
            failed = r.returncode != 0
        except Exception:
            failed = True

    preview = _parse_apt_simulate_output(lines, source_type, package_names, failed)

    if is_dist_upgrade:
        preview.flatpak_updates = get_flatpak_system_updates()

    return preview
