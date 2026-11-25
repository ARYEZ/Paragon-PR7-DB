"""Check a real install of PR7-DB-Setup.exe (CI, Windows): verify_install.py installed|uninstalled

`installed`: the files, the Start menu entry, the uninstall entry, Open with for SQLite files (the ticked task) starting
the installed exe, and the installed exe's --selftest: a real database created, filled, edited, written, reverted,
queried, exported and imported. `uninstalled`: all of it gone, the Open with entries too."""
import json
import os
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

HKCU = winreg.HKEY_CURRENT_USER
APP = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "PR7-DB"
SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Paragon PR7-DB.lnk"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{59D3D2F2-BE74-4683-B47A-BEE9B3265EC5}_is1"
PROGID = "Paragon.PR7DB.SQLite"
COMMAND = r"Software\Classes\Paragon.PR7DB.SQLite\shell\open\command"


def value(root, path, name=""):
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def check(ok, what, p):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        p.append(what)


def installed():
    p = []
    exe = APP / "PR7-DB.exe"
    for f in (exe, APP / "unins000.exe", APP / "_internal" / "pr7db" / "fonts" / "Inter-Regular.ttf"):
        check(f.is_file(), f"file {f}", p)
    check(SHORTCUT.is_file(), "Start menu shortcut", p)
    check(value(HKCU, UNINSTALL, "DisplayName") == "Paragon PR7-DB", "Installed apps entry", p)
    cmd = value(HKCU, COMMAND) or ""
    check(str(exe).lower() in cmd.lower() and cmd.endswith('"%1"'), f"SQLite files open with the installed exe ({cmd})", p)
    for ext in ("db", "sqlite", "sqlite3"):
        check(value(HKCU, rf"Software\Classes\.{ext}\OpenWithProgids", PROGID) is not None, f".{ext} Open with lists PR7-DB", p)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.json"
        try:
            subprocess.run([str(exe), "--selftest", str(out)], timeout=600, check=False)
        except subprocess.TimeoutExpired:
            pass
        got = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        check(got.get("ok") is True and got.get("fonts") is True,
              f"the installed exe creates, edits, writes, reverts, queries, imports and exports ({got})", p)
    return p


def uninstalled():
    deadline = time.time() + 60
    while True:
        p = []
        check(not (APP / "PR7-DB.exe").exists(), "program files removed", p)
        check(not SHORTCUT.exists(), "Start menu shortcut removed", p)
        check(value(HKCU, UNINSTALL, "DisplayName") is None, "Installed apps entry removed", p)
        check(value(HKCU, COMMAND) is None, "Open with entries removed", p)
        if not p or time.time() > deadline:
            return p
        print("... waiting for the uninstaller to finish")
        time.sleep(3)


if __name__ == "__main__":
    problems = {"installed": installed, "uninstalled": uninstalled}[sys.argv[1]]()
    print("\nALL OK" if not problems else f"\n{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
