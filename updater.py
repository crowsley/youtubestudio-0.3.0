from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.removeprefix("v").split("."))


def installer_command(path: Path) -> list[str]:
    return [str(path), "/VERYSILENT", "/NORESTART"]


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "AtoZ-Voice-Studio-Updater"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def main() -> int:
    config = read_json(APP_DIR / "update_config.json")
    installed = read_json(APP_DIR / "version.json")["version"]
    asset_name = config["release_asset_name"]
    try:
        release = request_json(f"https://api.github.com/repos/{config['github_owner']}/{config['github_repo']}/releases/latest")
        latest = release["tag_name"].removeprefix("v")
        print(f"Installed: {installed}\nLatest:    {latest}")
        if version_tuple(latest) <= version_tuple(installed):
            input("You already have the latest version. Press Enter to close.")
            return 0
        asset = next((item for item in release.get("assets", []) if item["name"] == asset_name), None)
        if not asset:
            raise RuntimeError(f"Release asset not found: {asset_name}")
        installer = Path(tempfile.gettempdir()) / asset_name
        print("Downloading update...")
        request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "AtoZ-Voice-Studio-Updater"})
        with urllib.request.urlopen(request, timeout=180) as response:
            installer.write_bytes(response.read())
        subprocess.Popen(installer_command(installer))
        print("The app was saved and closed. The update installer has started.")
        return 0
    except Exception as exc:
        input(f"Update failed: {exc}\nPress Enter to close.")
        return 1


if __name__ == "__main__":
    if os.environ.get("ATOZ_VOICE_STUDIO_UPDATER_SMOKE_TEST") == "1":
        marker = os.environ["ATOZ_VOICE_STUDIO_UPDATER_SMOKE_MARKER"]
        Path(marker).write_text(json.dumps({"version": read_json(APP_DIR / "version.json")["version"], "config": read_json(APP_DIR / "update_config.json")["github_repo"]}), encoding="utf-8")
    else:
        raise SystemExit(main())
