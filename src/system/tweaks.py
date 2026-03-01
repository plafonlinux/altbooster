"""
tweaks.py — Применение различных системных твиков.
"""

from __future__ import annotations

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
    """Патчит системное расширение drive-menu для GNOME Shell, добавляя задержку 5 сек.

    Что делает в реальности:
    - Если файл уже пропатчен (содержит GLib.timeout_add_seconds) — выходит без
      изменений (идемпотентность).
    - Делает бэкап оригинала в .bak.
    - Через три sed -i заменяет прямое создание DriveMenu на отложенное (таймер 5 сек),
      убирает немедленный addToStatusArea и добавляет очистку таймера при деактивации.
    - Восстанавливает права 644, потому что некоторые версии sed временно меняют их.
    - При неудаче патчинга восстанавливает оригинал из бэкапа.

    Зачем нужна задержка: при быстром монтировании дисков GNOME Shell иногда падает
    из-за race condition в drive-menu. 5 секунд достаточно для стабилизации оболочки.
    """
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
    """Устанавливает плагин AAC-кодека для DaVinci Resolve из tar.gz-архива.

    Что делает в реальности:
    - Распаковывает архив в /tmp через tar xzf.
    - Копирует bundle-директорию плагина (aac_encoder_plugin.dvcp.bundle)
      в /opt/resolve/IOPlugins/ — именно там DaVinci Resolve ищет IO-плагины.
    - Требует root, потому что /opt/resolve/ принадлежит root.
    """
    cmd = ["bash", "-c", f"tar xzf '{archive_path}' -C /tmp && cp -r /tmp/aac_encoder_plugin.dvcp.bundle /opt/resolve/IOPlugins/"]
    run_privileged(cmd, on_line, on_done)
