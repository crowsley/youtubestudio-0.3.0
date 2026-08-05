from pathlib import Path

from updater import installer_command, version_tuple


def test_version_ordering() -> None:
    assert version_tuple("v0.3.0") == (0, 3, 0)
    assert version_tuple("0.10.0") > version_tuple("0.9.9")


def test_installer_handoff() -> None:
    assert installer_command(Path("update.exe")) == ["update.exe", "/VERYSILENT", "/NORESTART"]


if __name__ == "__main__":
    test_version_ordering()
    test_installer_handoff()
    print("Updater checks passed.")
