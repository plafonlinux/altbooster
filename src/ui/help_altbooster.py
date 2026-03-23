
import subprocess
from pathlib import Path

_INSTALLED_DIR = Path("/usr/local/share/help/C/altbooster")
_LOCAL_DIR = Path(__file__).parent.parent.parent / "help" / "C"


def show_help(window, page=None):
    base = _INSTALLED_DIR if _INSTALLED_DIR.exists() else _LOCAL_DIR
    filename = f"{page}.page" if page else "index.page"
    target = base / filename
    if not target.exists():
        target = base / "index.page"
    subprocess.Popen(["yelp", str(target)])
