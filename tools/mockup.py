"""Mock-up pictures of PR7-DB, drawn offscreen in the family theme from a real SQLite database (made-up studio data):
QT_SCALE_FACTOR=2 python tools/mockup.py OUT_DIR

1_browse.png     Browse Data: a table with a filter under each column, NULLs dim, an edited row (orange, not written
                 yet), the Edit Database Cell panel (OBSIDIAN)
2_sql.png        Execute SQL: a query with the family's highlighting, its results and how long it took (AURORA)
3_structure.png  Database Structure: tables with their columns and types, indices, views, triggers and each CREATE
                 statement (OBSIDIAN)
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,  # noqa: E402
                               QLineEdit, QPlainTextEdit, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from pr7db import theme, widgets  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/mockup")
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
theme.load_fonts()
app.setFont(QFont("Inter", 10))

CHANGED = "#F07A2A"


def style():
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())


def dim(text):
    lab = QLabel(text)
    lab.setProperty("role", "dim")
    return lab


def field(text):
    lab = QLabel(text.upper())
    lab.setProperty("role", "field")
    return lab


def mono(px=13):
    f = QFont("DejaVu Sans Mono")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(px)
    return f


# ------------------------------------------------------------------ a real database
db = sqlite3.connect(":memory:")
db.executescript("""
CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT NOT NULL, client TEXT, started DATE, budget REAL,
                       status TEXT DEFAULT 'open');
CREATE TABLE footage (id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id), clip TEXT NOT NULL,
                      camera TEXT, duration_s REAL, size_mb INTEGER, thumbnail BLOB);
CREATE TABLE renders (id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id), take INTEGER,
                      version INTEGER, codec TEXT, rendered_at TEXT);
CREATE INDEX footage_project ON footage(project_id);
CREATE UNIQUE INDEX renders_take ON renders(project_id, take, version);
CREATE VIEW project_sizes AS SELECT p.title, COUNT(f.id) AS clips, SUM(f.size_mb) AS total_mb
                             FROM projects p LEFT JOIN footage f ON f.project_id = p.id GROUP BY p.id;
CREATE TRIGGER close_finished AFTER INSERT ON renders WHEN NEW.codec = 'master'
  BEGIN UPDATE projects SET status = 'done' WHERE id = NEW.project_id; END;
""")
projects = [("ELSEWORLD", "Northlight Pictures", "2026-03-02", 184000.0, "open"),
            ("Last Light", "Harbour Records", "2026-05-17", 42500.0, "open"),
            ("Saltwater", None, "2025-11-30", 97000.0, "done"),
            ("Paper Moons", "Northlight Pictures", "2026-08-09", None, "open")]
db.executemany("INSERT INTO projects (title, client, started, budget, status) VALUES (?,?,?,?,?)", projects)
cams = ["ARRI Alexa 35", "RED V-Raptor", "Sony FX6", None]
for i in range(1, 241):
    db.execute("INSERT INTO footage (project_id, clip, camera, duration_s, size_mb, thumbnail) VALUES (?,?,?,?,?,?)",
               (1 + i % 4, f"A{1 + i // 40:03d}_C{i:04d}.mov", cams[(i // 3) % 4], round(4 + (i * 37 % 300) / 3.1, 1),
                120 + i * 53 % 4100, b"\x89PNG\r\n\x1a\n" if i % 3 else None))
for t in range(1, 9):
    db.execute("INSERT INTO renders (project_id, take, version, codec, rendered_at) VALUES (1, ?, 1, ?, ?)",
               (t, "prores_4444" if t % 2 else "hevc_10bit", f"2026-11-{10 + t:02d} 2{t % 4}:14"))


class Sql(QSyntaxHighlighter):
    """SQL in the family's colours: keywords orange, strings pink, numbers pearl, comments dim."""

    KEYWORDS = ("SELECT FROM WHERE JOIN LEFT ON GROUP BY ORDER AS COUNT SUM ROUND AND OR NOT NULL DESC ASC LIMIT "
                "HAVING IS IN LIKE").split()

    def highlightBlock(self, text):
        import re
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(theme.LED_ORANGE))
        fmt.setFontWeight(QFont.Weight.DemiBold)
        for m in re.finditer(r"\b(" + "|".join(self.KEYWORDS) + r")\b", text, re.I):
            self.setFormat(m.start(), m.end() - m.start(), fmt)
        s = QTextCharFormat()
        s.setForeground(QColor("#D6457E"))
        for m in re.finditer(r"'[^']*'", text):
            self.setFormat(m.start(), m.end() - m.start(), s)
        n = QTextCharFormat()
        n.setForeground(QColor(theme.TEXT))
        c = QTextCharFormat()
        c.setForeground(QColor(theme.TEXT_DIM))
        c.setFontItalic(True)
        i = text.find("--")
        if i >= 0:
            self.setFormat(i, len(text) - i, c)


