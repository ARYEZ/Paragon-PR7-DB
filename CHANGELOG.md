# Changelog

## 0.1.3

- Knows PR7-HW, PR7-BK and PR7-TM (the family list), as 0.1.2 meant to ship; the test run no longer trips over Qt's shutdown on Windows.

## 0.1.2

- The family list (`family.py`) knows the three newest Paragon apps: PR7-HW (hardware information), PR7-BK
  (backups) and PR7-TM (terminal), so links between the apps and the PR7-U offer find them.

## 0.1.1

- The family list (`family.py`) knows the four newest Paragon apps: PR7-HX (hex editor), PR7-DB (SQLite browser),
  PR7-UN (uninstaller) and PR7-LN (launcher), so links between the apps and the PR7-U offer find them.

## 0.1.0

- First release: SQLite databases, the way DB Browser for SQLite works.
- Database Structure, Browse Data (page by page, column filters, Edit Database Cell), Edit Pragmas, Execute SQL
  (highlighting, current statement, errors pointed out, Stop).
- Every change waits for Write Changes; Revert Changes throws it away.
- Create / Modify Table, Create Index; CSV and SQL import / export; Compact, Integrity and Foreign-Key checks.
- Open with for SQLite files; OBSIDIAN and AURORA finishes; Check for Updates.
