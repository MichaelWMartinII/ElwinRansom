#!/usr/bin/env python3
"""Install launchd agents for Elwin Ransom (server + Telegram bot).

Usage:
    python install.py             # install (or reinstall) both agents
    python install.py --uninstall # stop and remove both agents
"""

import argparse
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"

_SERVER_LABEL = "com.elwin.server"
_BOT_LABEL = "com.elwin.bot"
_VISION_LABEL = "com.elwin.vision"
_WEBAPP_LABEL = "com.elwin.webapp"
_SERVER_PLIST = LAUNCH_AGENTS / f"{_SERVER_LABEL}.plist"
_BOT_PLIST = LAUNCH_AGENTS / f"{_BOT_LABEL}.plist"
_VISION_PLIST = LAUNCH_AGENTS / f"{_VISION_LABEL}.plist"
_WEBAPP_PLIST = LAUNCH_AGENTS / f"{_WEBAPP_LABEL}.plist"


def _launchctl(cmd: str, plist: Path) -> bool:
    try:
        subprocess.run(
            ["launchctl", cmd, str(plist)],
            check=True, capture_output=True, timeout=5,
        )
        return True
    except Exception as e:
        print(f"  [WARN] launchctl {cmd} failed: {e}")
        return False


def install_server() -> bool:
    """Install the llama-server LaunchAgent."""
    if _SERVER_PLIST.exists():
        _launchctl("unload", _SERVER_PLIST)

    plist_data = {
        "Label": _SERVER_LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-is",
            "/bin/bash", str(ROOT / "start.sh"),
        ],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"/tmp/{_SERVER_LABEL}.log",
        "StandardErrorPath": f"/tmp/{_SERVER_LABEL}.log",
    }

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    with open(_SERVER_PLIST, "wb") as f:
        plistlib.dump(plist_data, f)

    return _launchctl("load", _SERVER_PLIST)


def install_bot() -> bool:
    """Install the Telegram bot LaunchAgent."""
    if not VENV_PYTHON.exists():
        print(f"  [ERROR] venv Python not found: {VENV_PYTHON}")
        print("  Run: pip install -r requirements.txt first")
        return False

    if _BOT_PLIST.exists():
        _launchctl("unload", _BOT_PLIST)

    plist_data = {
        "Label": _BOT_LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-is",
            str(VENV_PYTHON), "-m", "companion.telegram_bot",
        ],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"/tmp/{_BOT_LABEL}.log",
        "StandardErrorPath": f"/tmp/{_BOT_LABEL}.log",
    }

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    with open(_BOT_PLIST, "wb") as f:
        plistlib.dump(plist_data, f)

    return _launchctl("load", _BOT_PLIST)


def install_vision() -> bool:
    """Install the vision server LaunchAgent."""
    if _VISION_PLIST.exists():
        _launchctl("unload", _VISION_PLIST)

    plist_data = {
        "Label": _VISION_LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-is",
            "/bin/bash", str(ROOT / "start-vision.sh"),
        ],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"/tmp/{_VISION_LABEL}.log",
        "StandardErrorPath": f"/tmp/{_VISION_LABEL}.log",
    }

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    with open(_VISION_PLIST, "wb") as f:
        plistlib.dump(plist_data, f)

    return _launchctl("load", _VISION_PLIST)


def uninstall_server() -> None:
    _launchctl("unload", _SERVER_PLIST)
    _SERVER_PLIST.unlink(missing_ok=True)
    print(f"  Removed {_SERVER_LABEL}")


def uninstall_bot() -> None:
    _launchctl("unload", _BOT_PLIST)
    _BOT_PLIST.unlink(missing_ok=True)
    print(f"  Removed {_BOT_LABEL}")


def install_webapp() -> bool:
    """Install the web UI LaunchAgent."""
    if not VENV_PYTHON.exists():
        print(f"  [ERROR] venv Python not found: {VENV_PYTHON}")
        print("  Run: pip install -r requirements.txt first")
        return False

    if _WEBAPP_PLIST.exists():
        _launchctl("unload", _WEBAPP_PLIST)

    plist_data = {
        "Label": _WEBAPP_LABEL,
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-is",
            str(VENV_PYTHON), "-m", "companion.webapp",
        ],
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"/tmp/{_WEBAPP_LABEL}.log",
        "StandardErrorPath": f"/tmp/{_WEBAPP_LABEL}.log",
    }

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    with open(_WEBAPP_PLIST, "wb") as f:
        plistlib.dump(plist_data, f)

    return _launchctl("load", _WEBAPP_PLIST)


def uninstall_vision() -> None:
    _launchctl("unload", _VISION_PLIST)
    _VISION_PLIST.unlink(missing_ok=True)
    print(f"  Removed {_VISION_LABEL}")


def uninstall_webapp() -> None:
    _launchctl("unload", _WEBAPP_PLIST)
    _WEBAPP_PLIST.unlink(missing_ok=True)
    print(f"  Removed {_WEBAPP_LABEL}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Elwin Ransom launchd agents")
    parser.add_argument("--uninstall", action="store_true",
                        help="Stop and remove both agents")
    args = parser.parse_args()

    if args.uninstall:
        print("Removing launchd agents...")
        uninstall_server()
        uninstall_vision()
        uninstall_bot()
        uninstall_webapp()
        print("Done.")
        return

    print("Installing launchd agents...")
    print()

    ok_server = install_server()
    print(f"  {'✓' if ok_server else '✗'} Server  ({_SERVER_LABEL})")

    ok_vision = install_vision()
    print(f"  {'✓' if ok_vision else '✗'} Vision  ({_VISION_LABEL})")

    ok_bot = install_bot()
    print(f"  {'✓' if ok_bot else '✗'} Bot     ({_BOT_LABEL})")

    ok_webapp = install_webapp()
    print(f"  {'✓' if ok_webapp else '✗'} Web UI  ({_WEBAPP_LABEL})")

    print()
    print("Logs:")
    print(f"  Server:  /tmp/{_SERVER_LABEL}.log")
    print(f"  Vision:  /tmp/{_VISION_LABEL}.log")
    print(f"  Bot:     /tmp/{_BOT_LABEL}.log")
    print(f"  Web UI:  /tmp/{_WEBAPP_LABEL}.log")
    print()
    print("Recommended — prevent system sleep on AC power (run once):")
    print("  sudo pmset -c sleep 0 disksleep 0")
    print()
    print("To uninstall:")
    print("  python install.py --uninstall")


if __name__ == "__main__":
    main()
