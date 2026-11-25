"""One SQLite database (no Qt), the way DB Browser for SQLite works on it.

Every change (a cell, a new or deleted row, a table created, SQL run in Execute SQL) happens inside one open
transaction: `write()` commits it (Write Changes), `revert()` rolls it back (Revert Changes), `dirty` says whether
anything waits. Statements SQLite won't run inside a transaction (VACUUM, some PRAGMAs) need the changes written first.

Browse Data reads a page at a time (`rows(offset, limit)`), so a table of millions of rows opens at once; rows are
addressed by rowid (tables WITHOUT ROWID and views are read-only, as in DB Browser). Filters use DB Browser's syntax:
plain text = contains; = <> > < >= <= ; "10~20" a range; "/regex/" a regular expression; "=NULL" / "<>NULL".
"""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field

NEEDS_NO_TRANSACTION = re.compile(r"^\s*(VACUUM|PRAGMA\s+(journal_mode|page_size|auto_vacuum|locking_mode)\b|"
                                  r"ATTACH|DETACH|BEGIN|COMMIT|END|ROLLBACK)", re.I)
READS = re.compile(r"^\s*(SELECT|VALUES|EXPLAIN)\b|^\s*PRAGMA\s+[\w.]+\s*(\([^)]*\))?\s*;?\s*$", re.I)
PRAGMAS = [  # DB Browser's Edit Pragmas list: name, kind (bool / int / choice), choices
    ("auto_vacuum", "choice", ["NONE", "FULL", "INCREMENTAL"]),
    ("automatic_index", "bool", None),
    ("checkpoint_fullfsync", "bool", None),
    ("foreign_keys", "bool", None),
    ("fullfsync", "bool", None),
    ("ignore_check_constraints", "bool", None),
    ("journal_mode", "choice", ["DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"]),
    ("journal_size_limit", "int", None),
    ("locking_mode", "choice", ["NORMAL", "EXCLUSIVE"]),
    ("max_page_count", "int", None),
    ("page_size", "choice", ["512", "1024", "2048", "4096", "8192", "16384", "32768", "65536"]),
    ("recursive_triggers", "bool", None),
    ("secure_delete", "bool", None),
    ("synchronous", "choice", ["OFF", "NORMAL", "FULL", "EXTRA"]),
    ("temp_store", "choice", ["DEFAULT", "FILE", "MEMORY"]),
    ("user_version", "int", None),
    ("wal_autocheckpoint", "int", None),
]
CHOICE_NUMBERS = {"auto_vacuum": ["NONE", "FULL", "INCREMENTAL"], "synchronous": ["OFF", "NORMAL", "FULL", "EXTRA"],
                  "temp_store": ["DEFAULT", "FILE", "MEMORY"]}


def quote(name: str) -> str:
    """An identifier, double-quoted (any name works: spaces, keywords, quotes)."""
    return '"' + str(name).replace('"', '""') + '"'


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "X'" + value.hex().upper() + "'"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def split_statements(sql: str) -> list[tuple[int, int, str]]:
    """Each complete statement as (start, end, text), by SQLite's own rule (a ; inside a string or a trigger body
    doesn't end it). A last statement without ; counts too."""
    out = []
    start = 0
    i = 0
    n = len(sql)
    while i < n:
        j = sql.find(";", i)
        if j < 0:
            break
        chunk = sql[start:j + 1]
        if sqlite3.complete_statement(chunk):
            if _has_code(chunk):
                out.append(_span(sql, start, j + 1))
            start = j + 1
        i = j + 1
    rest = sql[start:]
    if _has_code(rest):
        out.append(_span(sql, start, n))
    return out


def _span(sql: str, a: int, b: int) -> tuple[int, int, str]:
    """The statement's own place, without the whitespace around it."""
    text = sql[a:b]
    lead = len(text) - len(text.lstrip())
    return a + lead, b - (len(text) - len(text.rstrip())), text.strip()


