"""Rebuildable, transactionally maintained activity summaries and query indexes."""

GRADED = "('lesson','quiz','stage_assessment','remedial')"


def initialize(db):
    """The caller holds the schema lock. Original events are never rewritten."""
    row = db.execute("SELECT value FROM kv WHERE key='performance_schema'").fetchone()
    if row and row[0] == '1':
        return
    db.executescript('''
        CREATE INDEX IF NOT EXISTS content_kind_created ON content(kind,created);
        CREATE INDEX IF NOT EXISTS content_lesson_number ON content(kind,json_extract(data,'$.source'),COALESCE(json_extract(data,'$.lesson_no'),json_extract(data,'$.day')));
        CREATE INDEX IF NOT EXISTS events_ref_created ON events(ref,created);
        CREATE INDEX IF NOT EXISTS events_kind_created ON events(kind,created);
        CREATE INDEX IF NOT EXISTS events_created ON events(created);
        CREATE INDEX IF NOT EXISTS cards_due ON cards(due);
        CREATE INDEX IF NOT EXISTS cards_example ON cards(json_extract(data,'$.example'));
        CREATE TABLE IF NOT EXISTS activity_daily(day TEXT PRIMARY KEY,count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_kind(kind TEXT PRIMARY KEY,count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_skill(skill TEXT PRIMARY KEY,correct INTEGER NOT NULL,total INTEGER NOT NULL);
    ''')
    # Installation and backfill share a transaction, so interruption cannot leave
    # partially counted history or install triggers twice.
    statements = ['BEGIN IMMEDIATE;',
                  'DELETE FROM activity_daily;', 'DELETE FROM activity_kind;', 'DELETE FROM activity_skill;',
                  'INSERT INTO activity_daily SELECT substr(created,1,10),COUNT(*) FROM events GROUP BY substr(created,1,10);',
                  'INSERT INTO activity_kind SELECT kind,COUNT(*) FROM events GROUP BY kind;',
                  f"""INSERT INTO activity_skill
                      SELECT json_extract(j.value,'$.skill'),SUM(COALESCE(json_extract(j.value,'$.correct'),0)),COUNT(*)
                      FROM events e,json_each(e.data,'$.results') j WHERE e.kind IN {GRADED}
                      GROUP BY json_extract(j.value,'$.skill');"""]
    for operation in ('INSERT', 'DELETE', 'UPDATE'):
        name = 'activity_' + operation.lower()
        body = []
        if operation in ('DELETE', 'UPDATE'):
            body += ["UPDATE activity_daily SET count=count-1 WHERE day=substr(old.created,1,10);",
                     "UPDATE activity_kind SET count=count-1 WHERE kind=old.kind;",
                     f"""UPDATE activity_skill SET
                         correct=correct-COALESCE((SELECT SUM(COALESCE(json_extract(value,'$.correct'),0)) FROM json_each(old.data,'$.results') WHERE json_extract(value,'$.skill')=activity_skill.skill),0),
                         total=total-(SELECT COUNT(*) FROM json_each(old.data,'$.results') WHERE json_extract(value,'$.skill')=activity_skill.skill)
                         WHERE old.kind IN {GRADED};"""]
        if operation in ('INSERT', 'UPDATE'):
            body += ["INSERT INTO activity_daily VALUES(substr(new.created,1,10),1) ON CONFLICT(day) DO UPDATE SET count=count+1;",
                     "INSERT INTO activity_kind VALUES(new.kind,1) ON CONFLICT(kind) DO UPDATE SET count=count+1;",
                     f"""INSERT INTO activity_skill
                         SELECT json_extract(value,'$.skill'),SUM(COALESCE(json_extract(value,'$.correct'),0)),COUNT(*)
                         FROM json_each(new.data,'$.results') WHERE new.kind IN {GRADED}
                         GROUP BY json_extract(value,'$.skill')
                         ON CONFLICT(skill) DO UPDATE SET correct=correct+excluded.correct,total=total+excluded.total;"""]
        statements.append(f'DROP TRIGGER IF EXISTS {name};')
        statements.append(f'CREATE TRIGGER {name} AFTER {operation} ON events BEGIN ' + '\n'.join(body) + ' END;')
    statements += ["INSERT OR REPLACE INTO kv VALUES('performance_schema','1');", 'COMMIT;']
    try:
        db.executescript('\n'.join(statements))
    except Exception:
        db.rollback()
        raise
