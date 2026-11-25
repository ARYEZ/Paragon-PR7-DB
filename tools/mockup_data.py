"""The made-up studio database of the mock-up and the screenshots (real SQLite)."""
import sqlite3


def studio(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path)
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
    db.commit()
    return db
