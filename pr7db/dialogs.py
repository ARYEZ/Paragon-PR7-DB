"""PR7-DB's dialogs: Create / Modify Table, Create Index, Import CSV, Export (CSV, SQL), Options."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QRadioButton, QButtonGroup, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import settings

TYPES = ["INTEGER", "TEXT", "REAL", "BLOB", "NUMERIC", ""]
SEPARATORS = {",": ",", ";": ";", "Tab": "\t", "|": "|"}


def _field(text):
    lab = QLabel(text.upper())
    lab.setProperty("role", "field")
    return lab


def _buttons(dlg, ok_text="OK"):
    b = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    ok = b.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
    ok.setDefault(True)
    b.accepted.connect(dlg.accept)
    b.rejected.connect(dlg.reject)
    return b


class TableDialog(QDialog):
    """Edit Table Definition (DB Browser's): the name and the columns with type, NN, PK, AI, U, default, check.
    For an existing table, `changes()` lists what to do (rename, add / rename / drop columns)."""

    HEAD = ["NAME", "TYPE", "NN", "PK", "AI", "U", "DEFAULT", "CHECK"]

    def __init__(self, parent=None, name: str = "", columns=None):
        super().__init__(parent)
        self.existing = columns is not None
        self.setWindowTitle("Modify Table" if self.existing else "Create Table")
        self.setMinimumSize(780, 420)
        form = QFormLayout()
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("Table name")
        form.addRow(_field("Table"), self.name)
        self.without_rowid = QCheckBox("Without rowid")
        if not self.existing:
            form.addRow("", self.without_rowid)
        self.grid = QTableWidget(0, len(self.HEAD))
        self.grid.setHorizontalHeaderLabels(self.HEAD)
        self.grid.verticalHeader().hide()
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.original = []
        for c in columns or []:
            self.add_row({"name": c.name, "type": c.type, "notnull": c.notnull, "pk": bool(c.pk),
                          "default": c.default or ""}, original=c.name)
        row = QHBoxLayout()
        add = QPushButton("ADD FIELD")
        add.clicked.connect(lambda: self.add_row({"name": f"Field{self.grid.rowCount() + 1}", "type": "TEXT"}))
        rem = QPushButton("REMOVE FIELD")
        rem.clicked.connect(self.remove_row)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(row)
        lay.addWidget(self.grid, 1)
        if self.existing:
            note = QLabel("Existing columns can be renamed or removed and new ones added (SQLite's ALTER TABLE); "
                          "their type and constraints stay as they are.")
            note.setWordWrap(True)
            note.setProperty("role", "dim")
            lay.addWidget(note)
        lay.addWidget(_buttons(self, "SAVE" if self.existing else "CREATE"))

    def add_row(self, c: dict, original: str | None = None):
        r = self.grid.rowCount()
        self.grid.insertRow(r)
        it = QTableWidgetItem(c.get("name", ""))
        it.setData(Qt.ItemDataRole.UserRole, original)
        self.grid.setItem(r, 0, it)
        t = QComboBox()
        t.setEditable(True)
        t.addItems(TYPES)
        t.setCurrentText(c.get("type", "TEXT"))
        t.setEnabled(original is None)
        self.grid.setCellWidget(r, 1, t)
        for col, key in ((2, "notnull"), (3, "pk"), (4, "autoincrement"), (5, "unique")):
            box = QCheckBox()
            box.setChecked(bool(c.get(key)))
            box.setEnabled(original is None)
            self.grid.setCellWidget(r, col, box)
        self.grid.setItem(r, 6, QTableWidgetItem(str(c.get("default") or "")))
        self.grid.setItem(r, 7, QTableWidgetItem(c.get("check", "")))
        if original is not None:
            self.original.append(original)
            for col in (6, 7):
                self.grid.item(r, col).setFlags(Qt.ItemFlag.ItemIsEnabled)

    def remove_row(self):
        r = self.grid.currentRow()
        if r >= 0:
            self.grid.removeRow(r)

    def columns(self) -> list[dict]:
        out = []
        for r in range(self.grid.rowCount()):
            out.append({"name": self.grid.item(r, 0).text().strip(),
                        "type": self.grid.cellWidget(r, 1).currentText().strip(),
                        "notnull": self.grid.cellWidget(r, 2).isChecked(), "pk": self.grid.cellWidget(r, 3).isChecked(),
                        "autoincrement": self.grid.cellWidget(r, 4).isChecked(),
                        "unique": self.grid.cellWidget(r, 5).isChecked(),
                        "default": self.grid.item(r, 6).text().strip(), "check": self.grid.item(r, 7).text().strip(),
                        "original": self.grid.item(r, 0).data(Qt.ItemDataRole.UserRole)})
        return out

    def changes(self, old_name: str) -> list[tuple]:
        """[("rename_table", new), ("rename_column", old, new), ("drop_column", name), ("add_column", dict)]"""
        out = []
        new_name = self.name.text().strip()
        if new_name and new_name != old_name:
            out.append(("rename_table", new_name))
        cols = self.columns()
        kept = {c["original"] for c in cols if c["original"]}
        for name in self.original:
            if name not in kept:
                out.append(("drop_column", name))
        for c in cols:
            if c["original"] and c["name"] != c["original"]:
                out.append(("rename_column", c["original"], c["name"]))
            elif not c["original"]:
                out.append(("add_column", c))
        return out


class IndexDialog(QDialog):
    def __init__(self, parent=None, tables: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Create Index")
        self.tables = tables or {}
        form = QFormLayout()
        self.name = QLineEdit()
        self.table = QComboBox()
        self.table.addItems(list(self.tables))
        self.unique = QCheckBox("Unique")
        self.cols = QListWidget()
        self.cols.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.table.currentTextChanged.connect(self._fill)
        form.addRow(_field("Index name"), self.name)
        form.addRow(_field("Table"), self.table)
        form.addRow("", self.unique)
        form.addRow(_field("Columns"), self.cols)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(_buttons(self, "CREATE"))
        self._fill(self.table.currentText())

    def _fill(self, table):
        self.cols.clear()
        for c in self.tables.get(table, []):
            self.cols.addItem(QListWidgetItem(c))
        if not self.name.text() or self.name.text().endswith("_index"):
            self.name.setText(f"{table}_index")

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "table": self.table.currentText(),
                "columns": [i.text() for i in self.cols.selectedItems()], "unique": self.unique.isChecked()}


class CsvDialog(QDialog):
    """Import CSV (a file into a table) or Export CSV (a table into a file)."""

    def __init__(self, parent=None, importing: bool = True, tables: list[str] | None = None, path: str = "",
                 table: str = ""):
        super().__init__(parent)
        self.importing = importing
        self.setWindowTitle("Import CSV" if importing else "Export CSV")
        form = QFormLayout()
        row = QHBoxLayout()
        self.path = QLineEdit(path)
        row.addWidget(self.path, 1)
        b = QPushButton("BROWSE…")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        w = QWidget()
        w.setLayout(row)
        form.addRow(_field("File"), w)
        self.table = QComboBox()
        self.table.setEditable(importing)
        self.table.addItems(tables or [])
        self.table.setCurrentText(table)
        form.addRow(_field("Table"), self.table)
        self.header = QCheckBox("Column names in the first line")
        self.header.setChecked(True)
        form.addRow("", self.header)
        self.sep = QComboBox()
        self.sep.addItems(list(SEPARATORS))
        form.addRow(_field("Field separator"), self.sep)
        self.quote = QComboBox()
        self.quote.addItems(['"', "'", "(none)"])
        form.addRow(_field("Quote character"), self.quote)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(_buttons(self, "IMPORT" if importing else "EXPORT"))

    def _browse(self):
        if self.importing:
            got, _ = QFileDialog.getOpenFileName(self, "Import CSV", self.path.text(), "CSV files (*.csv *.tsv *.txt);;All files (*)")
        else:
            got, _ = QFileDialog.getSaveFileName(self, "Export CSV", self.path.text(), "CSV files (*.csv)")
        if got:
            self.path.setText(got)
            if self.importing and not self.table.currentText():
                import os
                self.table.setCurrentText(os.path.splitext(os.path.basename(got))[0])

    def values(self) -> dict:
        q = self.quote.currentText()
        return {"path": self.path.text().strip(), "table": self.table.currentText().strip(),
                "header": self.header.isChecked(), "sep": SEPARATORS[self.sep.currentText()],
                "quote": "" if q == "(none)" else q}


class SqlExportDialog(QDialog):
    def __init__(self, parent=None, tables: list[str] | None = None, path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Export SQL")
        form = QFormLayout()
        row = QHBoxLayout()
        self.path = QLineEdit(path)
        row.addWidget(self.path, 1)
        b = QPushButton("BROWSE…")
        b.clicked.connect(lambda: self._browse())
        row.addWidget(b)
        w = QWidget()
        w.setLayout(row)
        form.addRow(_field("File"), w)
        self.tables = QListWidget()
        self.tables.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for t in tables or []:
            it = QListWidgetItem(t)
            self.tables.addItem(it)
            it.setSelected(True)
        form.addRow(_field("Tables"), self.tables)
        row = QHBoxLayout()
        self.what = {}
        group = QButtonGroup(self)
        for key, text in (("both", "Schema and data"), ("schema", "Schema only"), ("data", "Data only")):
            r = QRadioButton(text)
            r.setChecked(key == "both")
            group.addButton(r)
            row.addWidget(r)
            self.what[key] = r
        w = QWidget()
        w.setLayout(row)
        form.addRow(_field("Export"), w)
        self.keep = QCheckBox("Keep old schema (CREATE TABLE IF NOT EXISTS)")
        form.addRow("", self.keep)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(_buttons(self, "EXPORT"))

    def _browse(self):
        got, _ = QFileDialog.getSaveFileName(self, "Export SQL", self.path.text(), "SQL files (*.sql)")
        if got:
            self.path.setText(got)

    def values(self) -> dict:
        what = next(k for k, r in self.what.items() if r.isChecked())
        return {"path": self.path.text().strip(), "tables": [i.text() for i in self.tables.selectedItems()],
                "schema": what in ("both", "schema"), "data": what in ("both", "data"), "keep": self.keep.isChecked()}


class OptionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        form = QFormLayout()
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(6, 32)
        self.font_size.setSingleStep(0.5)
        self.font_size.setSuffix(" pt")
        self.font_size.setValue(settings.get_float("font_size", 10.0))
        form.addRow(_field("SQL text size"), self.font_size)
        self.foreign = QCheckBox("Foreign keys on when a database opens")
        self.foreign.setChecked(settings.get_bool("foreign_keys", True))
        form.addRow("", self.foreign)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(_buttons(self))

    def save(self):
        settings.set("font_size", self.font_size.value())
        settings.set("foreign_keys", self.foreign.isChecked())
