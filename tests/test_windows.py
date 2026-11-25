"""Windows CI only: Open with for SQLite files written into the real HKCU and removed again."""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not os.environ.get("CI"), reason="Windows CI only")


def test_open_with_registers_and_unregisters():
    import winreg

    from pr7db import shell

    def value(path, name=""):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None
    def exists(path, name):
        """Open with's entries are empty REG_NONE values: they read back as None, so ask whether they exist."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                winreg.QueryValueEx(k, name)
                return True
        except OSError:
            return False
    try:
        shell.register()
        assert shell.registered()
        assert value(rf"Software\Classes\{shell.PROGID}\shell\open\command").endswith('"%1"')
        for ext in shell.EXTS:
            assert exists(rf"Software\Classes\.{ext}\OpenWithProgids", shell.PROGID)
    finally:
        shell.unregister()
    assert not shell.registered() and value(rf"Software\Classes\{shell.PROGID}") is None
    assert not exists(r"Software\Classes\.db\OpenWithProgids", shell.PROGID)
