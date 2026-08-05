from updater import version_tuple


def test_version_ordering() -> None:
    assert version_tuple("v0.3.0") == (0, 3, 0)
    assert version_tuple("0.10.0") > version_tuple("0.9.9")


if __name__ == "__main__":
    test_version_ordering()
    print("Updater checks passed.")
