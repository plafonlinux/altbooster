from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path


_GITHUB_API = "https://api.github.com/repos/plafonlinux/altbooster"


def _fetch_release_assets(version: str) -> list[dict]:
    url = f"{_GITHUB_API}/releases/tags/v{version}"
    req = urllib.request.Request(url, headers={"User-Agent": "ALTBooster"})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    assets = data.get("assets")
    return assets if isinstance(assets, list) else []


def _select_checksum_asset(assets: list[dict]) -> str | None:
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if "sha256" in name or name.endswith(".sha256"):
            return str(asset.get("browser_download_url", ""))
    return None


def _extract_expected_sha256(text: str, archive_name: str) -> str | None:
    archive_lower = archive_name.lower()
    for line in text.splitlines():
        if archive_lower not in line.lower():
            continue
        match = re.search(r"\b([a-fA-F0-9]{64})\b", line)
        if match:
            return match.group(1).lower()

    # fallback: first 64-hex token in file
    match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
    return match.group(1).lower() if match else None


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_release_tarball(version: str, tarball_path: str | Path) -> tuple[bool, str]:
    tarball = Path(tarball_path)
    if not tarball.exists():
        return False, "Файл обновления не найден"

    try:
        assets = _fetch_release_assets(version)
    except Exception as e:
        return False, f"Не удалось получить release-asset'ы: {e}"

    checksum_url = _select_checksum_asset(assets)
    if not checksum_url:
        return False, "В релизе отсутствует SHA256-файл для проверки"

    try:
        req = urllib.request.Request(checksum_url, headers={"User-Agent": "ALTBooster"})
        with urllib.request.urlopen(req, timeout=10) as response:
            checksum_text = response.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"Не удалось скачать SHA256-файл: {e}"

    expected = _extract_expected_sha256(checksum_text, tarball.name)
    if not expected:
        return False, "Не удалось извлечь SHA256 из checksum-файла"

    actual = _sha256_file(tarball)
    if actual != expected:
        return False, "Проверка SHA256 не пройдена"
    return True, "SHA256 подтверждён"
