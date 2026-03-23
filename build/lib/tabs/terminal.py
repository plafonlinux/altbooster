
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core import backend
from core import config
from ui.widgets import make_scrolled_page, scroll_child_into_view
from ui.rows import SettingRow


_ALIASES_BLOCK = r"""
alias tm="sudo timeshift"
alias tmc="sudo timeshift --create"
alias tmd="sudo timeshift --delete"
alias tmda="sudo timeshift --delete-all"
alias tml="sudo timeshift --list"
alias n="fastfetch"
alias k="uname -rs"
alias g="gnome-shell --version"
alias f="lsb_release -sd"
alias m="inxi -G | grep mesa"
alias age="stat / | grep Birth:"
alias ram="sudo dmidecode -t memory | grep Speed"
alias cpu="lscpu | grep Имя"
alias cpuc="lscpu"
alias w="wine --version"
alias pc="inxi -Ixxx"
alias net="inxi -Nxxx"
alias ep="eepm-help"
alias epm-help="eepm-help"
alias eph="eepm-help"
alias find="epmqp"
alias poisk="epms"
alias up="epm update && epm full-upgrade && flatpak update --noninteractive -y"
alias cc="sudo apt-get clean && sudo apt-get autoclean && sudo apt-get check && sudo remove-old-kernels -a && flatpak uninstall --unused -y && sudo journalctl --vacuum-time=1weeks"
alias c="clear"
alias son="sudo systemctl suspend"
alias reboot="systemctl reboot"
alias r="systemctl reboot"
alias ls="ls --color"
alias l="lsd --date '+%d.%m.%Y %H:%M' -lah"
alias fli="flatpak install --noninteractive -y flathub"
alias flr="flatpak remove --noninteractive -y"
alias fr="flatpak repair"
alias fl="flatpak list"
alias gte="gnome-text-editor"
alias sgte="sudo gnome-text-editor"
alias fstab="sudo vim /etc/fstab"
alias bashrc="vim ~/.bashrc"
alias zshrc="vim ~/.zshrc"
alias bashrc="vim .bashrc"
alias grubedit="sudo vim /etc/default/grub"
alias editgrub="sudo vim /etc/default/grub"
alias upgrub="sudo grub-mkconfig -o /boot/grub/grub.cfg"
alias grubup="sudo grub-mkconfig -o /boot/grub/grub.cfg"
alias sn="sudo nautilus"
alias v4="sudo modprobe v4l2loopback"
alias modeprobe="sudo modprobe v4l2loopback"
alias vmax="sudo sysctl -w vm.max_map_count=2147483642"
"""

_FASTFETCH_CONFIG = r"""{
    "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
    "display": {
		"separator": "\u001b[90m ▐ "
    },
    "modules": [
        {
            "type": "os",
            "key": "",
            "keyColor": "94",
            "format": "{2} {#2}[p11]"
        },
        {
            "type": "kernel",
            "key": "",
            "keyColor": "39"
        },
        {
            "type": "packages",
            "key": "󰏖",
            "keyColor": "33"
        },
        {
            "type": "shell",
            "key": "",
            "keyColor": "94",
            "format": "{1} {#2}[{4}] {#2}"
        },
        {
            "type": "terminal",
            "key": "",
            "keyColor": "39",
            "format": "{1} {#2}[{6}]"
        },
        "break",

        {
            "type": "wm",
            "key": "󱍜",
            "keyColor": "34"
        },
        {
            "type": "wmtheme",
            "key": "󰉼",
            "keyColor": "33"
        },
        {
            "type": "icons",
            "key": "",
            "keyColor": "93"
        },

        "break",
        {
            "type": "host",
            "key": "󰌢",
            "keyColor": "92"
        },
        {
            "type": "display",
            "key": "󰹑",
            "keyColor": "32"
        },
        {
            "type": "cpu",
            "key": "󰍛",
            "keyColor": "96"
        },
        {
            "type": "gpu",
            "key": "󰢮",
            "keyColor": "96"
        },
        {
            "type": "memory",
            "key": "",
            "keyColor": "36"
        },
        {
            "type": "disk",
            "key": "󰋊",
            // "format": "{size-used} / {size-total} ({size-percentage})"
        },

        "break",
        {
            "type": "uptime",
            "key": "󱤦",
            "keyColor": "39"
        },
        {
            "type": "command",
            "key": "󱦟",
            "keyColor": "31",
            "text": "birth_install=$(stat -c %W /); current=$(date +%s); time_progression=$((current - birth_install)); days_difference=$((time_progression / 86400)); echo $days_difference день",
            "format": "Этой системе {1}"
        },

        "break",
        {
            "type": "poweradapter",
            "key": "{#90}{$1}│ {#91}Power       {#90}│",
            "format": "{$2}{$3}{name}",
            // fastfetch -h poweradapter-format
            // {2}: PowerAdapter name - name
            // The default is something similar to "{1}W".
        },
        {
            "type": "battery",
            "key": "Battery",
            "temp": true,
        },

        "break",
        "colors"
    ]
}
"""

