"""PR7-DB's window: DB Browser for SQLite's four tabs (Database Structure, Browse Data, Edit Pragmas, Execute SQL), its
menus and keys, one database at a time. Every question goes through `_ask(kind)` (tests answer through `answers`);
`sync = True` runs SQL inline (tests)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading

from PySide6.QtCore import QObject, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QImage, QKeySequence, QPixmap,
                           QRegularExpressionValidator)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStackedWidget,
                               QTableView, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import __version__, database, finish, settings, theme, widgets
from .database import Database
from .dialogs import CsvDialog, IndexDialog, OptionsDialog, SqlExportDialog, TableDialog
from .models import FilterHeader, ResultModel, TableModel
from .sqledit import SqlEditor, mono_font

TABS = ["DATABASE STRUCTURE", "BROWSE DATA", "EDIT PRAGMAS", "EXECUTE SQL"]
FILTER = "SQLite databases (*.db *.sqlite *.sqlite3 *.db3 *.s3db);;All files (*)"


def _dim(text=""):
    lab = QLabel(text)
    lab.setProperty("role", "dim")
    return lab


def _field(text):
    lab = QLabel(text.upper())
    lab.setProperty("role", "field")
    return lab


def fmt_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


class _Bridge(QObject):
    ran = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, sync: bool = False):
        super().__init__()
        self.sync = sync
        self.answers: dict = {}
        self.said: list = []
        self.db: Database | None = None
        self.model: TableModel | None = None
        self.result_model: ResultModel | None = None
        self.running = False
        self.bridge = _Bridge()
        self.bridge.ran.connect(self._ran)
        self.setWindowTitle("Paragon PR7-DB")
        self.setAcceptDrops(True)
        self.resize(1320, 860)
        self._build()
        self.restyle()
        self._sync()

    # ================================================================ building
    def _build(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = widgets.Header("DATABASE")
        lay.addWidget(self.header)
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 10, 16, 6)
        bl.setSpacing(6)
        self.keys: dict[str, widgets.KeyButton] = {}
        groups = [[("new", "add", "New", self.new_database), ("open", "open", "Open", self.open_dialog),
                   ("write", "save", "Write Changes", self.write), ("revert", "undo", "Revert", self.revert)],
                  [("new_table", "table", "New Table", self.create_table), ("execute", "play", "Execute", self.run_all)],
                  [("import", "import", "Import", self.import_csv), ("export", "export", "Export", self.export_csv),
                   ("options", "gear", "Options", self.options)]]
        for i, group in enumerate(groups):
            if i:
                sep = QLabel()
                sep.setObjectName("sep")
                sep.setFixedSize(1, 40)
                bl.addSpacing(8)
                bl.addWidget(sep)
                bl.addSpacing(8)
            for key, icon, label, slot in group:
                k = widgets.KeyButton(icon, label)
                k.setFixedSize(QSize(108 if label == "Write Changes" else 100, 62))
                k.clicked.connect(lambda _=False, s=slot: s())
                bl.addWidget(k)
                self.keys[key] = k
        bl.addStretch(1)
        lay.addWidget(bar)

        self.stack = QStackedWidget()
        self.welcome = QLabel("OPEN A SQLITE DATABASE\n\nCtrl+O, New Database (Ctrl+N), or drop a file on this window")
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome.setProperty("role", "dim")
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(lambda i: self._tab_changed(i))
        self.tabs.addTab(self._structure_tab(), TABS[0])
        self.tabs.addTab(self._browse_tab(), TABS[1])
        self.tabs.addTab(self._pragmas_tab(), TABS[2])
        self.tabs.addTab(self._sql_tab(), TABS[3])
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(16, 0, 16, 8)
        wl.addWidget(self.stack)
        lay.addWidget(wrap, 1)
        foot = QFrame()
        foot.setObjectName("foot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 6, 16, 6)
        self.status = _dim("")
        fl.addWidget(self.status, 1)
        self.state = _dim("")
        fl.addWidget(self.state)
        lay.addWidget(foot)
        self._menus()

    def _structure_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 6)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["NAME", "TYPE", "SCHEMA"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._tree_open(it))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        lay.addWidget(self.tree, 1)
        return w

    def _browse_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 6)
        top = QHBoxLayout()
        top.addWidget(_field("Table"))
        self.table_combo = QComboBox()
        self.table_combo.setMinimumWidth(240)
        self.table_combo.currentTextChanged.connect(lambda t: self.browse(t) if t else None)
        top.addWidget(self.table_combo)
        top.addSpacing(16)
        top.addWidget(_dim("Filter in any column:"))
        self.everywhere = QLineEdit()
        self.everywhere.setFixedWidth(220)
        self.everywhere.textChanged.connect(lambda _t: self._filters_changed())
        top.addWidget(self.everywhere)
        for key, text, slot in (("new_record", "NEW RECORD", self.new_record),
                                ("delete_record", "DELETE RECORD", self.delete_records)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=slot: s())
            top.addWidget(b)
            setattr(self, key + "_button", b)
        top.addStretch(1)
        self.count_label = _dim("")
        top.addWidget(self.count_label)
        lay.addLayout(top)
        split = QSplitter()
        self.grid = QTableView()
        self.filter_header = FilterHeader(self.grid)
        self.filter_header.changed.connect(lambda _v: self._filters_changed())
        self.grid.setHorizontalHeader(self.filter_header)
        self.grid.setSortingEnabled(True)
        self.grid.setAlternatingRowColors(True)
        self.grid.setShowGrid(False)
        self.grid.verticalHeader().setDefaultSectionSize(26)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                  QAbstractItemView.EditTrigger.EditKeyPressed |
                                  QAbstractItemView.EditTrigger.AnyKeyPressed)
        split.addWidget(self.grid)
        split.addWidget(self._cell_panel())
        split.setSizes([900, 330])
        lay.addWidget(split, 1)
        return w

    def _cell_panel(self):
        w = QWidget()
        w.setMinimumWidth(280)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 0, 0, 0)
        self.cell_title = _field("Edit database cell")
        lay.addWidget(self.cell_title)
        row = QHBoxLayout()
        row.addWidget(_dim("Mode"))
        self.cell_mode = QComboBox()
        self.cell_mode.addItems(["Text", "Binary", "Image", "JSON"])
        self.cell_mode.currentTextChanged.connect(lambda _m: self._show_cell())
        row.addWidget(self.cell_mode)
        row.addStretch(1)
        lay.addLayout(row)
        self.cell_stack = QStackedWidget()
        self.cell_text = QPlainTextEdit()
        self.cell_text.setFont(mono_font())
        self.cell_image = QLabel()
        self.cell_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.cell_image)
        scroll.setWidgetResizable(True)
        self.cell_stack.addWidget(self.cell_text)
        self.cell_stack.addWidget(scroll)
        lay.addWidget(self.cell_stack, 1)
        self.cell_info = _dim("")
        lay.addWidget(self.cell_info)
        row = QHBoxLayout()
        self.cell_apply = QPushButton("APPLY")
        self.cell_apply.setToolTip("Ctrl+Return")
        self.cell_apply.clicked.connect(lambda: self.apply_cell())
        self.cell_null = QPushButton("SET NULL")
        self.cell_null.clicked.connect(lambda: self.apply_cell(null=True))
        self.cell_import = QPushButton("IMPORT FILE…")
        self.cell_import.clicked.connect(lambda: self.import_cell())
        for b in (self.cell_apply, self.cell_null, self.cell_import):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _pragmas_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 10, 10, 6)
        form = QFormLayout()
        self.pragma_edits = {}
        for name, kind, choices in database.PRAGMAS:
            if kind == "bool":
                e = QCheckBox()
            elif kind == "int":
                e = QLineEdit()                    # not a spin box: max_page_count is 4294967294, past Qt's int
                e.setValidator(QRegularExpressionValidator(QRegularExpression(r"-?\d{1,19}")))
                e.setFixedWidth(200)
            else:
                e = QComboBox()
                e.addItems(choices)
            form.addRow(_field(name.replace("_", " ")), e)
            self.pragma_edits[name] = e
        box = QWidget()
        box.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(box)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)
        row = QHBoxLayout()
        save = QPushButton("SAVE")
        save.clicked.connect(lambda: self.save_pragmas())
        reset = QPushButton("RESET")
        reset.clicked.connect(lambda: self.fill_pragmas())
        row.addWidget(save)
        row.addWidget(reset)
        row.addWidget(_dim("page size and auto vacuum need a VACUUM: Save runs it"))
        row.addStretch(1)
        outer.addLayout(row)
        return w

    def _sql_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 6)
        sp = QSplitter(Qt.Orientation.Vertical)
        self.editor = SqlEditor()
        self.editor.setPlainText(settings.get("last_sql", ""))
        sp.addWidget(self.editor)
        res = QWidget()
        rl = QVBoxLayout(res)
        rl.setContentsMargins(0, 6, 0, 0)
        self.results = QTableView()
        self.results.setAlternatingRowColors(True)
        self.results.setShowGrid(False)
        self.results.verticalHeader().setDefaultSectionSize(26)
        rl.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.sql_message = _dim("")
        self.sql_message.setWordWrap(True)
        row.addWidget(self.sql_message, 1)
        self.stop_button = QPushButton("STOP")
        self.stop_button.clicked.connect(lambda: self.stop())
        self.stop_button.setEnabled(False)
        row.addWidget(self.stop_button)
        rl.addLayout(row)
        sp.addWidget(res)
        sp.setSizes([260, 420])
        lay.addWidget(sp, 1)
        return w

    def _act(self, menu, text, slot, keys=None, key=None, checkable=False):
        a = QAction(text, self)
        if keys:
            a.setShortcuts([QKeySequence(k) for k in (keys if isinstance(keys, (list, tuple)) else [keys])])
        a.setCheckable(checkable)
        a.triggered.connect(lambda *_: slot())
        menu.addAction(a)
        if key:
            self.acts[key] = a
        return a

    def _menus(self):
        self.acts: dict[str, QAction] = {}
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._act(m, "New Database…", self.new_database, "Ctrl+N", "new")
        self._act(m, "Open Database…", self.open_dialog, "Ctrl+O", "open")
        self._act(m, "Open Database Read Only…", lambda: self.open_dialog(read_only=True), "Ctrl+Shift+O", "open_ro")
        self._act(m, "Write Changes", self.write, "Ctrl+S", "write")
        self._act(m, "Revert Changes", self.revert, None, "revert")
        self._act(m, "Close Database", self.close_database, ["Ctrl+W", "Ctrl+F4"], "close_db")
        m.addSeparator()
        im = m.addMenu("Import")
        self._act(im, "Table from CSV File…", self.import_csv, None, "import_csv")
        self._act(im, "Database from SQL File…", self.import_sql, None, "import_sql")
        em = m.addMenu("Export")
        self._act(em, "Table as CSV File…", self.export_csv, None, "export_csv")
        self._act(em, "Database to SQL File…", self.export_sql, None, "export_sql")
        m.addSeparator()
        self._act(m, "Exit", self.close, "Ctrl+Q")
        m = mb.addMenu("&Edit")
        self._act(m, "Create Table…", self.create_table, None, "create_table")
        self._act(m, "Modify Table…", self.modify_table, None, "modify_table")
        self._act(m, "Delete Table", lambda: self.drop_object("table"), None, "drop_table")
        self._act(m, "Create Index…", self.create_index, None, "create_index")
        m.addSeparator()
        self._act(m, "Options…", self.options, None, "options")
        m = mb.addMenu("&View")
        for i, name in enumerate(TABS):
            self._act(m, name.title(), lambda i=i: self.tabs.setCurrentIndex(i), f"Alt+{i + 1}", f"tab_{i}")
        m.addSeparator()
        self._act(m, "Refresh", self.refresh, "F5", "refresh")
        fm = m.addMenu("Finish")
        group = QActionGroup(self)
        self.finish_acts = {}
        for name in ("obsidian", "aurora"):
            a = self._act(fm, name.upper(), lambda n=name: self.set_finish(n), checkable=True)
            group.addAction(a)
            self.finish_acts[name] = a
        self.finish_acts[theme.FINISH].setChecked(True)
        m = mb.addMenu("E&xecute")
        self._act(m, "Execute All", self.run_all, ["Ctrl+Return", "Ctrl+R"], "execute")
        self._act(m, "Execute Current Line", self.run_current, "Shift+F5", "execute_line")
        self._act(m, "Stop", self.stop, None, "stop")
        m = mb.addMenu("&Tools")
        self._act(m, "Compact Database", self.compact, None, "compact")
        self._act(m, "Integrity Check", self.integrity_check, None, "integrity")
        self._act(m, "Foreign-Key Check", self.foreign_key_check, None, "fk_check")
        m.addSeparator()
        self._act(m, "Open SQLite Files with PR7-DB", self.toggle_open_with, None, "open_with", True)
        m = mb.addMenu("&Help")
        self._act(m, "Check for Updates…", self.check_updates, None, "updates")
        self._act(m, "About PR7-DB", self.about, None, "about")
        self._open_with_state()

    # ================================================================ look
    def restyle(self):
        app = QApplication.instance()
        if app is not None:
            app.setPalette(theme.palette())
            app.setStyleSheet(theme.stylesheet())
        self.centralWidget().setStyleSheet(
            f"QWidget#central {{ background: {theme.BACKGROUND}; }} QLabel#sep {{ background: {theme.PANEL_EDGE}; }} "
            f"QFrame#foot {{ background: {theme.TAB_FRAME}; border-top: 1px solid {theme.PANEL_EDGE}; }}")
        self.header.update()
        self.editor.restyle()
        self.cell_text.setFont(mono_font())
        for a_name, a in getattr(self, "finish_acts", {}).items():
            a.setChecked(a_name == theme.FINISH)
        if self.db is not None:
            self.fill_structure()

    def set_finish(self, name: str):
        theme.set_finish(name)
        finish.set(theme.FINISH)
        settings.set("finish", theme.FINISH)
        self.restyle()

    # ================================================================ asking
    def _ask(self, kind: str, payload=None):
        if kind in self.answers:
            a = self.answers[kind]
            return a(payload) if callable(a) else a
        if kind == "confirm":
            return QMessageBox.question(self, "PR7-DB", payload) == QMessageBox.StandardButton.Yes
        if kind == "unwritten":                     # write / discard / cancel
            box = QMessageBox(QMessageBox.Icon.Question, "PR7-DB",
                              "Do you want to write the changes to the database?", parent=self)
            write = box.addButton("WRITE", QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton("DISCARD", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("CANCEL", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            return "write" if box.clickedButton() is write else "discard" if box.clickedButton() is discard else "cancel"
        if kind == "open_file":
            return QFileDialog.getOpenFileName(self, "Open Database", payload or "", FILTER)[0]
        if kind == "new_file":
            return QFileDialog.getSaveFileName(self, "New Database", payload or "", FILTER)[0]
        if kind == "open_any":
            return QFileDialog.getOpenFileName(self, payload[0], "", payload[1])[0]
        if kind == "table":
            dlg = TableDialog(self, *payload)
            return dlg if dlg.exec() else None
        if kind == "index":
            dlg = IndexDialog(self, payload)
            return dlg.values() if dlg.exec() else None
        if kind == "csv":
            dlg = CsvDialog(self, *payload)
            return dlg.values() if dlg.exec() else None
        if kind == "sql_export":
            dlg = SqlExportDialog(self, *payload)
            return dlg.values() if dlg.exec() else None
        if kind == "options":
            dlg = OptionsDialog(self)
            if dlg.exec():
                dlg.save()
                return True
            return False
        return None

    def _say(self, kind: str, text: str):
        self.said.append((kind, text))
        if "say" in self.answers:
            self.answers["say"](kind, text)
            return
        icon = QMessageBox.Icon.Warning if kind in ("error", "warning") else QMessageBox.Icon.Information
        QMessageBox(icon, "PR7-DB", text, parent=self).exec()

    # ================================================================ opening and closing
    def open_database(self, path: str, read_only: bool = False) -> bool:
        if not self._may_close():
            return False
        try:
            db = Database(path, read_only=read_only)
        except sqlite3.DatabaseError as e:
            self._say("error", f"{os.path.basename(path)} isn't a SQLite database PR7-DB can open:\n{e}")
            return False
        self._close_db()
        self.db = db
        if settings.get_bool("foreign_keys", True) and not read_only:
            db.con.execute("PRAGMA foreign_keys = ON")
        recent = [path] + [p for p in settings.get_list("recent") if p != path]
        settings.set("recent", recent[:10])
        self.stack.setCurrentWidget(self.tabs)
        self.refresh()
        tables = db.tables()
        self.tabs.setCurrentIndex(1 if tables else 0)
        return True

    def new_database(self) -> bool:
        path = self._ask("new_file", "")
        if not path:
            return False
        if not self._may_close():
            return False
        try:
            Database.create(path).close()
        except (OSError, sqlite3.Error) as e:
            self._say("error", f"Couldn't make {path}:\n{e}")
            return False
        ok = self.open_database(path)
        if ok:
            self.create_table()
        return ok

    def open_dialog(self, read_only: bool = False) -> bool:
        start = os.path.dirname(self.db.path) if self.db and self.db.path else ""
        path = self._ask("open_file", start)
        return self.open_database(path, read_only) if path else False

    def _may_close(self) -> bool:
        if self.db is None or not self.db.dirty:
            return True
        got = self._ask("unwritten")
        if got == "write":
            return self.write()
        if got == "discard":
            self.db.revert()
            return True
        return False

    def _close_db(self):
        if self.db is not None:
            self.db.close()
        self.db = None
        self.model = None
        self.result_model = None
        self.grid.setModel(None)
        self.results.setModel(None)

    def close_database(self) -> bool:
        if not self._may_close():
            return False
        self._close_db()
        self.stack.setCurrentWidget(self.welcome)
        self._sync()
        return True

    def closeEvent(self, e):
        settings.set("last_sql", self.editor.toPlainText())
        if not self._may_close():
            e.ignore()
            return
        if self.running and self.db is not None:
            self.db.interrupt()
        self._close_db()
        e.accept()

    def write(self) -> bool:
        if self.db is None:
            return False
        try:
            self.db.write()
        except sqlite3.Error as e:
            self._say("error", f"Couldn't write the changes:\n{e}")
            return False
        if self.model is not None:
            self.model.changed.clear()
            self.grid.viewport().update()
        self._sync()
        return True

    def revert(self) -> bool:
        if self.db is None or not self.db.dirty:
            return False
        if not self._ask("confirm", "Throw away every change since the last Write Changes?"):
            return False
        self.db.revert()
        self.refresh()
        return True

    def refresh(self):
        if self.db is None:
            return
        self.fill_structure()
        current = self.table_combo.currentText()
        names = self.db.tables()
        self.table_combo.blockSignals(True)
        self.table_combo.clear()
        for n in names:
            self.table_combo.addItem(n)
        self.table_combo.blockSignals(False)
        if names:
            self.table_combo.setCurrentText(current if current in names else names[0])
            self.browse(self.table_combo.currentText())
        else:
            self.grid.setModel(None)
            self.model = None
        self.fill_pragmas()
        self._sync()

    # ================================================================ Database Structure
    def fill_structure(self):
        self.tree.clear()
        if self.db is None:
            return
        objs = self.db.objects()
        icons = {"table": "table", "view": "list", "index": "key", "trigger": "step"}
        for kind, label in (("table", "Tables"), ("index", "Indices"), ("view", "Views"), ("trigger", "Triggers")):
            mine = [o for o in objs if o.type == kind]
            top = QTreeWidgetItem([f"{label} ({len(mine)})", "", ""])
            f = top.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for o in mine:
                it = QTreeWidgetItem([o.name, "", " ".join(o.sql.split())])
                it.setData(0, Qt.ItemDataRole.UserRole, (o.type, o.name))
                it.setIcon(0, widgets.line_icon(icons[kind], None, 16))
                it.setToolTip(2, o.sql)
                top.addChild(it)
                for c in o.columns:
                    ci = QTreeWidgetItem([c.name, c.type, ("NOT NULL " if c.notnull else "") +
                                          ("PRIMARY KEY " if c.pk else "") + (f"DEFAULT {c.default}" if c.default
                                                                              else "")])
                    ci.setForeground(1, QColor(theme.LED_ORANGE))
                    ci.setForeground(2, QColor(theme.TEXT_DIM))
                    it.addChild(ci)
            top.setExpanded(True)

    def _tree_selected(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _tree_open(self, it):
        got = it.data(0, Qt.ItemDataRole.UserRole)
        if got and got[0] in ("table", "view"):
            self.table_combo.setCurrentText(got[1])
            self.tabs.setCurrentIndex(1)

    def _tree_menu(self, pos):
        got = self._tree_selected()
        if not got:
            return
        m = QMenu(self)
        kind, name = got
        if kind in ("table", "view"):
            m.addAction("Browse Table", lambda: self._tree_open(self.tree.currentItem()))
        if kind == "table":
            m.addAction("Modify Table…", lambda: self.modify_table(name))
            m.addAction("Export as CSV…", lambda: self.export_csv(name))
        m.addAction(f"Delete {kind.title()}", lambda: self.drop_object(kind, name))
        m.addAction("Copy Create Statement", lambda: QApplication.clipboard().setText(
            next((o.sql for o in self.db.objects() if o.name == name), "")))
        if self.answers.get("menu"):
            self.last_menu = m
            return
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ================================================================ Browse Data
    def browse(self, table: str):
        if self.db is None or not table:
            return
        self.model = TableModel(self.db, table)
        self.model.failed.connect(lambda msg: self._say("error", f"SQLite refused that:\n{msg}"))
        self.model.rowsInserted.connect(lambda *_: self._count())
        self.model.modelReset.connect(lambda: self._count())
        self.model.dataChanged.connect(lambda *_: self._sync())
        self.grid.setModel(self.model)
        self.grid.selectionModel().currentChanged.connect(lambda *_: self._cell_moved())
        self.filter_header.set_columns(self.model.columns)
        self.grid.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.grid.resizeColumnsToContents()
        for c in range(self.model.columnCount()):
            self.grid.setColumnWidth(c, min(320, max(90, self.grid.columnWidth(c))))
        self._count()
        self._show_cell()
        self._sync()

    def _filters_changed(self):
        if self.model is None:
            return
        self.model.filters = self.filter_header.values()
        self.model.everywhere = self.everywhere.text()
        self.model.reload()

    def _count(self):
        m = self.model
        if m is None:
            self.count_label.setText("")
            return
        if m.error:
            self.count_label.setText(m.error)
            return
        filtered = bool(m.filters or m.everywhere.strip())
        total = self.db.count(m.table) if filtered else m.total
        shown = len(m.data_rows)
        text = f"{min(1, shown)} – {shown} of {m.total} row{'s' if m.total != 1 else ''}"
        if filtered:
            text += f" (filtered from {total})"
        self.count_label.setText(text)

    def new_record(self) -> int | None:
        if self.model is None or not self.model.editable:
            return None
        try:
            rid = self.db.insert_row(self.model.table)
        except sqlite3.Error as e:
            self._say("error", f"SQLite refused a new row:\n{e}")
            return None
        self.model.reload()
        self.model.fetch_all()
        for r in range(self.model.rowCount()):
            if self.model.rowid(r) == rid:
                self.grid.setCurrentIndex(self.model.index(r, 0))
                self.grid.scrollTo(self.model.index(r, 0))
                break
        self._sync()
        return rid

    def selected_rows(self) -> list[int]:
        if self.model is None or self.grid.selectionModel() is None:
            return []
        rows = {i.row() for i in self.grid.selectionModel().selectedIndexes()}
        cur = self.grid.currentIndex()
        if not rows and cur.isValid():
            rows = {cur.row()}
        return sorted(rows)

    def delete_records(self) -> int:
        if self.model is None or not self.model.editable:
            return 0
        rows = self.selected_rows()
        if not rows:
            return 0
        rids = [self.model.rowid(r) for r in rows]
        try:
            n = self.db.delete_rows(self.model.table, rids)
        except sqlite3.Error as e:
            self._say("error", f"SQLite refused to delete:\n{e}")
            return 0
        self.model.reload()
        self._sync()
        return n

    def _cell_moved(self):
        """A new cell: the panel's mode follows its value (Text; Image or Binary for a BLOB)."""
        m = self.model
        idx = self.grid.currentIndex() if m is not None else None
        if m is not None and idx is not None and idx.isValid():
            v = m.value(idx.row(), idx.column())
            mode = ("Image" if not QImage.fromData(v).isNull() else "Binary") if isinstance(v, bytes) else "Text"
            self.cell_mode.blockSignals(True)
            self.cell_mode.setCurrentText(mode)
            self.cell_mode.blockSignals(False)
        self._show_cell()

    def _show_cell(self):
        m = self.model
        idx = self.grid.currentIndex() if m is not None else None
        if m is None or idx is None or not idx.isValid():
            self.cell_title.setText("EDIT DATABASE CELL")
            self.cell_text.setPlainText("")
            self.cell_info.setText("")
            for b in (self.cell_apply, self.cell_null, self.cell_import):
                b.setEnabled(False)
            return
        v = m.value(idx.row(), idx.column())
        self.cell_title.setText(f"EDIT DATABASE CELL  ·  {m.columns[idx.column()].upper()}, ROW {idx.row() + 1}")
        mode = self.cell_mode.currentText()
        if mode == "Image" and isinstance(v, bytes) and not QImage.fromData(v).isNull():
            pm = QPixmap.fromImage(QImage.fromData(v))
            self.cell_image.setPixmap(pm.scaled(QSize(300, 300), Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
                                      if pm.width() > 300 or pm.height() > 300 else pm)
            self.cell_stack.setCurrentIndex(1)
        else:
            self.cell_stack.setCurrentIndex(0)
            if v is None:
                text = ""
            elif isinstance(v, bytes):
                text = " ".join(f"{b:02X}" for b in v[:4096]) + (" …" if len(v) > 4096 else "")
            elif mode == "JSON":
                try:
                    text = json.dumps(json.loads(str(v)), indent=2, ensure_ascii=False)
                except ValueError:
                    text = str(v)
            else:
                text = str(v)
            self.cell_text.setPlainText(text)
        kind = "NULL" if v is None else "Binary" if isinstance(v, bytes) else type(v).__name__.replace(
            "str", "Text").replace("int", "Integer").replace("float", "Real")
        size = f"{len(v)} bytes" if isinstance(v, bytes) else (f"{len(str(v))} characters" if v is not None else "")
        self.cell_info.setText(f"Type of data: {kind}" + (f"  ·  {size}" if size else ""))
        editable = m.editable
        for b in (self.cell_apply, self.cell_null, self.cell_import):
            b.setEnabled(editable)

    def apply_cell(self, null: bool = False) -> bool:
        m = self.model
        idx = self.grid.currentIndex() if m else None
        if m is None or idx is None or not idx.isValid() or not m.editable:
            return False
        old = m.value(idx.row(), idx.column())
        if null:
            value = None
        elif self.cell_mode.currentText() == "Binary" or isinstance(old, bytes):
            try:
                value = bytes.fromhex(self.cell_text.toPlainText().replace("…", ""))
            except ValueError:
                self._say("warning", "Binary mode takes hex values (pairs of 0-9 A-F).")
                return False
        elif self.cell_mode.currentText() == "Image":
            return False
        else:
            from .models import typed
            value = typed(self.cell_text.toPlainText(), old) if not isinstance(old, str) else \
                self.cell_text.toPlainText()
        ok = m.set_value(idx.row(), idx.column(), value)
        self._show_cell()
        self._sync()
        return ok

    def import_cell(self) -> bool:
        m = self.model
        idx = self.grid.currentIndex() if m else None
        if m is None or idx is None or not idx.isValid():
            return False
        path = self._ask("open_any", ("Import File into Cell", "All files (*)"))
        if not path:
            return False
        with open(path, "rb") as f:
            data = f.read()
        ok = m.set_value(idx.row(), idx.column(), data)
        self._show_cell()
        self._sync()
        return ok

    # ================================================================ Edit Pragmas
    def fill_pragmas(self):
        if self.db is None:
            return
        values = self.db.pragmas()
        for name, kind, _ in database.PRAGMAS:
            e, v = self.pragma_edits[name], values.get(name)
            if kind == "bool":
                e.setChecked(bool(v))
            elif kind == "int":
                e.setText(str(int(v or 0)))
            else:
                e.setCurrentText(str(v))

    def save_pragmas(self) -> bool:
        if self.db is None:
            return False
        current = self.db.pragmas()
        try:
            for name, kind, _ in database.PRAGMAS:
                e = self.pragma_edits[name]
                v = e.isChecked() if kind == "bool" else int(e.text() or 0) if kind == "int" else e.currentText()
                old = current.get(name)
                if (kind == "bool" and bool(old) == v) or (kind != "bool" and str(old).upper() == str(v).upper()):
                    continue
                self.db.set_pragma(name, v)
        except sqlite3.Error as e:
            self._say("error", f"Couldn't set that pragma:\n{e}")
            self.fill_pragmas()
            return False
        self.fill_pragmas()
        return True

    # ================================================================ Execute SQL
    def run_all(self):
        return self._run(self.editor.toPlainText(), 0)

    def run_current(self):
        text = self.editor.toPlainText()
        got = database.statement_at(text, self.editor.textCursor().position())
        if got is None:
            return None
        return self._run(got[2], got[0])

    def _run(self, sql: str, start: int):
        if self.db is None or self.running:
            return None
        self.tabs.setCurrentIndex(3)
        self.editor.mark_error(None)
        settings.set("last_sql", self.editor.toPlainText())
        self.running = True
        self.sql_message.setText("Running…")
        self._sync()
        db = self.db
        if self.sync:
            self._ran(db.execute(sql, start))
            return self.last_result
        threading.Thread(target=lambda: self.bridge.ran.emit(db.execute(sql, start)), daemon=True).start()
        return None

    def _ran(self, r):
        self.running = False
        self.last_result = r
        if r.error:
            self.sql_message.setText(f"Error: {r.error}\n{r.message}")
            self.editor.mark_error(r.failed_at)
            self.results.setModel(None)
        else:
            self.result_model = ResultModel(r.columns, r.cursor) if r.columns else None
            self.results.setModel(self.result_model)
            if self.result_model is not None:
                self.results.resizeColumnsToContents()
                n = len(self.result_model.rows)
                more = "" if self.result_model.done else "+ (more as you scroll)"
                self.sql_message.setText(f"{n}{more} row{'s' if n != 1 else ''} returned in "
                                         f"{max(1, round(r.seconds * 1000))} ms.")
            else:
                self.sql_message.setText(r.message)
        if self.db is not None:
            self.fill_structure()
            names = self.db.tables()
            if self.table_combo.count() != len(names) or any(self.table_combo.itemText(i) != n
                                                             for i, n in enumerate(names)):
                self.refresh()
            elif self.model is not None:
                self.model.reload()
        self._sync()

    def stop(self):
        if self.running and self.db is not None:
            self.db.interrupt()

    # ================================================================ tables and indices
    def create_table(self) -> bool:
        if self.db is None:
            return False
        dlg = self._ask("table", ("", None))
        if not dlg:
            return False
        name, cols = dlg.name.text().strip(), [c for c in dlg.columns() if c["name"]]
        if not name or not cols:
            self._say("warning", "A table needs a name and at least one field.")
            return False
        try:
            self.db.create_table(name, cols, dlg.without_rowid.isChecked())
        except (sqlite3.Error, ValueError) as e:
            self._say("error", f"SQLite refused that table:\n{e}")
            return False
        self.refresh()
        self.table_combo.setCurrentText(name)
        return True

    def modify_table(self, name: str | None = None) -> bool:
        if self.db is None:
            return False
        name = name or (self.model.table if self.model else "")
        if not name or self.db.kind(name) != "table":
            return False
        dlg = self._ask("table", (name, self.db.columns(name)))
        if not dlg:
            return False
        try:
            for change in dlg.changes(name):
                if change[0] == "rename_table":
                    self.db.rename_table(name, change[1])
                    name = change[1]
                elif change[0] == "rename_column":
                    self.db.rename_column(name, change[1], change[2])
                elif change[0] == "drop_column":
                    self.db.drop_column(name, change[1])
                elif change[0] == "add_column":
                    self.db.add_column(name, change[1])
        except sqlite3.Error as e:
            self._say("error", f"SQLite refused that change:\n{e}")
            self.refresh()
            return False
        self.refresh()
        self.table_combo.setCurrentText(name)
        return True

    def drop_object(self, kind: str, name: str | None = None) -> bool:
        if self.db is None:
            return False
        name = name or (self.model.table if self.model and kind == "table" else "")
        if not name:
            return False
        if not self._ask("confirm", f"Delete the {kind} {name}? (Revert Changes brings it back until you write.)"):
            return False
        try:
            self.db.drop(kind, name)
        except sqlite3.Error as e:
            self._say("error", f"SQLite refused:\n{e}")
            return False
        self.refresh()
        return True

    def create_index(self) -> bool:
        if self.db is None:
            return False
        tables = {t: [c.name for c in self.db.columns(t)] for t in self.db.tables(views=False)}
        got = self._ask("index", tables)
        if not got or not got["columns"]:
            return False
        try:
            self.db.create_index(got["name"], got["table"], got["columns"], got["unique"])
        except sqlite3.Error as e:
            self._say("error", f"SQLite refused that index:\n{e}")
            return False
        self.refresh()
        return True

    # ================================================================ import / export
    def import_csv(self) -> int:
        if self.db is None:
            return 0
        got = self._ask("csv", (True, self.db.tables(views=False), "", ""))
        if not got or not got["path"] or not got["table"]:
            return 0
        try:
            n = self.db.import_csv(got["path"], got["table"], got["header"], got["sep"], got["quote"])
        except (OSError, sqlite3.Error, ValueError, UnicodeDecodeError) as e:
            self._say("error", f"Couldn't import {os.path.basename(got['path'])}:\n{e}")
            return 0
        self.refresh()
        self.table_combo.setCurrentText(got["table"])
        self._say("info", f"{n} row{'s' if n != 1 else ''} imported into {got['table']}. Write Changes keeps them.")
        return n

    def import_sql(self) -> bool:
        if self.db is None:
            return False
        path = self._ask("open_any", ("Import SQL File", "SQL files (*.sql);;All files (*)"))
        if not path:
            return False
        r = self.db.import_sql(path)
        if r.error:
            self._say("error", f"The SQL file stopped with an error:\n{r.error}")
        self.refresh()
        return not r.error

    def export_csv(self, table: str | None = None) -> int:
        if self.db is None:
            return 0
        table = table or (self.model.table if self.model else "")
        got = self._ask("csv", (False, self.db.tables(), "", table))
        if not got or not got["path"] or not got["table"]:
            return 0
        try:
            n = self.db.export_csv(got["table"], got["path"], got["sep"], got["quote"], got["header"])
        except (OSError, sqlite3.Error) as e:
            self._say("error", f"Couldn't export:\n{e}")
            return 0
        self._say("info", f"{n} row{'s' if n != 1 else ''} exported to {got['path']}.")
        return n

    def export_sql(self) -> int:
        if self.db is None:
            return 0
        got = self._ask("sql_export", (self.db.tables(views=False), ""))
        if not got or not got["path"]:
            return 0
        try:
            n = self.db.export_sql(got["path"], got["tables"], got["schema"], got["data"], got["keep"])
        except (OSError, sqlite3.Error) as e:
            self._say("error", f"Couldn't export:\n{e}")
            return 0
        self._say("info", f"Exported to {got['path']}.")
        return n

    # ================================================================ tools
    def compact(self) -> bool:
        if self.db is None:
            return False
        if self.db.dirty and not self._may_close():
            return False
        before = self.db.size()
        try:
            self.db.vacuum()
        except sqlite3.Error as e:
            self._say("error", f"Couldn't compact:\n{e}")
            return False
        self._say("info", f"Compacted: {fmt_size(before)} → {fmt_size(self.db.size())}.")
        self._sync()
        return True

    def integrity_check(self) -> list[str]:
        if self.db is None:
            return []
        got = self.db.integrity_check()
        self._say("info" if got == ["ok"] else "warning",
                  "Integrity check: ok." if got == ["ok"] else "Integrity check found problems:\n" + "\n".join(got[:30]))
        return got

    def foreign_key_check(self) -> list:
        if self.db is None:
            return []
        got = self.db.foreign_key_check()
        self._say("info" if not got else "warning",
                  "Foreign-key check: no broken references." if not got else
                  f"{len(got)} row(s) point at rows that aren't there:\n" +
                  "\n".join(f"{t} row {r} → {p}" for t, r, p, _ in got[:30]))
        return got

    def options(self) -> bool:
        if not self._ask("options"):
            return False
        self.restyle()
        return True

    # ================================================================ Open with
    def _open_with_state(self):
        a = self.acts.get("open_with")
        if a is None:
            return
        if sys.platform != "win32":
            a.setEnabled(False)
            return
        from . import shell
        a.setChecked(shell.registered())

    def toggle_open_with(self):
        from . import shell
        try:
            if self.acts["open_with"].isChecked():
                shell.register()
            else:
                shell.unregister()
        except OSError as e:
            self._say("error", f"Couldn't change that: {e}")
        self._open_with_state()

    # ================================================================ drag and drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_database(paths[0])

    # ================================================================ keys, menus, status
    def _tab_changed(self, i: int):
        if i == 2:
            self.fill_pragmas()
        self._sync()

    def _sync(self):
        if not hasattr(self, "acts"):
            return
        a, db = self.acts, self.db
        have = db is not None
        writable = have and not db.read_only
        dirty = have and db.dirty
        idle = not self.running
        for k in ("write", "revert"):
            a[k].setEnabled(bool(dirty) and idle)
        for k in ("close_db", "export_csv", "export_sql", "refresh", "integrity", "fk_check"):
            a[k].setEnabled(have)
        for k in ("import_csv", "import_sql", "create_table", "create_index", "compact"):
            a[k].setEnabled(writable and idle)
        table = have and self.model is not None and db.kind(self.model.table) == "table"
        a["modify_table"].setEnabled(bool(writable and table and idle))
        a["drop_table"].setEnabled(bool(writable and table and idle))
        for k in ("execute", "execute_line"):
            a[k].setEnabled(have and idle)
        a["stop"].setEnabled(self.running)
        self.stop_button.setEnabled(self.running)
        editable = bool(self.model and self.model.editable)
        self.new_record_button.setEnabled(editable)
        self.delete_record_button.setEnabled(editable)
        self.keys["write"].setEnabled(a["write"].isEnabled())
        self.keys["revert"].setEnabled(a["revert"].isEnabled())
        for k in ("new_table", "import"):
            self.keys[k].setEnabled(writable and idle)
        self.keys["execute"].setEnabled(a["execute"].isEnabled())
        self.keys["export"].setEnabled(have)
        if have:
            name = os.path.basename(db.path) if db.path else "in memory"
            self.status.setText(f"{name}  ·  {db.path or ''}  ·  {fmt_size(db.size())}" +
                                ("  ·  READ-ONLY" if db.read_only else ""))
            n = len(db.changes)
            self.state.setText("Running SQL…" if self.running else
                               (f"{n} change{'s' if n != 1 else ''} not written" if dirty else "All changes written"))
            self.setWindowTitle(f"{name}{' *' if dirty else ''} - Paragon PR7-DB")
        else:
            self.status.setText("No database open")
            self.state.setText("")
            self.setWindowTitle("Paragon PR7-DB")

    def check_updates(self):
        from . import update_ui
        update_ui.check(self, manual=True)

    def about(self):
        self._say("info", f"Paragon PR7-DB {__version__}\nSQLite databases, the way DB Browser for SQLite works "
                          f"(SQLite {sqlite3.sqlite_version}).")

