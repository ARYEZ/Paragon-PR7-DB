"""Screenshots of the real PR7-DB window on a made-up studio database: QT_SCALE_FACTOR=2 python tools/screenshots.py OUT

browse.png      Browse Data, a column filtered, a cell changed and not written yet (OBSIDIAN)
sql.png         Execute SQL with results (AURORA)
structure.png   Database Structure (OBSIDIAN)
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["PARAGON_FAMILY_FILE"] = os.path.join(tempfile.mkdtemp(), "finish")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])

from pr7db import theme  # noqa: E402

theme.load_fonts()
app.setFont(QFont("Inter", 10))
import mockup_data  # noqa: E402
from pr7db.main_window import MainWindow  # noqa: E402

path = str(Path(tempfile.mkdtemp()) / "studio.db")
mockup_data.studio(path).close()

SQL = """-- the biggest projects by footage, with how much each camera shot
SELECT p.title, f.camera, COUNT(*) AS clips, ROUND(SUM(f.size_mb) / 1024.0, 2) AS gb
FROM footage f
JOIN projects p ON p.id = f.project_id
WHERE f.camera IS NOT NULL
GROUP BY p.title, f.camera
ORDER BY gb DESC
LIMIT 12"""


def shoot(w, name):
    w.show()
    for _ in range(6):
        app.processEvents()
    w.grab().save(str(out / name))


theme.set_finish("obsidian")
w = MainWindow(sync=True)
w.answers.update({"say": lambda k, t: None, "confirm": True, "unwritten": "discard"})
w.resize(1320, 820)
w.restyle()
w.open_database(path)
w.table_combo.setCurrentText("footage")
w.filter_header.boxes[3].setText("ARRI")
w.model.set_value(2, 3, "ARRI Alexa 35 (B cam)")
w.grid.setCurrentIndex(w.model.index(2, 3))
w._sync()
shoot(w, "browse.png")
w.set_finish("aurora")
w.editor.setPlainText(SQL)
w.run_all()
shoot(w, "sql.png")
w.set_finish("obsidian")
w.tabs.setCurrentIndex(0)
w.tree.topLevelItem(0).child(0).setExpanded(True)
w.tree.topLevelItem(0).child(1).setExpanded(True)
shoot(w, "structure.png")
w.close()
print("ok", out)