def grid(cur, rows, changed=(), null_dim=True, filters=None, stretch=True):
    cols = [d[0] for d in cur.description]
    t = QTableWidget(len(rows) + (1 if filters is not None else 0), len(cols))
    t.setHorizontalHeaderLabels([c.upper() for c in cols])
    t.verticalHeader().setDefaultSectionSize(26)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    off = 0
    if filters is not None:
        off = 1
        t.setVerticalHeaderLabels([""] + [str(i + 1) for i in range(len(rows))])
        for c in range(len(cols)):
            e = QLineEdit(filters.get(cols[c], ""))
            e.setPlaceholderText("Filter")
            e.setStyleSheet("padding: 2px 6px; min-height: 14px;")
            t.setCellWidget(0, c, e)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                it = QTableWidgetItem("NULL")
                it.setForeground(QColor(theme.TEXT_DIM))
                f = it.font()
                f.setItalic(True)
                it.setFont(f)
            elif isinstance(v, bytes):
                it = QTableWidgetItem("BLOB")
                it.setForeground(QColor(theme.TEXT_DIM))
            else:
                it = QTableWidgetItem(f"{v:,.1f}" if isinstance(v, float) else str(v))
                if isinstance(v, (int, float)):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if (r, c) in changed:
                it.setForeground(QColor(CHANGED))
            t.setItem(r + off, c, it)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    h.setStretchLastSection(stretch)
    return t


def toolbar():
    bar = QWidget()
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(16, 10, 16, 6)
    lay.setSpacing(6)
    groups = [[("add", "New"), ("open", "Open"), ("save", "Write Changes"), ("undo", "Revert")],
              [("table", "New Table"), ("play", "Execute")],
              [("import", "Import"), ("export", "Export"), ("gear", "Options")]]
    for i, group in enumerate(groups):
        if i:
            sep = QLabel()
            sep.setFixedSize(1, 40)
            sep.setStyleSheet(f"background: {theme.PANEL_EDGE};")
            lay.addSpacing(8)
            lay.addWidget(sep)
            lay.addSpacing(8)
        for icon, label in group:
            k = widgets.KeyButton(icon, label)
            k.setFixedSize(QSize(108 if label == "Write Changes" else 100, 62))
            lay.addWidget(k)
    lay.addStretch(1)
    return bar


def status(left, right):
    f = QFrame()
    f.setStyleSheet(f"QFrame {{ background: {theme.TAB_FRAME}; border-top: 1px solid {theme.PANEL_EDGE}; }}")
    lay = QHBoxLayout(f)
    lay.setContentsMargins(16, 6, 16, 6)
    lay.addWidget(dim(left))
    lay.addStretch(1)
    lay.addWidget(dim(right))
    return f


def window(tabs, current, status_bar):
    w = QWidget()
    w.setObjectName("win")
    w.setStyleSheet(f"QWidget#win {{ background: {theme.BACKGROUND}; }}")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(widgets.Header("DATABASE"))
    lay.addWidget(toolbar())
    t = QTabWidget()
    for name, body in tabs:
        t.addTab(body, name)
    t.setCurrentIndex(current)
    wrap = QWidget()
    wl = QVBoxLayout(wrap)
    wl.setContentsMargins(16, 0, 16, 8)
    wl.addWidget(t)
    lay.addWidget(wrap, 1)
    lay.addWidget(status_bar)
    w.resize(1320, 820)
    return w


def placeholder():
    return QWidget()


