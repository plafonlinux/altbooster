from __future__ import annotations

import os
import shlex
import tempfile

from .privileges import run_privileged, OnLine, OnDone


def apply_vm_dirty(on_log: OnLine, on_done: OnDone) -> None:
    cmd = ["bash", "-c", "echo -e 'vm.dirty_bytes = 67108864\\nvm.dirty_background_bytes = 16777216' > /etc/sysctl.d/90-dirty.conf && sysctl -p /etc/sysctl.d/90-dirty.conf"]
    run_privileged(cmd, on_log, on_done)


def patch_drive_menu(on_log: OnLine, on_done: OnDone) -> None:
    home = os.path.expanduser("~")

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

    qpatch = shlex.quote(patch_path)
    script = f"""set -e
TARGET="/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
BACKUP="$TARGET.bak"
ORIGINAL_SIZE=6197

echo "Проверяю наличие системного расширения..."

if [ ! -f "$TARGET" ]; then
    echo "Ошибка: файл $TARGET не найден."
    rm -f {qpatch}
    exit 1
fi

CURRENT_SIZE=$(stat -c%s "$TARGET")
echo "Оригинальный размер: $ORIGINAL_SIZE"
echo "Текущий размер:      $CURRENT_SIZE"

if [ "$CURRENT_SIZE" != "$ORIGINAL_SIZE" ]; then
    echo "Размер файла изменился. Патч НЕ применяется."
    rm -f {qpatch}
    exit 0
fi

if grep -q "this._mounts.some" "$TARGET"; then
    echo "Патч уже применён."
    rm -f {qpatch}
    exit 0
fi

echo "Создаю резервную копию: $BACKUP"
cp "$TARGET" "$BACKUP"

echo "Устанавливаю утилиту patch..."
apt-get install -y patch

echo "Применяю патч..."
patch -u -f "$TARGET" < {qpatch}

echo "Патч успешно применён."
echo "Очищаю кэш GNOME Shell..."
rm -rf {shlex.quote(home)}/.cache/gnome-shell/*

rm -f {qpatch}
echo "Готово!"
echo "Чтобы изменения вступили в силу, нажми Win+L и разблокируй экран."
"""
    run_privileged(["bash", "-c", script], on_log, on_done)

def install_aac_codec(archive_path: str, on_line: OnLine, on_done: OnDone) -> None:
    cmd = ["bash", "-c", f"tar --no-symlinks -xzf {shlex.quote(archive_path)} -C /tmp && cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"]
    run_privileged(cmd, on_line, on_done)