class TerminalPage(Gtk.Box):
    def __init__(self, log_fn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._log = log_fn
        scroll, body = make_scrolled_page()
        self._body = body
        self.append(scroll)

        self._build_ptyxis_group(body)
        self._build_shortcuts_group(body)
        self._build_zsh_group(body)
        self._build_fastfetch_group(body)
        self._build_aliases_group(body)
        self._register_terminal_search_rows()

    def _register_terminal_search_rows(self):
        self._terminal_row_by_id = {
            "ptyxis_install": self._row_ptyxis_install,
            "ptyxis_default": self._row_ptyxis_default,
            "shortcut_1": self._row_shortcut_1,
            "shortcut_2": self._row_shortcut_2,
            "zsh_install": self._row_zsh_install,
            "zplug_install": self._row_zplug_install,
            "zsh_default": self._row_zsh_default,
            "fastfetch_install": self._row_fastfetch_install,
            "firacode_install": self._row_font_install,
            "font_apply": self._row_font_apply,
            "ffcfg_install": self._row_ff_config,
            "aliases_add": self._row_aliases,
        }

    def focus_row_by_id(self, row_id: str) -> bool:
        w = self._terminal_row_by_id.get(row_id)
        if w is None:
            return False
        scroll = self.get_first_child()
        if isinstance(scroll, Gtk.ScrolledWindow):
            scroll_child_into_view(scroll, w)
        GLib.idle_add(w.grab_focus)
        return True

    def _build_ptyxis_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Ptyxis")
        group.set_description("Современный терминал GNOME, заменяет gnome-terminal")
        
        btn_all = Gtk.Button(label="Применить всё")
        btn_all.set_valign(Gtk.Align.CENTER)
        btn_all.add_css_class("suggested-action")
        btn_all.connect("clicked", self._on_apply_all)
        group.set_header_suffix(btn_all)
        
        body.append(group)

        self._row_ptyxis_install = SettingRow(
            "utilities-terminal-symbolic", "Установить Ptyxis",
            "epmi ptyxis + удалить gnome-terminal", "Установить",
            self._on_install_ptyxis,
            lambda: backend.check_app_installed({"check": ["which", "ptyxis"]}),
            "term_ptyxis_install", "Установлен",
            self._on_remove_ptyxis, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_ptyxis_install)

        self._row_ptyxis_default = SettingRow(
            "starred-symbolic", "Ptyxis по умолчанию",
            "xdg-mime default org.gnome.Ptyxis.desktop", "Применить",
            self._on_ptyxis_default,
            self._check_ptyxis_default,
            "term_ptyxis_default", "Применено",
            self._on_ptyxis_default_undo, "Сбросить"
        )
        group.add(self._row_ptyxis_default)

    def _on_install_ptyxis(self, row):
        row.set_working()
        self._log("\n▶  Установка Ptyxis...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка Ptyxis...")
        def _done(ok):
            row.set_done(ok)
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        backend.run_privileged(["bash", "-c", "apt-get remove -y gnome-terminal 2>/dev/null || true"], self._log,
            lambda ok: backend.run_epm(["epm", "-i", "ptyxis"], self._log, _done))

    def _on_remove_ptyxis(self, row):
        row.set_working()
        self._log("\n▶  Удаление Ptyxis...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление Ptyxis...")
        backend.run_privileged(["apt-get", "remove", "-y", "ptyxis"], self._log, 
            lambda ok: (row.set_undo_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _check_ptyxis_default(self):
        try:
            r = subprocess.run(
                ["xdg-mime", "query", "default", "x-scheme-handler/terminal"],
                capture_output=True, text=True
            )
            return "org.gnome.Ptyxis.desktop" in r.stdout
        except Exception:
            return False

    def _on_ptyxis_default(self, row):
        row.set_working()
        self._log("\n▶  Назначение Ptyxis терминалом по умолчанию...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Настройка терминала по умолчанию...")
        def _do():
            subprocess.run(["xdg-mime", "default", "org.gnome.Ptyxis.desktop", "x-scheme-handler/terminal"])
            ok = self._check_ptyxis_default()
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  Готово!\n" if ok else "✘  Ошибка\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        threading.Thread(target=_do, daemon=True).start()

    def _on_ptyxis_default_undo(self, row):
        row.set_working()
        self._log("\n▶  Сброс терминала по умолчанию (gnome-terminal)...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Сброс терминала по умолчанию...")
        def _do():
            subprocess.run(["xdg-mime", "default", "org.gnome.Terminal.desktop", "x-scheme-handler/terminal"])
            GLib.idle_add(row.set_undo_done, True)
            GLib.idle_add(self._log, "✔  Сброшено\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()


    def _build_shortcuts_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Горячие клавиши")
        group.set_description("Шорткаты для открытия терминала")
        body.append(group)

        self._row_shortcut_1 = SettingRow(
            "input-keyboard-symbolic", "Terminal 1",
            "Ctrl + Alt + T", "Назначить",
            lambda r: self._set_shortcut(r, "custom0", "Terminal", "ptyxis --new-window", "<Control><Alt>t", "<Primary><Alt>t"),
            lambda: self._check_shortcut("custom0", "<Control><Alt>t"),
            "term_shortcut_1", "Назначен",
            lambda r: self._remove_shortcut(r, "custom0"), "Сбросить"
        )
        group.add(self._row_shortcut_1)

        self._row_shortcut_2 = SettingRow(
            "input-keyboard-symbolic", "Terminal 2",
            "Super + Enter", "Назначить",
            lambda r: self._set_shortcut(r, "custom1", "Terminal Super", "ptyxis --new-window", "<Super>Return"),
            lambda: self._check_shortcut("custom1", "<Super>Return"),
            "term_shortcut_2", "Назначен",
            lambda r: self._remove_shortcut(r, "custom1"), "Сбросить"
        )
        group.add(self._row_shortcut_2)

    def _get_custom_bindings(self):
        val = backend.gsettings_get("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings")
        if not val or val == "@as []":
            return []
        val = val.strip("[]")
        if not val:
            return []
        return [x.strip(" '\"") for x in val.split(",") if x.strip(" '\"")]

    def _check_shortcut(self, uid, binding):
        current_paths = self._get_custom_bindings()
        for path in current_paths:
            if not path.endswith("/"):
                path += "/"
            val = backend.gsettings_get("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:" + path, "binding")
            if binding in val:
                return True
        return False

    def _set_shortcut(self, row, uid, name, cmd, binding, alt_binding=None):
        row.set_working()
        self._log(f"\n▶  Настройка шортката {name}...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress(f"Настройка шортката {name}...")
        def _do():
            path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{uid}/"
            schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:" + path
            
            backend.run_gsettings(["set", schema, "name", f"'{name}'"])
            backend.run_gsettings(["set", schema, "command", f"'{cmd}'"])
            backend.run_gsettings(["set", schema, "binding", f"'{binding}'"])
            
            current = self._get_custom_bindings()
            if path not in current:
                current.append(path)
                array_str = "[" + ", ".join(f"'{p}'" for p in current) + "]"
                backend.run_gsettings(["set", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", array_str])
            
            GLib.idle_add(row.set_done, True)
            GLib.idle_add(self._log, "✔  Шорткат назначен\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()

    def _remove_shortcut(self, row, uid):
        row.set_working()
        self._log(f"\n▶  Удаление шортката...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление шортката...")
        def _do():
            path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{uid}/"
            current = self._get_custom_bindings()
            if path in current:
                current.remove(path)
                array_str = "[" + ", ".join(f"'{p}'" for p in current) + "]"
                backend.run_gsettings(["set", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", array_str])
            
            schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:" + path
            backend.run_gsettings(["reset", schema, "name"])
            backend.run_gsettings(["reset", schema, "command"])
            backend.run_gsettings(["reset", schema, "binding"])

            GLib.idle_add(row.set_undo_done, True)
            GLib.idle_add(self._log, "✔  Шорткат удалён\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()


    def _build_zsh_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("ZSH")
        group.set_description("Устанавливает zsh + git + zplug, делает ZSH shell по умолчанию")
        body.append(group)

        self._row_zsh_install = SettingRow(
            "utilities-terminal-symbolic", "Установить git и zsh",
            "apt-get install -y git zsh", "Установить",
            self._on_install_zsh,
            lambda: backend.check_app_installed({"check": ["which", "zsh"]}),
            "term_zsh_install", "Установлен",
            self._on_remove_zsh, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_zsh_install)

        self._row_zplug_install = SettingRow(
            "utilities-terminal-symbolic", "Установить zplug",
            "git clone https://github.com/zplug/zplug ~/.zplug", "Установить",
            self._on_install_zplug,
            lambda: backend.check_app_installed({"check": ["path", "~/.zplug"]}),
            "term_zplug_install", "Установлен",
            self._on_remove_zplug, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_zplug_install)

        self._row_zsh_default = SettingRow(
            "system-run-symbolic", "ZSH по умолчанию",
            "chsh -s /bin/zsh", "Применить",
            self._on_zsh_default,
            self._check_zsh_default,
            "term_zsh_default", "Применено",
            self._on_zsh_default_undo, "Сбросить"
        )
        group.add(self._row_zsh_default)

    def _on_install_zsh(self, row):
        row.set_working()
        self._log("\n▶  Установка git и zsh...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка ZSH...")
        backend.run_privileged(["apt-get", "install", "-y", "git", "zsh"], self._log, 
            lambda ok: (row.set_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _on_remove_zsh(self, row):
        row.set_working()
        self._log("\n▶  Удаление zsh...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление ZSH...")
        backend.run_privileged(["apt-get", "remove", "-y", "zsh"], self._log, 
            lambda ok: (row.set_undo_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _on_install_zplug(self, row):
        row.set_working()
        self._log("\n▶  Установка zplug...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка zplug...")
        def _do():
            r = subprocess.run(["git", "clone", "https://github.com/zplug/zplug", os.path.expanduser("~/.zplug")], capture_output=True, text=True)
            ok = r.returncode == 0
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, "✔  zplug установлен!\n" if ok else f"✘  Ошибка: {r.stderr}\n")
            if hasattr(win, "stop_progress"): win.stop_progress(ok)
        threading.Thread(target=_do, daemon=True).start()

    def _on_remove_zplug(self, row):
        row.set_working()
        self._log("\n▶  Удаление zplug...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление zplug...")
        def _do():
            shutil.rmtree(os.path.expanduser("~/.zplug"), ignore_errors=True)
            GLib.idle_add(row.set_undo_done, True)
            GLib.idle_add(self._log, "✔  zplug удалён\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()

    def _check_zsh_default(self):
        return os.environ.get("SHELL") == "/bin/zsh"

    def _on_zsh_default(self, row):
        row.set_working()
        self._log("\n▶  Установка ZSH по умолчанию...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка ZSH по умолчанию...")
        user = os.environ.get("USER")
        backend.run_privileged(["chsh", "-s", "/bin/zsh", user], self._log, 
            lambda ok: (row.set_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _on_zsh_default_undo(self, row):
        row.set_working()
        self._log("\n▶  Возврат Bash по умолчанию...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Возврат Bash по умолчанию...")
        user = os.environ.get("USER")
        backend.run_privileged(["chsh", "-s", "/bin/bash", user], self._log, 
            lambda ok: (row.set_undo_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))


    def _build_fastfetch_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Fastfetch + шрифты")
        group.set_description("Системная информация с иконками Nerd Fonts")
        body.append(group)

        self._row_fastfetch_install = SettingRow(
            "dialog-information-symbolic", "Установить Fastfetch",
            "epmi fastfetch", "Установить",
            self._on_install_fastfetch,
            lambda: backend.check_app_installed({"check": ["which", "fastfetch"]}),
            "term_ff_install", "Установлен",
            self._on_remove_fastfetch, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_fastfetch_install)

        self._row_font_install = SettingRow(
            "font-x-generic-symbolic", "Шрифт FiraCode Nerd Font",
            "epmi fonts-ttf-fira-code-nerd", "Установить",
            self._on_install_font,
            lambda: backend.check_app_installed({"check": ["rpm", "fonts-ttf-fira-code-nerd"]}),
            "term_font_install", "Установлен",
            self._on_remove_font, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_font_install)

        self._row_font_apply = SettingRow(
            "font-x-generic-symbolic", "Применить шрифт в Ptyxis",
            "FiraCode Nerd Font Regular 14", "Применить",
            self._on_apply_font,
            self._check_ptyxis_font,
            "term_font_apply", "Применён",
            self._on_apply_font_undo, "Сбросить"
        )
        group.add(self._row_font_apply)

        self._row_ff_config = SettingRow(
            "document-save-symbolic", "Конфиг Fastfetch (Default)",
            "Сохраняет в ~/.config/fastfetch/config.jsonc", "Установить",
            self._on_install_ff_config,
            lambda: backend.check_app_installed({"check": ["path", "~/.config/fastfetch/config.jsonc"]}),
            "term_ff_config", "Установлен",
            self._on_remove_ff_config, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_ff_config)

    def _on_install_fastfetch(self, row):
        row.set_working()
        self._log("\n▶  Установка Fastfetch...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка Fastfetch...")
        backend.run_epm(["epm", "-i", "fastfetch"], self._log, 
            lambda ok: (row.set_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _on_remove_fastfetch(self, row):
        row.set_working()
        self._log("\n▶  Удаление Fastfetch...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление Fastfetch...")
        backend.run_epm(["epm", "-e", "fastfetch"], self._log, 
            lambda ok: (row.set_undo_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _on_install_font(self, row):
        row.set_working()
        self._log("\n▶  Установка шрифта...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Установка шрифта...")
        backend.run_epm(["epm", "-i", "fonts-ttf-fira-code-nerd"], self._log, 
            lambda ok: (row.set_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _on_remove_font(self, row):
        row.set_working()
        self._log("\n▶  Удаление шрифта...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление шрифта...")
        backend.run_epm(["epm", "-e", "fonts-ttf-fira-code-nerd"], self._log, 
            lambda ok: (row.set_undo_done(ok), win.stop_progress(ok) if hasattr(win, "stop_progress") else None))

    def _check_ptyxis_font(self):
        try:
            r = subprocess.run(["dconf", "read", "/org/gnome/Ptyxis/Profiles/default/font-name"], capture_output=True, text=True)
            return "FiraCode Nerd Font Regular 14" in r.stdout
        except Exception:
            return False

    def _on_apply_font(self, row):
        row.set_working()
        self._log("\n▶  Применение шрифта в Ptyxis...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Применение шрифта...")
        def _do():
            subprocess.run(["dconf", "write", "/org/gnome/Ptyxis/Profiles/default/font-name", "'FiraCode Nerd Font Regular 14'"])
            GLib.idle_add(row.set_done, True)
            GLib.idle_add(self._log, "✔  Шрифт применён\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()

    def _on_apply_font_undo(self, row):
        row.set_working()
        self._log("\n▶  Сброс шрифта в Ptyxis...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Сброс шрифта...")
        def _do():
            subprocess.run(["dconf", "reset", "/org/gnome/Ptyxis/Profiles/default/font-name"])
            GLib.idle_add(row.set_undo_done, True)
            GLib.idle_add(self._log, "✔  Шрифт сброшен\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()

    def _on_install_ff_config(self, row):
        row.set_working()
        self._log("\n▶  Создание конфига Fastfetch...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Создание конфига Fastfetch...")
        def _do():
            p = Path(os.path.expanduser("~/.config/fastfetch/config.jsonc"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_FASTFETCH_CONFIG, encoding="utf-8")
            GLib.idle_add(row.set_done, True)
            GLib.idle_add(self._log, "✔  Конфиг создан\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()

    def _on_remove_ff_config(self, row):
        row.set_working()
        self._log("\n▶  Удаление конфига Fastfetch...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление конфига Fastfetch...")
        def _do():
            p = Path(os.path.expanduser("~/.config/fastfetch/config.jsonc"))
            if p.exists():
                p.unlink()
            GLib.idle_add(row.set_undo_done, True)
            GLib.idle_add(self._log, "✔  Конфиг удалён\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()


    def _build_aliases_group(self, body):
        group = Adw.PreferencesGroup()
        group.set_title("Алиасы PLAFON")
        group.set_description("Добавляет набор алиасов в ~/.zshrc")
        body.append(group)

        self._row_aliases = SettingRow(
            "text-editor-symbolic", "Добавить алиасы в .zshrc",
            "Алиасы для epm, flatpak, timeshift, DaVinci и др.", "Добавить",
            self._on_add_aliases,
            self._check_aliases,
            "term_aliases", "Добавлены",
            self._on_remove_aliases, "Удалить", "user-trash-symbolic"
        )
        group.add(self._row_aliases)

    def _check_aliases(self):
        p = Path(os.path.expanduser("~/.zshrc"))
        if not p.exists():
            return False
        try:
            return "# --- ALT Booster Aliases ---" in p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False

    def _on_add_aliases(self, row):
        fd, tmp_path = tempfile.mkstemp(suffix=".sh", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_ALIASES_BLOCK.strip())
        
        editor_cmd = []
        if shutil.which("gnome-text-editor"):
            editor_cmd = ["gnome-text-editor", tmp_path]
        elif shutil.which("gedit"):
            editor_cmd = ["gedit", tmp_path]
        elif shutil.which("nano"):
            term = shutil.which("ptyxis") or shutil.which("gnome-terminal") or shutil.which("kgx")
            if term:
                editor_cmd = [term, "--", "nano", tmp_path]
        
        if not editor_cmd:
            editor_cmd = ["xdg-open", tmp_path]

        try:
            subprocess.Popen(editor_cmd)
        except Exception as e:
            self._log(f"✘ Не удалось открыть редактор: {e}\n")
            return

        dialog = Adw.AlertDialog(
            heading="Редактирование алиасов",
            body="Список алиасов открыт во внешнем редакторе.\nВнесите изменения, сохраните файл и нажмите «Применить».",
        )
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("apply", "Применить")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("apply")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "apply":
                try:
                    text = Path(tmp_path).read_text(encoding="utf-8")
                    self._do_add_aliases(row, text)
                except Exception as e:
                    self._log(f"✘ Ошибка чтения файла: {e}\n")
            
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _do_add_aliases(self, row, text):
        row.set_working()
        self._log("\n▶  Добавление алиасов в .zshrc...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Добавление алиасов...")
        def _do():
            p = Path(os.path.expanduser("~/.zshrc"))
            content = p.read_text(encoding="utf-8") if p.exists() else ""
            
            final_text = text
            if "# --- ALT Booster Aliases ---" not in final_text:
                final_text = "# --- ALT Booster Aliases ---\n" + final_text
            if "# ---------------------------" not in final_text:
                final_text = final_text.strip() + "\n# ---------------------------"
            
            if not final_text.startswith("\n"):
                final_text = "\n" + final_text
            if not final_text.endswith("\n"):
                final_text = final_text + "\n"

            if "# --- ALT Booster Aliases ---" not in content:
                with open(p, "a", encoding="utf-8") as f:
                    f.write(final_text)
            GLib.idle_add(row.set_done, True)
            GLib.idle_add(self._log, "✔  Алиасы добавлены\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()

    def _on_remove_aliases(self, row):
        row.set_working()
        self._log("\n▶  Удаление алиасов из .zshrc...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Удаление алиасов...")
        def _do():
            p = Path(os.path.expanduser("~/.zshrc"))
            if p.exists():
                content = p.read_text(encoding="utf-8")
                if "# --- ALT Booster Aliases ---" in content:
                    new_content = content.replace(_ALIASES_BLOCK, "")
                    if new_content == content:
                        lines = content.splitlines()
                        new_lines = []
                        skip = False
                        for line in lines:
                            if line.strip() == "# --- ALT Booster Aliases ---":
                                skip = True
                            if not skip:
                                new_lines.append(line)
                            if line.strip() == "# ---------------------------":
                                skip = False
                        new_content = "\n".join(new_lines) + "\n"
                    
                    p.write_text(new_content, encoding="utf-8")
            GLib.idle_add(row.set_undo_done, True)
            GLib.idle_add(self._log, "✔  Алиасы удалены\n")
            if hasattr(win, "stop_progress"): win.stop_progress(True)
        threading.Thread(target=_do, daemon=True).start()


    def _on_apply_all(self, btn):
        btn.set_sensitive(False)
        self._log("\n▶  Применение всех настроек терминала...\n")
        win = self.get_root()
        if hasattr(win, "start_progress"): win.start_progress("Настройка терминала...")
        threading.Thread(target=self._do_apply_all, args=(btn,), daemon=True).start()

    def _do_apply_all(self, btn):
        def run_step(row, action_name, sync_fn):
            try:
                if row._check_fn and row._check_fn():
                    GLib.idle_add(row.set_done, True)
                    return True
            except Exception:
                pass
            
            GLib.idle_add(row.set_working)
            GLib.idle_add(self._log, f"▶  {action_name}...\n")
            
            try:
                ok = sync_fn()
            except Exception as e:
                GLib.idle_add(self._log, f"✘  Ошибка: {e}\n")
                ok = False
            
            GLib.idle_add(row.set_done, ok)
            GLib.idle_add(self._log, f"{'✔' if ok else '✘'}  {action_name}\n")
            return ok

        run_step(self._row_ptyxis_install, "Установка Ptyxis", 
            lambda: backend.run_privileged_sync(["bash", "-c", "apt-get remove -y gnome-terminal 2>/dev/null || true && apt-get install -y ptyxis"], self._log))
        
        run_step(self._row_ptyxis_default, "Ptyxis по умолчанию",
            lambda: subprocess.run(["xdg-mime", "default", "org.gnome.Ptyxis.desktop", "x-scheme-handler/terminal"]).returncode == 0)

        def _sync_shortcut(uid, name, cmd, binding):
            path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{uid}/"
            schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:" + path
            backend.run_gsettings(["set", schema, "name", f"'{name}'"])
            backend.run_gsettings(["set", schema, "command", f"'{cmd}'"])
            backend.run_gsettings(["set", schema, "binding", f"'{binding}'"])
            current = self._get_custom_bindings()
            if path not in current:
                current.append(path)
                array_str = "[" + ", ".join(f"'{p}'" for p in current) + "]"
                backend.run_gsettings(["set", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", array_str])
            return True

        run_step(self._row_shortcut_1, "Шорткат Terminal 1",
            lambda: _sync_shortcut("custom0", "Terminal", "ptyxis --new-window", "<Control><Alt>t"))
        
        run_step(self._row_shortcut_2, "Шорткат Terminal 2",
            lambda: _sync_shortcut("custom1", "Terminal Super", "ptyxis --new-window", "<Super>Return"))

        run_step(self._row_zsh_install, "Установка ZSH",
            lambda: backend.run_privileged_sync(["apt-get", "install", "-y", "git", "zsh"], self._log))
        
        run_step(self._row_zplug_install, "Установка zplug",
            lambda: subprocess.run(["git", "clone", "https://github.com/zplug/zplug", os.path.expanduser("~/.zplug")], capture_output=True).returncode == 0)
        
        run_step(self._row_zsh_default, "ZSH по умолчанию",
            lambda: backend.run_privileged_sync(["chsh", "-s", "/bin/zsh", os.environ.get("USER")], self._log))

        run_step(self._row_fastfetch_install, "Установка Fastfetch",
            lambda: backend.run_epm_sync(["epm", "-i", "fastfetch"], self._log))
        
        run_step(self._row_font_install, "Установка шрифта",
            lambda: backend.run_epm_sync(["epm", "-i", "fonts-ttf-fira-code-nerd"], self._log))
        
        run_step(self._row_font_apply, "Применение шрифта",
            lambda: subprocess.run(["dconf", "write", "/org/gnome/Ptyxis/Profiles/default/font-name", "'FiraCode Nerd Font Regular 14'"]).returncode == 0)
        
        def _sync_ff_config():
            p = Path(os.path.expanduser("~/.config/fastfetch/config.jsonc"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_FASTFETCH_CONFIG, encoding="utf-8")
            return True
        run_step(self._row_ff_config, "Конфиг Fastfetch", _sync_ff_config)

        def _sync_aliases():
            p = Path(os.path.expanduser("~/.zshrc"))
            content = p.read_text(encoding="utf-8") if p.exists() else ""
            if "# --- ALT Booster Aliases ---" not in content:
                text = _ALIASES_BLOCK.strip()
                if not text.startswith("\n"): text = "\n" + text
                if not text.endswith("\n"): text = text + "\n"
                with open(p, "a", encoding="utf-8") as f:
                    f.write(text)
            return True
        run_step(self._row_aliases, "Алиасы", _sync_aliases)

        GLib.idle_add(btn.set_sensitive, True)
        GLib.idle_add(self._log, "\n✔  Все настройки терминала применены!\n")
        win = self.get_root()
        if hasattr(win, "stop_progress"): GLib.idle_add(win.stop_progress, True)
