import json
import os
import sqlite3
import subprocess
import sys
import time

from pr7db import app


def test_a_second_launch_hands_over(window, tmp_path, qapp):
    """A second PR7-DB (its own process, as Explorer starts it) opens its database in this window."""
    p = tmp_path / "x.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (a)")
    con.commit()
    con.close()
    assert app.listen(window) is not None
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from PySide6.QtCore import QCoreApplication; "
            "q = QCoreApplication([]); from pr7db import app; "
            "sys.exit(0 if app.hand_over({'files': sys.argv[2:]}, 3000) else 1)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen([sys.executable, "-c", code, root, str(p)])
    deadline = time.time() + 20
    while (window.db is None or proc.poll() is None) and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert proc.wait(5) == 0
    assert window.db is not None and window.db.path == str(p)
    window.instance_server.close()


def test_selftest(tmp_path):
    out = tmp_path / "out.json"
    assert app.selftest(str(out)) == 0
    r = json.loads(out.read_text())
    assert r["ok"] and r["sum"] == 7.5 and r["copy_rows"] == 3
