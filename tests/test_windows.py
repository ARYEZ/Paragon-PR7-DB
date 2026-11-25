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
    try:
        shell.register()
        assert shell.registered()
        assert value(rf"Software\Classes\{shell.PROGID}\shell\open\command").endswith('"%1"')
        for ext in shell.EXTS:
            assert value(rf"Software\Classes\.{ext}\OpenWithProgids", shell.PROGID) is not None
    finally:
        shell.unregister()
    assert not shell.registered() and value(rf"Software\Classes\{shell.PROGID}") is None