def _has_code(s: str) -> bool:
    s = re.sub(r"--[^\n]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return bool(s.strip().strip(";").strip())


def statement_at(sql: str, pos: int) -> tuple[int, int, str] | None:
    """The statement the cursor is in (Shift+F5 runs it); after the last one, the last one."""
    parts = split_statements(sql)
    for s, e, text in parts:
        if s <= pos <= e:
            return s, e, text
    before = [p for p in parts if p[1] <= pos]
    return before[-1] if before else (parts[0] if parts else None)


@dataclass
class Column:
    cid: int
    name: str
    type: str
    notnull: bool
    default: str | None
    pk: int
    hidden: int = 0


@dataclass
class Obj:
    type: str            # table / view / index / trigger
    name: str
    table: str
    sql: str
    columns: list[Column] = field(default_factory=list)


@dataclass
class Result:
    """What one Execute SQL run gave: the last statement's columns and rows (fetched lazily by the caller through
    `cursor`), the messages, how long it took."""
    columns: list[str]
    cursor: sqlite3.Cursor | None
    message: str
    seconds: float
    error: str = ""
    failed_at: tuple[int, int] | None = None     # the failing statement's place in the editor text


class Database:
    def __init__(self, path: str | None = None, read_only: bool = False):
        self.path = path
        self.read_only = read_only
        if path is None:
            self.con = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        elif read_only:
            uri = "file:" + path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
            self.con = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
        else:
            self.con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.con.create_function("REGEXP", 2, _regexp, deterministic=True)
        self.con.execute("SELECT count(*) FROM sqlite_master").fetchone()      # not a database: fails here
        self.changes: list[str] = []          # what waits to be written (shown in the status line)

    @classmethod
    def create(cls, path: str) -> "Database":
        if os.path.exists(path):
            os.remove(path)
        db = cls(path)
        db.con.execute("PRAGMA user_version")                                 # writes the file header
        return db

    # ----------------------------------------------------------------- the transaction
    @property
    def dirty(self) -> bool:
        return self.con.in_transaction

    def _begin(self):
        if self.read_only:
            raise sqlite3.OperationalError("the database is open read-only")
        if not self.con.in_transaction:
            self.con.execute("BEGIN")

    def write(self) -> None:
        """Write Changes."""
        if self.con.in_transaction:
            self.con.execute("COMMIT")
        self.changes.clear()

    def revert(self) -> None:
        """Revert Changes."""
        if self.con.in_transaction:
            self.con.execute("ROLLBACK")
        self.changes.clear()

    def close(self) -> None:
        try:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
        finally:
            self.con.close()

    def interrupt(self) -> None:
        """Stop a running statement (Execute SQL's Stop): it ends with an 'interrupted' error."""
        self.con.interrupt()

    # ----------------------------------------------------------------- structure
    def objects(self) -> list[Obj]:
        rows = self.con.execute("SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
                                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        out = []
        for t, name, tbl, sql in rows:
            o = Obj(t, name, tbl, sql)
            if t in ("table", "view"):
                o.columns = self.columns(name)
            out.append(o)
        return out

    def tables(self, views: bool = True) -> list[str]:
        kinds = ("table", "view") if views else ("table",)
        return [o.name for o in self.objects() if o.type in kinds]

    def columns(self, table: str) -> list[Column]:
        return [Column(r[0], r[1], r[2] or "", bool(r[3]), r[4], r[5], r[6] if len(r) > 6 else 0)
                for r in self.con.execute(f"PRAGMA table_xinfo({quote(table)})").fetchall() if not (len(r) > 6 and r[6] == 1)]

    def kind(self, name: str) -> str:
        r = self.con.execute("SELECT type, sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        return r[0] if r else ""

    def editable(self, table: str) -> bool:
        """Rows can be edited: a table with a rowid, and the database not read-only."""
        if self.read_only or self.kind(table) != "table":
            return False
        sql = self.con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0] or ""
        return not re.search(r"WITHOUT\s+ROWID\s*$", sql.strip().rstrip(";"), re.I)

    # ----------------------------------------------------------------- Browse Data
    def _where(self, table: str, filters: dict[str, str] | None, everywhere: str = "") -> tuple[str, list]:
        parts, args = [], []
        for col, text in (filters or {}).items():
            text = (text or "").strip()
            if not text:
                continue
            sql, a = filter_sql(quote(col), text)
            parts.append(sql)
            args += a
        if everywhere.strip():
            cols = [c.name for c in self.columns(table)]
            ors = [f"{quote(c)} LIKE ? ESCAPE '\\'" for c in cols]
            if ors:
                parts.append("(" + " OR ".join(ors) + ")")
                args += [f"%{_like(everywhere.strip())}%"] * len(ors)
        return (" WHERE " + " AND ".join(parts)) if parts else "", args

    def count(self, table: str, filters=None, everywhere: str = "") -> int:
        where, args = self._where(table, filters, everywhere)
        return self.con.execute(f"SELECT count(*) FROM {quote(table)}{where}", args).fetchone()[0]

    def rows(self, table: str, filters=None, everywhere: str = "", sort: tuple[str, bool] | None = None,
             offset: int = 0, limit: int = 1000) -> tuple[list[str], list[tuple]]:
        """(column names, rows); each row starts with its rowid (None for views / WITHOUT ROWID tables)."""
        cols = [c.name for c in self.columns(table)]
        rid = "rowid" if self.editable(table) or (self.kind(table) == "table" and self._has_rowid(table)) else "NULL"
        where, args = self._where(table, filters, everywhere)
        order = f" ORDER BY {quote(sort[0])} {'DESC' if sort[1] else 'ASC'}" if sort else \
            (" ORDER BY rowid" if rid == "rowid" else "")
        sel = ", ".join(quote(c) for c in cols) or "*"
        q = f"SELECT {rid}, {sel} FROM {quote(table)}{where}{order} LIMIT ? OFFSET ?"
        return cols, self.con.execute(q, args + [limit, offset]).fetchall()

    def _has_rowid(self, table: str) -> bool:
        try:
            self.con.execute(f"SELECT rowid FROM {quote(table)} LIMIT 0")
            return True
        except sqlite3.OperationalError:
            return False

    def set_cell(self, table: str, rowid: int, column: str, value) -> None:
        self._begin()
        self.con.execute(f"UPDATE {quote(table)} SET {quote(column)} = ? WHERE rowid = ?", (value, rowid))
        self.changes.append(f"{table}: a cell")

    def insert_row(self, table: str) -> int:
        """New Record: a row with the columns' defaults; returns its rowid."""
        self._begin()
        cur = self.con.execute(f"INSERT INTO {quote(table)} DEFAULT VALUES")
        self.changes.append(f"{table}: a new row")
        return cur.lastrowid

    def delete_rows(self, table: str, rowids: list[int]) -> int:
        self._begin()
        n = 0
        for r in rowids:
            n += self.con.execute(f"DELETE FROM {quote(table)} WHERE rowid = ?", (r,)).rowcount
        self.changes.append(f"{table}: {n} row(s) deleted")
        return n

    def cell(self, table: str, rowid: int, column: str):
        r = self.con.execute(f"SELECT {quote(column)} FROM {quote(table)} WHERE rowid = ?", (rowid,)).fetchone()
        return r[0] if r else None

    # ----------------------------------------------------------------- Execute SQL
    def execute(self, sql: str, start: int = 0) -> Result:
        """Run every statement in turn (inside the open transaction); the last one's rows are the result. On an
        error the statements before it stay done (as in DB Browser) and the failing one is pointed out."""
        t0 = time.perf_counter()
        parts = split_statements(sql)
        if not parts:
            return Result([], None, "Nothing to run.", 0.0)
        cur = None
        done = 0
        changed = 0
        wrote = False
        for s, e, text in parts:
            try:
                if NEEDS_NO_TRANSACTION.match(text):
                    if self.con.in_transaction and not re.match(r"^\s*(COMMIT|END|ROLLBACK)", text, re.I):
                        raise sqlite3.OperationalError("write or revert the changes first: SQLite can't run this "
                                                       "inside a transaction")
                elif not self.read_only and not READS.match(text):
                    self._begin()                    # a change: it waits for Write Changes
                    wrote = True
                cur = self.con.execute(text)
                if cur.description is None and cur.rowcount > 0:
                    changed += cur.rowcount
                done += 1
            except sqlite3.Error as err:
                return Result([], None, f"{done} statement(s) done before the error.", time.perf_counter() - t0,
                              f"{err}", (start + s, start + e))
        secs = time.perf_counter() - t0
        if wrote and self.con.in_transaction:
            self.changes.append("SQL run in Execute SQL")
        if cur is not None and cur.description:
            cols = [d[0] for d in cur.description]
            return Result(cols, cur, f"Ran in {secs * 1000:.0f} ms.", secs)
        msg = f"{done} statement(s) ran in {secs * 1000:.0f} ms" + (f", {changed} row(s) changed." if changed else ".")
        return Result([], None, msg, secs)

    # ----------------------------------------------------------------- pragmas
    def pragma(self, name: str):
        v = self.con.execute(f"PRAGMA {name}").fetchone()
        v = v[0] if v else None
        if name in CHOICE_NUMBERS and isinstance(v, int):
            return CHOICE_NUMBERS[name][v]
        return str(v).upper() if isinstance(v, str) else v

    def pragmas(self) -> dict:
        return {name: self.pragma(name) for name, _, _ in PRAGMAS}

    def set_pragma(self, name: str, value) -> None:
        if name not in {p[0] for p in PRAGMAS}:
            raise ValueError(name)
        if self.read_only:
            raise sqlite3.OperationalError("the database is open read-only")
        v = int(value) if isinstance(value, bool) else value
        if name in ("page_size", "auto_vacuum", "journal_mode", "locking_mode") and self.con.in_transaction:
            raise sqlite3.OperationalError("write or revert the changes first")
        self.con.execute(f"PRAGMA {name} = {v if isinstance(v, int) else literal(str(v))}")
        if name in ("page_size", "auto_vacuum"):
            self.con.execute("VACUUM")                         # these take effect only after a VACUUM

    # ----------------------------------------------------------------- tables, indices
    def create_table(self, name: str, columns: list[dict], without_rowid: bool = False) -> str:
        """columns: {name, type, notnull, pk, autoincrement, unique, default, check}. Returns the SQL run."""
        sql = create_table_sql(name, columns, without_rowid)
        self._begin()
        self.con.execute(sql)
        self.changes.append(f"table {name} created")
        return sql

    def rename_table(self, old: str, new: str):
        self._begin()
        self.con.execute(f"ALTER TABLE {quote(old)} RENAME TO {quote(new)}")
        self.changes.append(f"table {old} renamed")

    def add_column(self, table: str, col: dict):
        self._begin()
        self.con.execute(f"ALTER TABLE {quote(table)} ADD COLUMN {column_sql(col)}")
        self.changes.append(f"{table}: column {col['name']} added")

    def rename_column(self, table: str, old: str, new: str):
        self._begin()
        self.con.execute(f"ALTER TABLE {quote(table)} RENAME COLUMN {quote(old)} TO {quote(new)}")
        self.changes.append(f"{table}: column {old} renamed")

    def drop_column(self, table: str, name: str):
        self._begin()
        self.con.execute(f"ALTER TABLE {quote(table)} DROP COLUMN {quote(name)}")
        self.changes.append(f"{table}: column {name} removed")

    def drop(self, kind: str, name: str):
        """Delete a table / view / index / trigger."""
        if kind not in ("table", "view", "index", "trigger"):
            raise ValueError(kind)
        self._begin()
        self.con.execute(f"DROP {kind.upper()} {quote(name)}")
        self.changes.append(f"{kind} {name} deleted")

    def create_index(self, name: str, table: str, columns: list[str], unique: bool = False) -> str:
        sql = f"CREATE {'UNIQUE ' if unique else ''}INDEX {quote(name)} ON {quote(table)} (" + \
              ", ".join(quote(c) for c in columns) + ")"
        self._begin()
        self.con.execute(sql)
        self.changes.append(f"index {name} created")
        return sql

    # ----------------------------------------------------------------- tools
    def integrity_check(self) -> list[str]:
        return [r[0] for r in self.con.execute("PRAGMA integrity_check").fetchall()]

    def foreign_key_check(self) -> list[tuple]:
        return self.con.execute("PRAGMA foreign_key_check").fetchall()

    def vacuum(self):
        """Compact Database: needs the changes written first."""
        if self.con.in_transaction:
            raise sqlite3.OperationalError("write or revert the changes first")
        self.con.execute("VACUUM")

    def size(self) -> int:
        try:
            return os.path.getsize(self.path) if self.path else 0
        except OSError:
            return 0

    # ----------------------------------------------------------------- import / export
    def export_csv(self, table: str, path: str, sep: str = ",", quotechar: str = '"', header: bool = True,
                   newline: str = "\r\n") -> int:
        cols, _ = self.rows(table, limit=0)
        n = 0
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=sep, quotechar=quotechar or '"', lineterminator=newline,
                           quoting=csv.QUOTE_MINIMAL if quotechar else csv.QUOTE_NONE, escapechar=None if quotechar else "\\")
            if header:
                w.writerow(cols)
            cur = self.con.execute(f"SELECT {', '.join(quote(c) for c in cols)} FROM {quote(table)}")
            while True:
                batch = cur.fetchmany(5000)
                if not batch:
                    break
                for r in batch:
                    w.writerow(["" if v is None else (v.hex() if isinstance(v, bytes) else v) for v in r])
                    n += 1
        return n

    def import_csv(self, path: str, table: str, header: bool = True, sep: str = ",", quotechar: str = '"',
                   encoding: str = "utf-8-sig") -> int:
        """Into a new table (columns from the header, types guessed) or appended to an existing one with as many
        columns. One step of the open transaction (Revert undoes it)."""
        with open(path, encoding=encoding, newline="") as f:
            rows = list(csv.reader(f, delimiter=sep, quotechar=quotechar or '"'))
        if not rows:
            return 0
        names = rows[0] if header else [f"field{i + 1}" for i in range(len(rows[0]))]
        data = rows[1:] if header else rows
        width = len(names)
        data = [(r + [""] * width)[:width] for r in data]
        values = [[_typed(v) for v in r] for r in data]
        self._begin()
        if self.kind(table) != "table":
            types = []
            for i in range(width):
                col = [r[i] for r in values if r[i] is not None]
                types.append("INTEGER" if col and all(isinstance(v, int) for v in col) else
                             "REAL" if col and all(isinstance(v, (int, float)) for v in col) else "TEXT")
            self.con.execute(create_table_sql(table, [{"name": n or f"field{i + 1}", "type": t}
                                                      for i, (n, t) in enumerate(zip(names, types))]))
        elif len(self.columns(table)) != width:
            raise ValueError(f"{table} has {len(self.columns(table))} columns, the file {width}")
        marks = ", ".join("?" * width)
        self.con.executemany(f"INSERT INTO {quote(table)} VALUES ({marks})", values)
        self.changes.append(f"{len(values)} row(s) imported into {table}")
        return len(values)

    def export_sql(self, path: str, tables: list[str] | None = None, schema: bool = True, data: bool = True,
                   keep_old_schema: bool = False) -> int:
        """File > Export > Database to SQL file: CREATE statements and INSERTs in one transaction."""
        objs = self.objects()
        chosen = set(tables) if tables else {o.name for o in objs if o.type == "table"}
        out = io.StringIO()
        out.write("BEGIN TRANSACTION;\n")
        n = 0
        for o in objs:
            if o.type == "table" and o.name in chosen:
                if schema:
                    sql = o.sql
                    if keep_old_schema:
                        sql = re.sub(r"^\s*CREATE\s+TABLE\s+", "CREATE TABLE IF NOT EXISTS ", sql, flags=re.I)
                    out.write(sql.rstrip(";") + ";\n")
                if data:
                    cols = [c.name for c in self.columns(o.name)]
                    cur = self.con.execute(f"SELECT {', '.join(quote(c) for c in cols)} FROM {quote(o.name)}")
                    for r in cur:
                        out.write(f"INSERT INTO {quote(o.name)} ({', '.join(quote(c) for c in cols)}) VALUES (" +
                                  ", ".join(literal(v) for v in r) + ");\n")
                        n += 1
        if schema:
            for o in objs:
                if o.type in ("index", "view", "trigger") and o.sql and (o.table in chosen or o.type == "view"):
                    out.write(o.sql.rstrip(";") + ";\n")
        out.write("COMMIT;\n")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(out.getvalue())
        return n

    def import_sql(self, path: str) -> Result:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        text = re.sub(r"^\s*BEGIN TRANSACTION;\s*", "", text, flags=re.I)
        text = re.sub(r"\s*COMMIT;\s*$", "", text, flags=re.I)
        return self.execute(text)


# --------------------------------------------------------------------- helpers
def _regexp(pattern, value) -> bool:
    if value is None or pattern is None:
        return False
    try:
        return re.search(pattern, str(value)) is not None
    except re.error:
        return False


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _number(text: str):
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def filter_sql(col: str, text: str) -> tuple[str, list]:
    """DB Browser's filter syntax for one column."""
    t = text.strip()
    up = t.upper()
    if up in ("=NULL", "IS NULL"):
        return f"{col} IS NULL", []
    if up in ("<>NULL", "!=NULL", "IS NOT NULL"):
        return f"{col} IS NOT NULL", []
    if len(t) > 2 and t.startswith("/") and t.endswith("/"):
        return f"{col} REGEXP ?", [t[1:-1]]
    m = re.match(r"^(-?[\d.]+)\s*~\s*(-?[\d.]+)$", t)
    if m:
        return f"{col} BETWEEN ? AND ?", [_number(m.group(1)), _number(m.group(2))]
    for op in ("<>", "!=", ">=", "<=", "=", ">", "<"):
        if t.startswith(op):
            v = t[len(op):].strip()
            return f"{col} {'<>' if op == '!=' else op} ?", [_number(v)]
    return f"{col} LIKE ? ESCAPE '\\'", [f"%{_like(t)}%"]


def _typed(v: str):
    if v == "":
        return None
    if re.fullmatch(r"-?\d{1,18}", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d*(e-?\d+)?|-?\d*\.\d+(e-?\d+)?", v, re.I):
        return float(v)
    return v


def column_sql(c: dict) -> str:
    s = quote(c["name"])
    if c.get("type"):
        s += " " + c["type"]
    if c.get("notnull"):
        s += " NOT NULL"
    if c.get("unique"):
        s += " UNIQUE"
    if c.get("default") not in (None, ""):
        d = str(c["default"])
        s += " DEFAULT " + (d if re.fullmatch(r"-?[\d.]+|NULL|CURRENT_(TIME|DATE|TIMESTAMP)|\(.*\)", d, re.I)
                            else literal(d))
    if c.get("check"):
        s += f" CHECK ({c['check']})"
    return s


def create_table_sql(name: str, columns: list[dict], without_rowid: bool = False) -> str:
    if not columns:
        raise ValueError("a table needs at least one column")
    pks = [c for c in columns if c.get("pk")]
    defs = []
    for c in columns:
        s = column_sql(c)
        if len(pks) == 1 and c.get("pk"):
            s = s.replace(quote(c["name"]) + (" " + c["type"] if c.get("type") else ""),
                          quote(c["name"]) + (" " + c["type"] if c.get("type") else "") + " PRIMARY KEY" +
                          (" AUTOINCREMENT" if c.get("autoincrement") else ""), 1)
        defs.append(s)
    if len(pks) > 1:
        defs.append("PRIMARY KEY (" + ", ".join(quote(c["name"]) for c in pks) + ")")
    return f"CREATE TABLE {quote(name)} (\n\t" + ",\n\t".join(defs) + "\n)" + (" WITHOUT ROWID" if without_rowid else "")
