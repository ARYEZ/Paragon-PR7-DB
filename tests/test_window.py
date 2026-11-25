import sqlite3
import sys
import time

import pytest

from pr7db import theme


def make(tmp_path, rows=1200):
    p = str(tmp_path / "studio.db")
    con = sqlite3.connect(p)
    con.executescript("""CREATE TABLE clips (id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT 'new', camera TEXT,
                                            size REAL, thumb BLOB);
                         CREATE VIEW cams AS SELECT DISTINCT camera FROM clips;""")
    con.executemany("INSERT INTO clips (name, camera, size, thumb) VALUES (?, ?, ?, ?)",
                    [(f"clip{i:04d}", ["ARRI", "RED", None][i % 3], i / 2, b"\x89PNG" if i % 2 else None)
                     for i in range(rows)])
    con.commit()
    con.close()
    return p


def on_disk(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_open_browse_and_page(window, tmp_path):
    p = make(tmp_path)
    assert window.open_database(p)
    assert window.tabs.currentIndex() == 1 and window.table_combo.currentText() == "clips"
    assert window.count_label.text() == "1 – 500 of 1200 rows"                 # a page at a time
    window.model.fetchMore()
    assert len(window.model.data_rows) == 1000
    assert window.model.data(window.model.index(0, 2)) == "ARRI"
    assert window.model.data(window.model.index(2, 2)) == "NULL" and window.model.data(window.model.index(1, 4)) == "BLOB"
    assert window.status.text().startswith("studio.db") and window.state.text() == "All changes written"


def test_edit_write_and_revert(window, tmp_path):
    p = make(tmp_path)
    window.open_database(p)
    m = window.model
    assert m.setData(m.index(0, 1), "renamed")
    assert window.state.text() == "1 change not written" and window.acts["write"].isEnabled()
    assert on_disk(p, "SELECT name FROM clips WHERE id = 1") == [("clip0000",)]
    assert window.write() and on_disk(p, "SELECT name FROM clips WHERE id = 1") == [("renamed",)]
    assert window.state.text() == "All changes written"
    m.setData(m.index(0, 3), "12.5")
    assert window.db.cell("clips", 1, "size") == 12.5                          # a number stays a number
    assert window.revert() and window.db.cell("clips", 1, "size") == 0.0


def test_new_and_delete_records(window, tmp_path):
    p = make(tmp_path, rows=3)
    window.open_database(p)
    rid = window.new_record()
    assert rid == 4 and window.db.cell("clips", 4, "name") == "new"           # the column's default
    window.grid.setCurrentIndex(window.model.index(0, 0))
    assert window.delete_records() == 1
    window.write()
    assert on_disk(p, "SELECT id FROM clips ORDER BY id") == [(2,), (3,), (4,)]


def test_filters(window, tmp_path):
    window.open_database(make(tmp_path))
    window.filter_header.boxes[2].setText("=NULL")
    assert window.count_label.text() == "1 – 400 of 400 rows (filtered from 1200)"
    window.filter_header.boxes[2].setText("")
    window.filter_header.boxes[3].setText("10~20")
    assert window.model.total == 21
    window.filter_header.boxes[3].setText("")
    window.everywhere.setText("clip001")
    assert window.model.total == 10


def test_cell_panel(window, tmp_path):
    p = make(tmp_path, rows=3)
    window.open_database(p)
    window.grid.setCurrentIndex(window.model.index(1, 4))
    assert window.cell_mode.currentText() == "Binary" and window.cell_text.toPlainText() == "89 50 4E 47"
    window.cell_text.setPlainText("CA FE")
    assert window.apply_cell() and window.db.cell("clips", 2, "thumb") == b"\xca\xfe"
    window.grid.setCurrentIndex(window.model.index(0, 1))
    assert window.cell_mode.currentText() == "Text"            # the mode follows the cell
    window.cell_text.setPlainText("from the panel")
    assert window.apply_cell() and window.db.cell("clips", 1, "name") == "from the panel"
    assert not window.apply_cell(null=True) and window.db.cell("clips", 1, "name") == "from the panel"   # NOT NULL
    assert window.said and window.said[-1][0] == "error"


def test_execute_sql(window, tmp_path):
    p = make(tmp_path)
    window.open_database(p)
    window.editor.setPlainText("SELECT camera, COUNT(*) FROM clips GROUP BY camera ORDER BY camera;")
    r = window.run_all()
    assert r.columns == ["camera", "COUNT(*)"] and window.result_model.rows == [(None, 400), ("ARRI", 400), ("RED", 400)]
    assert window.sql_message.text().startswith("3 rows returned")
    window.editor.setPlainText("UPDATE clips SET camera = 'Sony' WHERE camera IS NULL;\nSELECT 1;")
    window.run_all()
    assert window.db.dirty and window.state.text() == "1 change not written"
    window.write()
    assert on_disk(p, "SELECT COUNT(*) FROM clips WHERE camera = 'Sony'") == [(400,)]


def test_current_statement_and_errors(window, tmp_path):
    window.open_database(make(tmp_path))
    text = "SELECT 1 AS a;\nSELECT 2 AS b;\nSELECT nope FROM clips;"
    window.editor.setPlainText(text)
    c = window.editor.textCursor()
    c.setPosition(text.index("SELECT 2") + 3)
    window.editor.setTextCursor(c)
    r = window.run_current()
    assert r.columns == ["b"]
    r = window.run_all()
    assert "no such column" in r.error and window.sql_message.text().startswith("Error")
    sels = window.editor.extraSelections()
    assert sels[0].cursor.selectedText() == "SELECT nope FROM clips;"


def test_stop_a_long_query(window, tmp_path):
    window.open_database(make(tmp_path, rows=10))
    window.sync = False
    window.editor.setPlainText("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT count(*) FROM n;")
    window.run_all()
    assert window.running and window.acts["stop"].isEnabled() and not window.acts["write"].isEnabled()
    time.sleep(0.2)
    window.stop()
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + 10
    while window.running and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not window.running and "interrupt" in window.last_result.error


class FakeTableDialog:
    def __init__(self, name, cols, without=False, changes=None):
        from PySide6.QtWidgets import QCheckBox, QLineEdit
        self.name = QLineEdit(name)
        self.without_rowid = QCheckBox()
        self.without_rowid.setChecked(without)
        self._cols, self._changes = cols, changes or []

    def columns(self):
        return self._cols

    def changes(self, old):
        return self._changes


def test_create_and_modify_tables(window, tmp_path):
    p = make(tmp_path, rows=2)
    window.open_database(p)
    window.answers["table"] = FakeTableDialog("renders", [{"name": "id", "type": "INTEGER", "pk": True},
                                                          {"name": "codec", "type": "TEXT", "default": "prores"}])
    assert window.create_table() and window.table_combo.currentText() == "renders"
    window.answers["table"] = FakeTableDialog("outputs", [], changes=[("rename_table", "outputs"),
                                                                    ("add_column", {"name": "take", "type": "INTEGER"}),
                                                                    ("rename_column", "codec", "format")])
    assert window.modify_table("renders")
    window.write()
    assert [r[1] for r in on_disk(p, "PRAGMA table_info(outputs)")] == ["id", "format", "take"]
    window.answers["index"] = {"name": "clips_cam", "table": "clips", "columns": ["camera"], "unique": False}
    assert window.create_index()
    assert window.drop_object("table", "outputs")
    window.write()
    names = {r[0] for r in on_disk(p, "SELECT name FROM sqlite_master")}
    assert "clips_cam" in names and "outputs" not in names


def test_real_table_dialog(qapp):
    from pr7db import database
    from pr7db.dialogs import TableDialog
    dlg = TableDialog(None, "t", [database.Column(0, "id", "INTEGER", False, None, 1),
                                  database.Column(1, "old", "TEXT", False, None, 0)])
    dlg.grid.item(1, 0).setText("new")
    dlg.add_row({"name": "extra", "type": "REAL"})
    dlg.name.setText("t2")
    assert dlg.changes("t") == [("rename_table", "t2"), ("rename_column", "old", "new"),
                                ("add_column", dlg.columns()[2])]
    dlg.grid.removeRow(0)
    assert ("drop_column", "id") in dlg.changes("t")


def test_csv_and_sql(window, tmp_path):
    p = make(tmp_path, rows=5)
    window.open_database(p)
    out = str(tmp_path / "clips.csv")
    window.answers["csv"] = {"path": out, "table": "clips", "header": True, "sep": ";", "quote": '"'}
    assert window.export_csv() == 5
    assert open(out, encoding="utf-8").readline().strip() == "id;name;camera;size;thumb"
    window.answers["csv"] = {"path": out, "table": "copy", "header": True, "sep": ";", "quote": '"'}
    assert window.import_csv() == 5 and window.table_combo.currentText() == "copy"
    window.write()
    assert on_disk(p, "SELECT count(*) FROM copy") == [(5,)]
    dump = str(tmp_path / "dump.sql")
    window.answers["sql_export"] = {"path": dump, "tables": ["clips"], "schema": True, "data": True, "keep": False}
    assert window.export_sql() == 5 and "CREATE TABLE clips" in open(dump).read()


def test_pragmas_and_tools(window, tmp_path):
    p = make(tmp_path, rows=5)
    window.open_database(p)
    window.tabs.setCurrentIndex(2)
    assert window.pragma_edits["journal_mode"].currentText() == "DELETE"
    assert int(window.pragma_edits["max_page_count"].text()) > 2 ** 31            # past Qt's int, shown as text
    window.pragma_edits["user_version"].setText("12")
    assert window.save_pragmas() and on_disk(p, "PRAGMA user_version") == [(12,)]
    assert window.integrity_check() == ["ok"] and window.foreign_key_check() == []
    assert window.compact()


def test_read_only(window, tmp_path):
    p = make(tmp_path, rows=3)
    assert window.open_database(p, read_only=True)
    assert not window.model.editable and not window.new_record_button.isEnabled()
    assert not window.acts["create_table"].isEnabled() and "READ-ONLY" in window.status.text()


def test_not_a_database(window, tmp_path):
    p = tmp_path / "notes.db"
    p.write_text("plain text, not SQLite" * 50)
    assert not window.open_database(str(p)) and window.said[-1][0] == "error"


def test_unwritten_changes_on_close(window, tmp_path):
    p = make(tmp_path, rows=3)
    window.open_database(p)
    window.model.setData(window.model.index(0, 1), "kept?")
    window.answers["unwritten"] = "cancel"
    assert not window.close_database() and window.db is not None
    window.answers["unwritten"] = "write"
    assert window.close_database() and on_disk(p, "SELECT name FROM clips WHERE id = 1") == [("kept?",)]


def test_structure_tab(window, tmp_path):
    window.open_database(make(tmp_path, rows=3))
    tables = window.tree.topLevelItem(0)
    assert tables.text(0) == "Tables (1)" and tables.child(0).text(0) == "clips"
    assert [tables.child(0).child(i).text(1) for i in range(5)] == ["INTEGER", "TEXT", "TEXT", "REAL", "BLOB"]
    window.tree.setCurrentItem(tables.child(0))
    window.answers["menu"] = True
    window._tree_menu(None)
    assert [a.text() for a in window.last_menu.actions()][:2] == ["Browse Table", "Modify Table…"]


def test_finish(window):
    window.set_finish("aurora")
    assert theme.FINISH == "aurora"
    window.set_finish("obsidian")


@pytest.mark.skipif(sys.platform == "win32", reason="elsewhere Open with can't be set")
def test_open_with_is_windows_only(window):
    assert not window.acts["open_with"].isEnabled()


def test_version():
    import pr7db
    assert pr7db.__version__.count(".") == 2
