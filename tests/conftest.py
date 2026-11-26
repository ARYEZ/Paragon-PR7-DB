import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PARAGON_FAMILY_FILE"] = os.path.join(tempfile.mkdtemp(prefix="family-"), "finish")  # never the real choice
os.environ["PR7DB_DATA"] = tempfile.mkdtemp(prefix="pr7db-data-")      # never the real data folder
os.environ["PR7DB_TRASH"] = tempfile.mkdtemp(prefix="pr7db-trash-")    # never the real Recycle Bin


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)           # never the real settings
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("settings")))
    app = QApplication.instance() or QApplication([])
    from pr7db import theme
    theme.load_fonts()
    yield app
    app.clipboard().clear()


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    """The real window; every question answered by `answers`, comparing inline."""
    from pr7db import settings
    from pr7db.main_window import MainWindow
    monkeypatch.setenv("PR7DB_TRASH", str(tmp_path / "trash"))
    for k in ("recent", "last_sql", "foreign_keys"):
        settings.set(k, "")
    w = MainWindow(sync=True)
    said = []
    w.said = said
    w.answers.update({"say": lambda kind, text: said.append((kind, text)), "confirm": lambda t: True,
                      "save_changes": "discard", "unwritten": "discard", "menu": True})
    w.resize(1200, 760)
    w.show()
    yield w
    w.answers["save_changes"] = "discard"
    w.close()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del w
    import gc
    gc.collect()          # on the main thread now (PR7-U's lesson)


# Qt's own teardown of its global objects at interpreter exit sometimes aborts (Windows CI, Linux too) after every test has passed
# (CI: "Fatal Python error: Aborted", no Python frame). Once pytest has printed its summary, leave with its exit code
# and skip that teardown.
_status = {}


def pytest_sessionfinish(session, exitstatus):
    _status["code"] = int(exitstatus)


def pytest_unconfigure(config):
    if "code" in _status:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_status["code"])
