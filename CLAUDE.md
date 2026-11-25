# Paragon PR7-DB: notes for Claude

**Paragon PR7-DB** is the owner's personal SQLite browser: Python (**PySide6** + the standard `sqlite3`) that **works the
way DB Browser for SQLite works**, in the Paragon family look (PR7-Z ... PR7-WM, PR7-HX `ARYEZ/Paragon-PR7-HX`).
Windows 10/11 x64 is the target; it runs on Linux for tests. The owner writes Python; explain the why.

## How it's built

- `pr7db/database.py` (no Qt): `Database` (isolation_level=None, check_same_thread=False so Execute SQL can run on a
  thread; read-only via a `mode=ro` URI; REGEXP registered). **Every change goes into one open transaction**
  (`_begin`): `write()` commits (Write Changes), `revert()` rolls back, `dirty` = `in_transaction`, `changes` lists what
  waits. Statements SQLite won't run in a transaction (`NEEDS_NO_TRANSACTION`: VACUUM, some PRAGMAs) need the changes
  written first; `READS` (SELECT / VALUES / EXPLAIN / a reading PRAGMA) don't open one. `split_statements` uses
  `sqlite3.complete_statement` (a ; in a string or trigger body doesn't split) and gives each statement's own span;
  `execute` runs them in turn, the last one's cursor is the result, an error reports `failed_at`. Browse Data:
  `rows(offset, limit)` with rowid first (None for views / WITHOUT ROWID: read-only), `filter_sql` = DB Browser's filter
  syntax, `set_cell` / `insert_row` / `delete_rows` by rowid. Pragmas (`PRAGMAS`, page_size / auto_vacuum run VACUUM),
  create / alter table (ALTER TABLE rename / add / rename column / drop column), indices, CSV and SQL import / export.
- `pr7db/models.py`: `TableModel` / `ResultModel` (plain list models, never QStandardItemModel: PR7-Z's crash) fetch PAGE
  rows at a time (canFetchMore / fetchMore); edited cells orange until written. `FilterHeader`: line edits under the
  header sections (viewport margin).
- `pr7db/sqledit.py`: SqlEditor (highlighter in the family's colours, gutter, `mark_error`). `dialogs.py`: TableDialog
  (`changes()` -> rename / add / rename column / drop column), IndexDialog, CsvDialog, SqlExportDialog, OptionsDialog.
- `pr7db/main_window.py`: the four tabs, DB Browser's menus and keys (`_act(..., key=)` into `acts`), one database at a
  time; every question through `_ask(kind)` (tests: `answers`; "unwritten" = write / discard / cancel); Execute SQL on
  a thread (`sync = True` inline in tests), Stop = `con.interrupt()`. Pragma numbers are QLineEdits (max_page_count is
  past Qt's int). The cell panel's mode follows each cell (`_cell_moved`).
- `pr7db/shell.py` (PR7-PM's pattern): Open with + Capabilities for .db .sqlite .sqlite3 .db3 .s3db (HKCU), never the
  default. `pr7db/app.py`: one window per user (QLocalSocket, PR7-R's Windows fix) plus a QLockFile; a hand-over opens
  the database (asking about unwritten changes); command-line modes never let an error escape; `--selftest OUT.json`,
  `--register`, `--unregister`.
- Installer `installer/pr7db.iss` (per user, AppId {59D3D2F2-BE74-4683-B47A-BEE9B3265EC5} permanent; tasks dbfiles
  (ticked, `--register`), desktopicon); CI installs it, `tools/verify_install.py installed|uninstalled`. Spec: file
  details, no UPX, PyInstaller's bootloader built in CI, Defender scan. tests/test_windows.py writes and removes Open
  with in the real HKCU on CI only.
- Updates: `pr7db/updates.py` / `update_ui.py` (the family's files, keep them in step); `family.py`: the family's AppIds
  (PR7-DB included). `pr7db/finish.py`: the family's finish, the same file in every app.

## The look (family rules)

- Theme is PR7-HX's (OBSIDIAN + AURORA). Colours: the family's only (SQL keywords orange, strings pink, changed cells
  orange, NULL dim); Inter for the UI, SQL and cell text monospaced; labels UPPER CASE; names keep their case.
- The PR7-DB mark: PR7 `Logo.cpp` geometry plus D (PR7-DU's) and B (PR7-HB's); icon "7DB" by `tools/make_icon.py`,
  wizard art by `tools/make_installer_art.py`. Mock-up: `tools/mockup.py` (data from `tools/mockup_data.py`). Check
  visuals with `QT_SCALE_FACTOR=2 python tools/screenshots.py OUT`.

## Rules

- Behaviour questions: do what DB Browser for SQLite does (tabs, filters, Write / Revert, Edit Cell, Execute SQL keys).
- Tests: `python -m pytest` (offscreen) on real database files; every write is checked by reading the file with a
  separate connection. Never the real settings (conftest) or, outside CI, the registry.
- Releases: bump `__version__` in `pr7db/__init__.py` and add a `## x.y.z` section to CHANGELOG.md, then push: CI
  publishes that version by itself once every check has passed.
