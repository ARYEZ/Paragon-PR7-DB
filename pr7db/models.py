"""Browse Data's and Execute SQL's grids: plain Python models (never QStandardItemModel: PR7-Z's crash) that fetch a
page at a time as the view scrolls, and the filter boxes under the column titles."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QLineEdit

from . import theme
from .database import Database

PAGE = 500
CHANGED = "#F07A2A"
BLOB_PREVIEW = 64


def display(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bytes):
        return "BLOB"
    if isinstance(v, float):
        return repr(v) if v != int(v) or abs(v) >= 1e16 else f"{v:.1f}"
    return str(v)


def _value_role(v, role):
    if role == Qt.ItemDataRole.ForegroundRole and (v is None or isinstance(v, bytes)):
        return QColor(theme.TEXT_DIM)
    if role == Qt.ItemDataRole.FontRole and v is None:
        f = QFont()
        f.setItalic(True)
        return f
    if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return None


class TableModel(QAbstractTableModel):
    """One table or view: rows fetched PAGE at a time; edits go to the database's open transaction."""

    failed = Signal(str)

    def __init__(self, db: Database, table: str):
        super().__init__()
        self.db, self.table = db, table
        self.filters: dict[str, str] = {}
        self.everywhere = ""
        self.sort_by: tuple[str, bool] | None = None
        self.columns: list[str] = []
        self.data_rows: list[list] = []      # [rowid, values...]
        self.total = 0
        self.changed: set[tuple[int, str]] = set()
        self.editable = db.editable(table)
        self.reload()

    def reload(self):
        self.beginResetModel()
        try:
            self.columns, rows = self.db.rows(self.table, self.filters, self.everywhere, self.sort_by, 0, PAGE)
            self.data_rows = [list(r) for r in rows]
            self.total = self.db.count(self.table, self.filters, self.everywhere)
            self.error = ""
        except sqlite3.Error as e:
            self.data_rows, self.total, self.error = [], 0, str(e)
        self.endResetModel()

    def canFetchMore(self, parent=QModelIndex()):
        return not parent.isValid() and len(self.data_rows) < self.total

    def fetchMore(self, parent=QModelIndex()):
        _, rows = self.db.rows(self.table, self.filters, self.everywhere, self.sort_by, len(self.data_rows), PAGE)
        if not rows:
            self.total = len(self.data_rows)
            return
        self.beginInsertRows(QModelIndex(), len(self.data_rows), len(self.data_rows) + len(rows) - 1)
        self.data_rows += [list(r) for r in rows]
        self.endInsertRows()

    def fetch_all(self):
        while self.canFetchMore():
            self.fetchMore()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.data_rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.columns[section] if section < len(self.columns) else None
        return str(section + 1)

    def rowid(self, row: int):
        return self.data_rows[row][0] if 0 <= row < len(self.data_rows) else None

    def value(self, row: int, col: int):
        return self.data_rows[row][col + 1]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v = self.value(index.row(), index.column())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if role == Qt.ItemDataRole.ToolTipRole and not isinstance(v, str):
                return None
            return display(v)
        if role == Qt.ItemDataRole.EditRole:
            return "" if v is None else (v.hex() if isinstance(v, bytes) else str(v))
        if role == Qt.ItemDataRole.ForegroundRole and (self.rowid(index.row()), self.columns[index.column()]) \
                in self.changed:
            return QColor(CHANGED)
        return _value_role(v, role)

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.editable and index.isValid() and not isinstance(self.value(index.row(), index.column()), bytes):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        old = self.value(index.row(), index.column())
        if isinstance(value, str) and not isinstance(old, str):
            value = typed(value, old)
        return self.set_value(index.row(), index.column(), value)

    def set_value(self, row: int, col: int, value) -> bool:
        """Write one cell (into the open transaction) and show it orange until Write Changes."""
        rid, name = self.rowid(row), self.columns[col]
        try:
            self.db.set_cell(self.table, rid, name, value)
        except sqlite3.Error as e:
            self.failed.emit(str(e))
            return False
        self.data_rows[row][col + 1] = self.db.cell(self.table, rid, name)      # as SQLite stored it (affinity)
        self.changed.add((rid, name))
        i = self.index(row, col)
        self.dataChanged.emit(i, i)
        return True

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if 0 <= column < len(self.columns):
            self.sort_by = (self.columns[column], order == Qt.SortOrder.DescendingOrder)
            self.reload()


def typed(text: str, old=None):
    """A typed cell value: a number stays a number (SQLite's column affinity decides in the end); "" over NULL
    stays NULL... unless something was typed."""
    t = text.strip()
    if t == "" and old is None:
        return None
    for conv in (int, float):
        try:
            return conv(t)
        except ValueError:
            pass
    return text


class ResultModel(QAbstractTableModel):
    """Execute SQL's results: read from the cursor PAGE rows at a time."""

    def __init__(self, columns: list[str], cursor):
        super().__init__()
        self.columns, self.cursor = columns, cursor
        self.rows: list[tuple] = []
        self.done = cursor is None
        if not self.done:
            self.fetchMore()

    def canFetchMore(self, parent=QModelIndex()):
        return not parent.isValid() and not self.done

    def fetchMore(self, parent=QModelIndex()):
        try:
            got = self.cursor.fetchmany(PAGE)
        except sqlite3.Error:
            got = []
        if len(got) < PAGE:
            self.done = True
        if got:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(got) - 1)
            self.rows += got
            self.endInsertRows()

    def fetch_all(self):
        while self.canFetchMore():
            self.fetchMore()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.columns[section]
        return str(section + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return display(v)
        return _value_role(v, role)


class FilterHeader(QHeaderView):
    """The column titles with a filter box under each (DB Browser's filter row)."""

    changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.boxes: list[QLineEdit] = []
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(True)
        self.sectionResized.connect(lambda *_: self._place())
        self.sectionMoved.connect(lambda *_: self._place())

    def set_columns(self, names: list[str], values: dict | None = None):
        for b in self.boxes:
            b.deleteLater()
        self.boxes = []
        for n in names:
            b = QLineEdit(self)
            b.setPlaceholderText("Filter")
            b.setText((values or {}).get(n, ""))
            b.setStyleSheet("QLineEdit { padding: 1px 6px; min-height: 14px; font-size: 12px; }")
            b.textChanged.connect(lambda _t: self.changed.emit(self.values()))
            b.show()
            self.boxes.append(b)
        self.names = names
        self._place()

    def values(self) -> dict:
        return {n: b.text() for n, b in zip(getattr(self, "names", []), self.boxes) if b.text().strip()}

    def sizeHint(self):
        s = super().sizeHint()
        s.setHeight(s.height() + 30)
        return s

    def updateGeometries(self):
        h = super().sizeHint().height()
        self.setViewportMargins(0, 0, 0, 30 if self.boxes else 0)
        super().updateGeometries()
        self._place(h)

    def _place(self, h: int | None = None):
        h = h or super().sizeHint().height()
        for i, b in enumerate(self.boxes):
            x = self.sectionViewportPosition(i)
            b.setGeometry(QRect(x + 3, h + 2, max(10, self.sectionSize(i) - 6), 26))