def shoot(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(str(out / name))
    widget.close()


NAMES = ["DATABASE STRUCTURE", "BROWSE DATA", "EDIT PRAGMAS", "EXECUTE SQL"]

# ------------------------------------------------------------------ 1: Browse Data
theme.set_finish("obsidian")
style()
browse = QWidget()
bl = QVBoxLayout(browse)
bl.setContentsMargins(10, 10, 10, 6)
top = QHBoxLayout()
top.addWidget(field("Table"))
combo = QComboBox()
combo.addItems(["footage", "projects", "renders", "project_sizes (view)"])
combo.setFixedWidth(240)
top.addWidget(combo)
top.addSpacing(16)
top.addWidget(dim("Filter in any column:"))
e = QLineEdit("ARRI")
e.setFixedWidth(220)
top.addWidget(e)
top.addStretch(1)
shown = db.execute("SELECT COUNT(*) FROM footage WHERE camera LIKE '%ARRI%'").fetchone()[0]
total = db.execute("SELECT COUNT(*) FROM footage").fetchone()[0]
top.addWidget(dim(f"1 – 19 of {shown} rows (filtered from {total})"))
bl.addLayout(top)
cur = db.execute("SELECT id, project_id, clip, camera, duration_s, size_mb, thumbnail FROM footage "
                 "WHERE camera LIKE '%ARRI%' ORDER BY id LIMIT 19")
rows = [list(r) for r in cur.fetchall()]
rows[2][3] = "ARRI Alexa 35 (B cam)"
split = QSplitter()
split.addWidget(grid(cur, rows, changed={(2, 3)}, filters={"camera": "ARRI"}))
cell = QWidget()
cl = QVBoxLayout(cell)
cl.setContentsMargins(10, 0, 0, 0)
cl.addWidget(field("Edit database cell  ·  camera, row 3"))
mode = QHBoxLayout()
mode.addWidget(dim("Mode"))
m = QComboBox()
m.addItems(["Text", "Binary", "Image", "JSON"])
mode.addWidget(m)
mode.addStretch(1)
cl.addLayout(mode)
ed = QPlainTextEdit("ARRI Alexa 35 (B cam)")
cl.addWidget(ed, 1)
cl.addWidget(dim("Type of data: Text  ·  21 characters"))
cl.addWidget(dim("Not written yet: Write Changes (Ctrl+S) saves it,\nRevert throws it away."))
cell.setFixedWidth(330)
split.addWidget(cell)
split.setSizes([900, 330])
bl.addWidget(split, 1)
shoot(window([(NAMES[0], placeholder()), (NAMES[1], browse), (NAMES[2], placeholder()), (NAMES[3], placeholder())], 1,
             status("studio.db  ·  D:\\Studio\\studio.db  ·  4.1 MB", "UTF-8  ·  1 change not written")),
      "1_browse.png")

# ------------------------------------------------------------------ 2: Execute SQL
theme.set_finish("aurora")
style()
sql_text = """-- the biggest projects by footage, with how much each camera shot
SELECT p.title, f.camera, COUNT(*) AS clips, ROUND(SUM(f.size_mb) / 1024.0, 2) AS gb
FROM footage f
JOIN projects p ON p.id = f.project_id
WHERE f.camera IS NOT NULL
GROUP BY p.title, f.camera
ORDER BY gb DESC
LIMIT 12"""
t0 = time.perf_counter()
cur = db.execute(sql_text)
rows = cur.fetchall()
ms = (time.perf_counter() - t0) * 1000
sqlw = QWidget()
sl = QVBoxLayout(sqlw)
sl.setContentsMargins(10, 10, 10, 6)
sp = QSplitter(Qt.Orientation.Vertical)
editor = QPlainTextEdit(sql_text)
editor.setFont(mono(14))
Sql(editor.document())
sp.addWidget(editor)
res = QWidget()
rl = QVBoxLayout(res)
rl.setContentsMargins(0, 6, 0, 0)
rl.addWidget(grid(cur, rows, stretch=False), 1)
msg = dim(f"{len(rows)} rows returned in {max(1, round(ms))} ms   ·   SELECT p.title, f.camera, COUNT(*) …")
rl.addWidget(msg)
sp.addWidget(res)
sp.setSizes([260, 420])
sl.addWidget(sp, 1)
shoot(window([(NAMES[0], placeholder()), (NAMES[1], placeholder()), (NAMES[2], placeholder()), (NAMES[3], sqlw)], 3,
             status("studio.db  ·  D:\\Studio\\studio.db  ·  4.1 MB", "Ctrl+Return runs all  ·  Shift+F5 the line")),
      "2_sql.png")

# ------------------------------------------------------------------ 3: Database Structure
theme.set_finish("obsidian")
style()
tree = QTreeWidget()
tree.setHeaderLabels(["NAME", "TYPE", "SCHEMA"])
tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
groups = {"table": "Tables", "index": "Indices", "view": "Views", "trigger": "Triggers"}
objs = db.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
for kind, label in groups.items():
    mine = [o for o in objs if o[0] == kind]
    top_item = QTreeWidgetItem([f"{label} ({len(mine)})", "", ""])
    f = top_item.font(0)
    f.setWeight(QFont.Weight.DemiBold)
    top_item.setFont(0, f)
    tree.addTopLevelItem(top_item)
    for _, name, sql in mine:
        it = QTreeWidgetItem([name, "", " ".join((sql or "").split())])
        it.setIcon(0, widgets.line_icon("table" if kind == "table" else "list" if kind == "view" else "key"
                                        if kind == "index" else "step", None, 16))
        top_item.addChild(it)
        if kind in ("table", "view"):
            for col in db.execute(f"PRAGMA table_info({name})").fetchall():
                c = QTreeWidgetItem([col[1], col[2] or "", ("NOT NULL " if col[3] else "") +
                                     ("PRIMARY KEY" if col[5] else "") + (f"DEFAULT {col[4]}" if col[4] else "")])
                c.setForeground(1, QColor(theme.LED_ORANGE))
                c.setForeground(2, QColor(theme.TEXT_DIM))
                it.addChild(c)
    top_item.setExpanded(True)
tree.topLevelItem(0).child(0).setExpanded(True)
tree.topLevelItem(0).child(1).setExpanded(True)
st = QWidget()
stl = QVBoxLayout(st)
stl.setContentsMargins(10, 10, 10, 6)
stl.addWidget(tree, 1)
shoot(window([(NAMES[0], st), (NAMES[1], placeholder()), (NAMES[2], placeholder()), (NAMES[3], placeholder())], 0,
             status("studio.db  ·  3 tables, 2 indices, 1 view, 1 trigger", "SQLite " + sqlite3.sqlite_version)),
      "3_structure.png")
print("ok", out)
