"""Execute SQL's editor: SQL in the family's colours (keywords orange, strings pink, numbers pearl, comments dim),
line numbers, the statement that failed underlined."""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from . import settings, theme

KEYWORDS = set("""ABORT ACTION ADD AFTER ALL ALTER ALWAYS ANALYZE AND AS ASC ATTACH AUTOINCREMENT BEFORE BEGIN BETWEEN BY
CASCADE CASE CAST CHECK COLLATE COLUMN COMMIT CONFLICT CONSTRAINT CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME
CURRENT_TIMESTAMP DATABASE DEFAULT DEFERRABLE DEFERRED DELETE DESC DETACH DISTINCT DO DROP EACH ELSE END ESCAPE EXCEPT
EXCLUSIVE EXISTS EXPLAIN FAIL FILTER FIRST FOLLOWING FOR FOREIGN FROM FULL GENERATED GLOB GROUP GROUPS HAVING IF IGNORE
IMMEDIATE IN INDEX INDEXED INITIALLY INNER INSERT INSTEAD INTERSECT INTO IS ISNULL JOIN KEY LAST LEFT LIKE LIMIT MATCH
MATERIALIZED NATURAL NO NOT NOTHING NOTNULL NULL NULLS OF OFFSET ON OR ORDER OTHERS OUTER OVER PARTITION PLAN PRAGMA
PRECEDING PRIMARY QUERY RAISE RANGE RECURSIVE REFERENCES REGEXP REINDEX RELEASE RENAME REPLACE RESTRICT RETURNING RIGHT
ROLLBACK ROW ROWS SAVEPOINT SELECT SET TABLE TEMP TEMPORARY THEN TIES TO TRANSACTION TRIGGER UNBOUNDED UNION UNIQUE
UPDATE USING VACUUM VALUES VIEW VIRTUAL WHEN WHERE WINDOW WITH WITHOUT INTEGER TEXT REAL BLOB NUMERIC""".split())
FUNCTIONS = set("""ABS AVG CHANGES CHAR COALESCE COUNT DATE DATETIME GROUP_CONCAT HEX IFNULL IIF INSTR JSON JSON_EXTRACT
LENGTH LIKELIHOOD LOWER LTRIM MAX MIN NULLIF PRINTF QUOTE RANDOM REPLACE ROUND RTRIM SUBSTR SUM TIME TOTAL TRIM TYPEOF
UNICODE UPPER STRFTIME JULIANDAY ROW_NUMBER RANK DENSE_RANK LAG LEAD""".split())
TOKEN = re.compile(r"--[^\n]*|'(?:[^']|'')*'?|\"(?:[^\"]|\"\")*\"?|\b\d+(?:\.\d+)?\b|\b\w+\b")


def mono_font() -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFamilies(["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Liberation Mono", "monospace"])
    f.setPointSizeF(max(6.0, settings.get_float("font_size", 10.0)))
    return f


class SqlHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        kw = QTextCharFormat()
        kw.setForeground(QColor(theme.LED_ORANGE))
        kw.setFontWeight(QFont.Weight.DemiBold)
        fn = QTextCharFormat()
        fn.setForeground(QColor(theme.LED_ORANGE))
        st = QTextCharFormat()
        st.setForeground(QColor("#D6457E"))
        num = QTextCharFormat()
        num.setForeground(QColor(theme.TEXT))
        num.setFontWeight(QFont.Weight.DemiBold)
        com = QTextCharFormat()
        com.setForeground(QColor(theme.TEXT_DIM))
        com.setFontItalic(True)
        state = self.previousBlockState()
        start = 0
        if state == 1:                                   # inside a /* comment */ from an earlier line
            end = text.find("*/")
            if end < 0:
                self.setFormat(0, len(text), com)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, end + 2, com)
            start = end + 2
        self.setCurrentBlockState(0)
        i = start
        while i < len(text):
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                if end < 0:
                    self.setFormat(i, len(text) - i, com)
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(i, end + 2 - i, com)
                i = end + 2
                continue
            m = TOKEN.match(text, i)
            if not m:
                i += 1
                continue
            tok = m.group(0)
            if tok.startswith("--"):
                self.setFormat(i, len(tok), com)
            elif tok.startswith("'"):
                self.setFormat(i, len(tok), st)
            elif tok[0].isdigit():
                self.setFormat(i, len(tok), num)
            elif tok.upper() in KEYWORDS:
                self.setFormat(i, len(tok), kw)
            elif tok.upper() in FUNCTIONS and text[m.end():m.end() + 1] == "(":
                self.setFormat(i, len(tok), fn)
            i = m.end()


class _Gutter(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(e.rect(), QColor(theme.DOCK_TOP))
        p.setFont(self.ed.font())
        p.setPen(QColor(theme.TEXT_DIM))
        b = self.ed.firstVisibleBlock()
        off = self.ed.contentOffset()
        while b.isValid():
            g = self.ed.blockBoundingGeometry(b).translated(off)
            if g.top() > e.rect().bottom():
                break
            p.drawText(QRectF(0, g.top(), self.width() - 6, g.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(b.blockNumber() + 1))
            b = b.next()


class SqlEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.gutter = _Gutter(self)
        self.highlighter = SqlHighlighter(self.document())
        self.blockCountChanged.connect(lambda _: self._fit())
        self.updateRequest.connect(self._scroll)
        self.setTabStopDistance(QFontMetricsF(mono_font()).horizontalAdvance(" ") * 4)
        self.restyle()

    def restyle(self):
        self.setFont(mono_font())
        self.setStyleSheet(f"QPlainTextEdit {{ background: {theme.WIDGET}; color: {theme.TEXT}; border: 1px solid "
                           f"{theme.PANEL_EDGE}; border-radius: 6px; padding: 0; selection-background-color: "
                           f"{theme.ACCENT_DIM}; selection-color: #FFFFFF; }}")
        self.highlighter.rehighlight()
        self._fit()

    def gutter_width(self) -> int:
        return int(QFontMetricsF(self.font()).horizontalAdvance("9" * max(3, len(str(self.blockCount())))) + 14)

    def _fit(self):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scroll(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def mark_error(self, span: tuple[int, int] | None):
        """Underline the statement that failed (and put the cursor there); None clears it."""
        if span is None:
            self.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        c = QTextCursor(self.document())
        c.setPosition(span[0])
        c.setPosition(min(span[1], len(self.toPlainText())), QTextCursor.MoveMode.KeepAnchor)
        sel.cursor = c
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        fmt.setUnderlineColor(QColor(theme.METER_RED))
        sel.format = fmt
        self.setExtraSelections([sel])
        cur = self.textCursor()
        cur.setPosition(span[0])
        self.setTextCursor(cur)
