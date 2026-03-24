import sys
import types
from pathlib import Path


def _ensure_src_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _install_gi_stub() -> None:
    if "gi" in sys.modules:
        return
    gi_module = types.ModuleType("gi")
    repository_module = types.ModuleType("gi.repository")
    glib_module = types.ModuleType("GLib")

    def idle_add(func, *args, **kwargs):
        return func(*args, **kwargs)

    glib_module.idle_add = idle_add
    repository_module.GLib = glib_module

    def require_version(*_args, **_kwargs):
        return None

    gi_module.require_version = require_version
    gi_module.repository = repository_module

    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = repository_module
    sys.modules["gi.repository.GLib"] = glib_module


_ensure_src_on_path()
_install_gi_stub()
