"""
tweaks.py — Применение различных системных твиков.
"""

from __future__ import annotations

import os
import shlex
import tempfile

from .privileges import run_privileged, OnLine, OnDone


def apply_vm_dirty(on_log: OnLine, on_done: OnDone) -> None:
    """Записывает параметры vm.dirty в /etc/sysctl.d/90-dirty.conf и применяет их.

    Что делает в реальности:
    - vm.dirty_bytes = 64 МБ: ядро начинает сбрасывать «грязные» страницы на диск
      только когда их накопится не менее 64 МБ (вместо стандартных ~5% RAM).
    - vm.dirty_background_bytes = 16 МБ: фоновый writeback запускается тише,
      что снижает латентность записи в пользовательских задачах.
    - sysctl -p применяет настройки сразу, без перезагрузки.
    """
    cmd = ["bash", "-c", "echo -e 'vm.dirty_bytes = 67108864\\nvm.dirty_background_bytes = 16777216' > /etc/sysctl.d/90-dirty.conf && sysctl -p /etc/sysctl.d/90-dirty.conf"]
    run_privileged(cmd, on_log, on_done)


def patch_drive_menu(on_log: OnLine, on_done: OnDone) -> None:
    """Патчит расширение drive-menu для GNOME Shell, устраняя дублирование накопителей.

    Что делает в реальности:
    - Проверяет размер файла: патч рассчитан на оригинальный extension.js размером
      6197 байт. Если размер не совпадает (другая версия расширения) — выходит без
      изменений, чтобы не сломать файл.
    - Если патч уже применён (содержит this._mounts.some) — выходит без изменений.
    - Делает бэкап оригинала в .bak.
    - Применяет unified diff через утилиту patch: добавляет проверку дубликата
      монтирования в начало _addMount(), чтобы одно устройство не появлялось
      в меню несколько раз.
    - Очищает кэш GNOME Shell для немедленного применения изменений.

    Diff записывается Python во временный файл (доступен root), потому что stdin
    в run_privileged занят паролем sudo и heredoc внутри bash -c не работает.
    """
    home = os.path.expanduser("~")

    # Unified diff без заголовков --- / +++: patch принимает имя файла явно
    diff_content = (
        "@@ -178,6 +178,9 @@\n"
        "     }\n"
        " \n"
        "     _addMount(mount) {\n"
        "+        if (this._mounts.some(item => item.mount === mount))\n"
        "+            return;\n"
        "+\n"
        "         let item = new MountMenuItem(mount);\n"
        "         this._mounts.unshift(item);\n"
        "         this.menu.addMenuItem(item, 0);\n"
    )

    fd, patch_path = tempfile.mkstemp(suffix=".patch", prefix="altbooster_drive_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(diff_content)
    except Exception:
        try:
            os.unlink(patch_path)
        except OSError:
            pass
        on_done(False)
        return

    script = f"""set -e
TARGET="/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
BACKUP="$TARGET.bak"
ORIGINAL_SIZE=6197

echo "Проверяю наличие системного расширения..."

if [ ! -f "$TARGET" ]; then
    echo "Ошибка: файл $TARGET не найден."
    rm -f {patch_path}
    exit 1
fi

CURRENT_SIZE=$(stat -c%s "$TARGET")
echo "Оригинальный размер: $ORIGINAL_SIZE"
echo "Текущий размер:      $CURRENT_SIZE"

if [ "$CURRENT_SIZE" != "$ORIGINAL_SIZE" ]; then
    echo "Размер файла изменился. Патч НЕ применяется."
else
    if grep -q "this._mounts.some" "$TARGET"; then
        echo "Патч уже применён."
    else
        echo "Создаю резервную копию: $BACKUP"
        cp "$TARGET" "$BACKUP"

        echo "Устанавливаю утилиту patch..."
        apt-get install -y patch

        echo "Применяю патч..."
        patch -u -f "$TARGET" < {patch_path}

        echo "Патч успешно применён."
        echo "Очищаю кэш GNOME Shell..."
        rm -rf {shlex.quote(home)}/.cache/gnome-shell/*
    fi
fi

rm -f {patch_path}
echo "Готово!"
echo "Чтобы изменения вступили в силу, нажми Win+L и разблокируй экран."
"""
    run_privileged(["bash", "-c", script], on_log, on_done)

def install_aac_codec(archive_path: str, on_line: OnLine, on_done: OnDone) -> None:
    """Устанавливает плагин AAC-кодека для DaVinci Resolve из tar.gz-архива.

    Что делает в реальности:
    - Распаковывает архив в /tmp через tar xzf.
    - Копирует bundle-директорию плагина (aac_encoder_plugin.dvcp.bundle)
      в /opt/resolve/IOPlugins/ — именно там DaVinci Resolve ищет IO-плагины.
    - Требует root, потому что /opt/resolve/ принадлежит root.
    """
    cmd = ["bash", "-c", f"tar xzf {shlex.quote(archive_path)} -C /tmp && cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"]
    run_privileged(cmd, on_line, on_done)
