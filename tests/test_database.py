import csv
import sqlite3

import pytest

from pr7db import database
from pr7db.database import Database


def make(tmp_path, name="studio.db"):
    p = str(tmp_path / name)
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT NOT NULL, client TEXT, budget REAL);
        CREATE TABLE tags (name TEXT PRIMARY KEY, n INTEGER) WITHOUT ROWID;
        CREATE VIEW big AS SELECT title FROM projects WHERE budget > 50000;
        CREATE INDEX projects_client ON projects(client);
        INSERT INTO projects (title, client, budget) VALUES ('ELSEWORLD', 'Northlight', 184000),
            ('Last Light', NULL, 42500), ('Saltwater', 'Harbour', 97000), ('Paper 100%_off', 'x', NULL);
        INSERT INTO tags VALUES ('noir', 3);
    """)
    con.commit()
    con.close()
    return p


def on_disk(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_structure(tmp_path):
    db = Database(make(tmp_path))
    kinds = {(o.type, o.name) for o in db.objects()}
    assert {("table", "projects"), ("table", "tags"), ("view", "big"), ("index", "projects_client")} <= kinds
    cols = db.columns("projects")
    assert [c.name for c in cols] == ["id", "title", "client", "budget"] and cols[1].notnull and cols[0].pk
    assert db.editable("projects") and not db.editable("tags") and not db.editable("big")
    db.close()


def test_changes_wait_for_write(tmp_path):
    p = make(tmp_path)
    db = Database(p)
    cols, rows = db.rows("projects")
    rid = rows[0][0]
    db.set_cell("projects", rid, "title", "ELSEWORLD II")
    assert db.dirty and db.cell("projects", rid, "title") == "ELSEWORLD II"
    assert on_disk(p, "SELECT title FROM projects WHERE rowid = 1") == [("ELSEWORLD",)]   # not written yet
    db.write()
    assert not db.dirty and on_disk(p, "SELECT title FROM projects WHERE rowid = 1") == [("ELSEWORLD II",)]
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_row("projects")                                  # title is NOT NULL
    db.revert()
    n = db.delete_rows("projects", [rid])
    assert n == 1 and db.count("projects") == 3
    db.revert()
    assert db.count("projects") == 4 and not db.dirty
    db.close()


def test_filters(tmp_path):
    db = Database(make(tmp_path))
    c = lambda f, e="": db.count("projects", f, e)            # noqa: E731
    assert c({"title": "light"}) == 1                           # contains, any case
    assert c({"budget": ">50000"}) == 2 and c({"budget": "40000~100000"}) == 2
    assert c({"client": "=NULL"}) == 1 and c({"client": "<>NULL"}) == 3
    assert c({"title": "/^[EL]/"}) == 2
    assert c({"title": "100%"}) == 1 and c({"title": "%"}) == 1       # % and _ are plain characters
    assert c({}, "harbour") == 1                                # Filter in any column
    cols, rows = db.rows("projects", sort=("budget", True))
    assert [r[1 + cols.index("title")] for r in rows][:2] == ["ELSEWORLD", "Saltwater"]
    _, page = db.rows("projects", offset=2, limit=1)
    assert len(page) == 1
    db.close()


def test_split_statements():
    sql = "SELECT 'a;b';\nCREATE TRIGGER t AFTER INSERT ON x BEGIN SELECT 1; SELECT 2; END;\n-- just a comment\nSELECT 3"
    parts = [p[2] for p in database.split_statements(sql)]
    assert parts == ["SELECT 'a;b';", "CREATE TRIGGER t AFTER INSERT ON x BEGIN SELECT 1; SELECT 2; END;",
                     "-- just a comment\nSELECT 3"]
    assert database.statement_at(sql, 2)[2] == "SELECT 'a;b';"
    assert database.statement_at(sql, len(sql))[2].endswith("SELECT 3")


def test_execute_sql(tmp_path):
    p = make(tmp_path)
    db = Database(p)
    r = db.execute("SELECT title, budget FROM projects WHERE budget > 50000 ORDER BY budget DESC")
    assert r.columns == ["title", "budget"] and r.cursor.fetchall() == [("ELSEWORLD", 184000.0), ("Saltwater", 97000.0)]
    assert not db.dirty                                         # a SELECT changes nothing
    r = db.execute("UPDATE projects SET budget = 1 WHERE client IS NULL; DELETE FROM projects WHERE title = 'x'")
    assert "1 row(s) changed" in r.message and db.dirty
    assert on_disk(p, "SELECT budget FROM projects WHERE client IS NULL") == [(42500.0,)]
    db.write()
    assert on_disk(p, "SELECT budget FROM projects WHERE client IS NULL") == [(1.0,)]
    r = db.execute("SELECT 1;\nSELECT nope FROM projects;\nSELECT 2", start=100)
    assert "no such column" in r.error and r.failed_at == (110, 136)
    r = db.execute("CREATE TABLE t (a); VACUUM")
    assert "write or revert" in r.error and db.kind("t") == "table"       # the CREATE stayed, inside the transaction
    db.revert()
    assert db.kind("t") == ""
    db.close()


def test_read_only(tmp_path):
    p = make(tmp_path)
    db = Database(p, read_only=True)
    assert not db.editable("projects")
    with pytest.raises(sqlite3.OperationalError):
        db.set_cell("projects", 1, "title", "x")
    assert db.execute("UPDATE projects SET title = 'x'").error
    db.close()


def test_not_a_database(tmp_path):
    p = tmp_path / "notes.db"
    p.write_text("this is not a database, it's a text file with a .db name" * 20)
    with pytest.raises(sqlite3.DatabaseError):
        Database(str(p))


def test_pragmas(tmp_path):
    p = make(tmp_path)
    db = Database(p)
    got = db.pragmas()
    assert got["journal_mode"] == "DELETE" and got["foreign_keys"] == 0 and got["auto_vacuum"] == "NONE"
    db.set_pragma("user_version", 7)
    db.set_pragma("auto_vacuum", "FULL")
    assert db.pragma("user_version") == 7 and db.pragma("auto_vacuum") == "FULL"
    assert on_disk(p, "PRAGMA user_version") == [(7,)]
    db.set_cell("projects", 1, "title", "y")
    with pytest.raises(sqlite3.OperationalError):
        db.set_pragma("page_size", 8192)                          # needs the changes written first
    db.close()


def test_tables_columns_indices(tmp_path):
    p = make(tmp_path)
    db = Database(p)
    sql = db.create_table("renders", [{"name": "id", "type": "INTEGER", "pk": True, "autoincrement": True},
                                      {"name": "take", "type": "INTEGER", "notnull": True, "default": "1"},
                                      {"name": "codec", "type": "TEXT", "default": "prores", "check": "length(codec) > 0"}])
    assert 'PRIMARY KEY AUTOINCREMENT' in sql and "DEFAULT 'prores'" in sql
    rid = db.insert_row("renders")
    assert db.cell("renders", rid, "codec") == "prores" and db.cell("renders", rid, "take") == 1
    db.add_column("renders", {"name": "note", "type": "TEXT"})
    db.rename_column("renders", "note", "notes")
    db.drop_column("renders", "notes")
    db.rename_table("renders", "outputs")
    db.create_index("outputs_take", "outputs", ["take"], unique=False)
    db.write()
    assert [r[1] for r in on_disk(p, "PRAGMA table_info(outputs)")] == ["id", "take", "codec"]
    assert on_disk(p, "SELECT name FROM sqlite_master WHERE type='index' AND name='outputs_take'")
    db.drop("table", "outputs")
    db.write()
    assert not on_disk(p, "SELECT name FROM sqlite_master WHERE name='outputs'")
    with pytest.raises(ValueError):
        database.create_table_sql("x", [])
    db.close()


def test_csv_round_trip(tmp_path):
    p = make(tmp_path)
    db = Database(p)
    out = str(tmp_path / "projects.csv")
    assert db.export_csv("projects", out) == 4
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["id", "title", "client", "budget"] and rows[2][2] == ""
    assert db.import_csv(out, "copy") == 4
    assert db.columns("copy")[0].type == "INTEGER" and db.columns("copy")[3].type == "REAL"
    assert db.count("copy") == 4
    db.import_csv(out, "copy")                                      # appended to the same table
    assert db.count("copy") == 8
    db.revert()
    assert db.kind("copy") == ""                                    # Revert undoes the whole import
    db.close()


def test_sql_export_and_import(tmp_path):
    p = make(tmp_path)
    db = Database(p)
    out = str(tmp_path / "dump.sql")
    assert db.export_sql(out) == 5
    text = open(out).read()
    assert text.startswith("BEGIN TRANSACTION;") and 'CREATE INDEX projects_client' in text
    fresh = Database.create(str(tmp_path / "fresh.db"))
    r = fresh.import_sql(out)
    assert not r.error, r.error
    fresh.write()
    assert on_disk(str(tmp_path / "fresh.db"), "SELECT count(*) FROM projects") == [(4,)]
    db.close()
    fresh.close()


def test_tools(tmp_path):
    db = Database(make(tmp_path))
    assert db.integrity_check() == ["ok"] and db.foreign_key_check() == []
    db.vacuum()
    db.set_cell("projects", 1, "title", "z")
    with pytest.raises(sqlite3.OperationalError):
        db.vacuum()
    db.close()
