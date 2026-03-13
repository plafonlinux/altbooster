
from typing import Callable

from builtin_actions.terminal import (
    check_ptyxis_default,
    set_ptyxis_default,
    check_shortcut_1,
    set_shortcut_1,
    check_shortcut_2,
    set_shortcut_2,
    check_zsh_default,
    set_zsh_default,
    install_zplug,
    check_ptyxis_font,
    install_fastfetch_config,
    check_zsh_aliases,
    add_zsh_aliases,
)
from builtin_actions.amd import (
    check_overclock,
    enable_overclock,
    check_wheel,
    setup_lact_wheel,
    apply_lact_config,
    confirm_reboot,
)

BUILTIN_REGISTRY: dict[str, Callable] = {
    "check_ptyxis_default":     check_ptyxis_default,
    "set_ptyxis_default":       set_ptyxis_default,
    "check_shortcut_1":         check_shortcut_1,
    "set_shortcut_1":           set_shortcut_1,
    "check_shortcut_2":         check_shortcut_2,
    "set_shortcut_2":           set_shortcut_2,
    "check_zsh_default":        check_zsh_default,
    "set_zsh_default":          set_zsh_default,
    "install_zplug":            install_zplug,
    "check_ptyxis_font":        check_ptyxis_font,
    "install_fastfetch_config": install_fastfetch_config,
    "check_zsh_aliases":        check_zsh_aliases,
    "add_zsh_aliases":          add_zsh_aliases,
    "check_overclock":          check_overclock,
    "enable_overclock":         enable_overclock,
    "check_wheel":              check_wheel,
    "setup_lact_wheel":         setup_lact_wheel,
    "apply_lact_config":        apply_lact_config,
    "confirm_reboot":           confirm_reboot,
}

