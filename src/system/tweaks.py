"""
tweaks.py — Применение различных системных твиков.
"""

from __future__ import annotations

from .privileges import run_privileged, OnLine, OnDone


def apply_vm_dirty(on_log: OnLine, on_done: OnDone) -> None:
    """Применяет оптимизации vm dirty."""
    cmd = ["bash", "-c", "echo -e 'vm.dirty_bytes = 67108864\\nvm.dirty_background_bytes = 16777216' > /etc/sysctl.d/90-dirty.conf && sysctl -p /etc/sysctl.d/90-dirty.conf"]
    run_privileged(cmd, on_log, on_done)

def patch_drive_menu(on_log: OnLine, on_done: OnDone) -> None:
    """Внедряет задержку 5 сек в extension.js (с восстановлением прав доступа)."""
    script = """
FILE="/usr/share/gnome-shell/extensions/drive-menu@gnome-shell-extensions.gcampax.github.com/extension.js"
if [ ! -f "$FILE" ]; then exit 1; fi

# Принудительно возвращаем права на чтение, если прошлый sed -i их сломал
chmod 644 "$FILE"

# Если уже пропатчено ранее
if grep -q "GLib.timeout_add_seconds" "$FILE"; then exit 0; fi

# Делаем бекап
cp "$FILE" "$FILE.bak"

# Применяем патч
sed -i 's/this._indicator = new DriveMenu();/this._delayId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => { this._indicator = new DriveMenu(); Main.panel.addToStatusArea("drive-menu", this._indicator); return GLib.SOURCE_REMOVE; });/' "$FILE"
sed -i '/Main.panel.addToStatusArea/d' "$FILE"
sed -i '/if (this._indicator) {/i \\        if (this._delayId) { GLib.Source.remove(this._delayId); this._delayId = null; }' "$FILE"

# Снова восстанавливаем права для нового файла
chmod 644 "$FILE"

# Проверяем успешность
if grep -q "GLib.timeout_add_seconds" "$FILE"; then
    exit 0
else
    mv "$FILE.bak" "$FILE"
    chmod 644 "$FILE"
    exit 1
fi
"""
    run_privileged(["bash", "-c", script], on_log, on_done)

def install_aac_codec(archive_path: str, on_line: OnLine, on_done: OnDone) -> None:
    """Устанавливает кодек AAC для DaVinci Resolve."""
    cmd = ["bash", "-c", f"tar xzf '{archive_path}' -C /tmp && cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"]
    run_privileged(cmd, on_line, on_done)
