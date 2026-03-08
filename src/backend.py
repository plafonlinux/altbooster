"""
backend.py — точка входа для доступа к системным функциям.
"""

from system.privileges import (
    set_sudo_password,
    get_sudo_password,
    set_sudo_nopass,
    set_pkexec_mode,
    start_pkexec_shell,
    sudo_check,
    run_privileged,
    run_privileged_sync,
    run_epm,
    run_epm_sync,
    OnLine,
    OnDone,
)
from system.gsettings import run_gsettings, gsettings_get
from system.checks import (
    is_sudo_enabled,
    is_flathub_enabled,
    is_fstrim_enabled,
    is_fractional_scaling_enabled,
    is_system_busy,
    check_app_installed,
    is_vm_dirty_optimized,
    is_drive_menu_patched,
    is_journal_optimized,
    is_davinci_installed,
    is_aac_installed,
    is_fairlight_installed,
    is_epm_installed,
)
from system.tweaks import (
    apply_vm_dirty,
    patch_drive_menu,
    install_aac_codec,
)

__all__ = [
    "set_sudo_password",
    "get_sudo_password",
    "set_sudo_nopass",
    "set_pkexec_mode",
    "start_pkexec_shell",
    "sudo_check",
    "run_privileged",
    "run_privileged_sync",
    "run_epm",
    "run_epm_sync",
    "OnLine",
    "OnDone",
    "run_gsettings",
    "gsettings_get",
    "is_sudo_enabled",
    "is_flathub_enabled",
    "is_fstrim_enabled",
    "is_fractional_scaling_enabled",
    "is_system_busy",
    "check_app_installed",
    "is_vm_dirty_optimized",
    "is_drive_menu_patched",
    "is_journal_optimized",
    "is_davinci_installed",
    "is_aac_installed",
    "is_fairlight_installed",
    "is_epm_installed",
    "apply_vm_dirty",
    "patch_drive_menu",
    "install_aac_codec",
]
