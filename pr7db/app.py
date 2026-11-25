"""Start PR7-DB.

    PR7-DB.exe [DATABASE]              the window with the database open (one window per user: a later start hands
                                       its database to the running window, which asks about unwritten changes first)
    PR7-DB.exe --register              list PR7-DB under Open with for SQLite files (HKCU); --unregister removes it
    PR7-DB.exe --version
    PR7-DB.exe --selftest OUT.json     create, fill, edit, write, revert, query, import and export a real database
                                       offscreen (CI runs the installed exe)
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
import tempfile

TIMEOUT_MS = 1500


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "Paragon-PR7-DB-" + hashlib.sha1(user.encode()).hexdigest()[:12]


def hand_over(message: dict, wait_ms: int = 0) -> bool:
    """Give a message to the running window. With wait_ms, keep trying that long (it may still be starting)."""
    from PySide6.QtCore import QElapsedTimer, QThread
    from PySide6.QtNetwork import QLocalSocket
    clock = QElapsedTimer()
    clock.start()
    while True:
        s = QLocalSocket()
        s.connectToServer(server_name())
        if s.waitForConnected(TIMEOUT_MS):
            s.write(json.dumps(message).encode() + b"\n")
            s.flush()
            s.waitForBytesWritten(TIMEOUT_MS)
            if not s.waitForDisconnected(TIMEOUT_MS):
                s.disconnectFromServer()
            return True
        if clock.elapsed() >= wait_ms:
            return False
        QThread.msleep(100)


def listen(window):
    """Later starts hand their files over (PR7-R's hand-off, with its Windows fix: data already waiting when the
    connection is accepted is read at once)."""
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer(window)
    QLocalServer.removeServer(server_name())
    if not server.listen(server_name()):
        return None

    def connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        buf = bytearray()

        def read():
            buf.extend(bytes(sock.readAll()))
            if b"\n" not in buf:
                return
            try:
                msg = json.loads(bytes(buf).split(b"\n", 1)[0])
            except ValueError:
                msg = {}
            sock.disconnectFromServer()
            receive(window, msg)
        sock.readyRead.connect(read)
        if sock.bytesAvailable():
            read()
    server.newConnection.connect(connection)
    window.instance_server = server
    return server


def receive(window, msg: dict):
    window.showNormal()
    window.raise_()
    window.activateWindow()
    files = [f for f in msg.get("files", []) if f]
    if files:
        window.open_database(files[0])


def selftest(out: str) -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    app = QApplication.instance() or QApplication(sys.argv[:1])
    result: dict = {}
    try:
        import sqlite3

        from . import __version__, theme
        from .main_window import MainWindow
        theme.load_fonts()
        d = tempfile.mkdtemp()
        path = os.path.join(d, "selftest.db")

        def on_disk(sql):
            con = sqlite3.connect(path)
            try:
                return con.execute(sql).fetchall()
            finally:
                con.close()

        w = MainWindow(sync=True)
        w.answers.update({"say": lambda k, t: None, "confirm": True, "unwritten": "discard", "new_file": path})

        class Table:                                      # what Create Table's dialog gives
            def __init__(self):
                from PySide6.QtWidgets import QCheckBox, QLineEdit
                self.name, self.without_rowid = QLineEdit("clips"), QCheckBox()

            def columns(self):
                return [{"name": "id", "type": "INTEGER", "pk": True}, {"name": "name", "type": "TEXT"},
                        {"name": "size", "type": "REAL"}]
        w.answers["table"] = Table()
        w.show()
        created = w.new_database()
        w.editor.setPlainText("INSERT INTO clips (name, size) VALUES ('a', 1.5), ('b', 2.5), ('c', 3.5);")
        w.run_all()
        w.write()
        filled = on_disk("SELECT count(*) FROM clips") == [(3,)]
        w.browse("clips")
        w.model.setData(w.model.index(0, 1), "renamed")
        w.write()
        edited = on_disk("SELECT name FROM clips WHERE id = 1") == [("renamed",)]
        w.model.setData(w.model.index(1, 1), "not kept")
        w.db.revert()
        reverted = on_disk("SELECT name FROM clips WHERE id = 2") == [("b",)]
        w.editor.setPlainText("SELECT sum(size) FROM clips")
        r = w.run_all()
        total = w.result_model.rows[0][0] if r and w.result_model and w.result_model.rows else None
        csv_path = os.path.join(d, "clips.csv")
        w.answers["csv"] = {"path": csv_path, "table": "clips", "header": True, "sep": ",", "quote": '"'}
        exported = w.export_csv()
        w.answers["csv"] = {"path": csv_path, "table": "copy", "header": True, "sep": ",", "quote": '"'}
        imported = w.import_csv()
        w.write()
        integrity = w.db.integrity_check()
        result = {"version": __version__, "sqlite": sqlite3.sqlite_version, "created": created, "filled": filled,
                  "edited": edited, "reverted": reverted, "sum": total, "exported": exported, "imported": imported,
                  "copy_rows": on_disk("SELECT count(*) FROM copy")[0][0], "integrity": integrity,
                  "fonts": "Inter" in QFontDatabase.families()}
        result["ok"] = (created and filled and edited and reverted and total == 7.5 and exported == 3 and
                        imported == 3 and integrity == ["ok"])
        w.close()
    except Exception as e:  # noqa: BLE001 - reported in the output
        import traceback
        result = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    app.clipboard().clear()
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or not argv[0].startswith("--"):
        return _window(argv)
    try:
        return _command(argv)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - reported, then a failing exit code
        import traceback
        if sys.stderr is not None:
            sys.stderr.write(traceback.format_exc())
        return 2


def _command(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        from . import __version__
        print(__version__)
        return 0
    if argv[:1] == ["--selftest"] and len(argv) == 2:
        return selftest(argv[1])
    if argv[:1] == ["--register"]:
        from . import shell
        shell.register()
        return 0
    if argv[:1] == ["--unregister"]:
        from . import shell
        shell.unregister()
        return 0
    print(__doc__)
    return 1


def _lock():
    """The first PR7-DB of this user holds this lock while it starts the window; later ones hand over to it."""
    from PySide6.QtCore import QLockFile
    lock = QLockFile(os.path.join(tempfile.gettempdir(), server_name() + ".lock"))
    lock.setStaleLockTime(0)
    return lock


def _window(argv: list[str]) -> int:
    from PySide6.QtWidgets import QApplication

    from . import settings, theme
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("Paragon")
    app.setApplicationName("PR7-DB")
    files = [os.path.abspath(a) for a in argv]
    lock = _lock()
    if not lock.tryLock(0):
        if hand_over({"files": files}, wait_ms=8000):
            return 0
        lock.removeStaleLockFile()
        lock.tryLock(0)
    elif hand_over({"files": files}):
        return 0
    from . import finish
    theme.set_finish(finish.start(settings.get("finish", "obsidian")))
    theme.load_fonts()
    app.setPalette(theme.palette())
    from PySide6.QtGui import QIcon
    icon = os.path.join(os.path.dirname(__file__), "icons", "app.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    from .main_window import MainWindow
    w = MainWindow()
    from PySide6.QtCore import QTimer
    _follow = QTimer(w)                      # one finish for the family: follow another app's switch (finish.py)
    _follow.timeout.connect(lambda: finish.follow(theme.FINISH, w.set_finish))
    _follow.start(2000)
    listen(w)
    w.show()
    if files:
        QTimer.singleShot(0, lambda: w.open_database(files[0]))
    from . import update_ui
    update_ui.start(w)
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
