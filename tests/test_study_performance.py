"""Temporary-database regressions for snapshot/state separation and lightweight RPCs."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from llm import AppError
from service import Service
from study import Study, encode


def paper(identity='fixture', count=8, large=False):
    return dict(id=identity, title='隔离练习', level='N5', version='1', source_type='ai',
        source='test fixture', resources=[dict(kind='answer', url='https://example.invalid/answer')],
        sections=[dict(id='one', title='前半', seconds=30), dict(id='two', title='后半', seconds=30)],
        questions=[dict(id=f'q{i}', section='one' if i<count//2 else 'two', skill='grammar',
            prompt='独立试题', options=['正解', '误选'], answer=0, explanation='fixture explanation',
            grammar_ids=[], passage='SNAPSHOT_ONLY_'+'原文'*5000 if large else '', audio_text='') for i in range(count)])


class StudyPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.app = Service(self.path)
        self.addCleanup(lambda: self.app.close() if self.app else None)
        self.config = patch('study.public_config', return_value=dict(model='fixture', provider='fixture'))
        self.config.start(); self.addCleanup(self.config.stop)

    def test_saves_and_heartbeat_never_parse_or_return_question_bodies(self):
        a = self.app._start(paper(count=60, large=True), 'practice')
        snapshot = self.app.db.execute('SELECT data FROM study_attempt_snapshots').fetchone()[0]
        loads = json.loads
        def no_snapshot(value, *args, **kwargs):
            self.assertNotIn('SNAPSHOT_ONLY_', value)
            return loads(value, *args, **kwargs)
        with patch('study.json.loads', side_effect=no_snapshot):
            saved = self.app.study_save(dict(id=a['id'], revision=0, answers={'q0': 0}))
            heartbeat = self.app.study_save(dict(id=a['id'], revision=saved['revision']))
            history = self.app.study_history(dict(level='N5', paged=True))
            summary = self.app.study_summary({})
        self.assertTrue(saved['delta']); self.assertNotIn('paper', saved)
        self.assertEqual(heartbeat['answers'], {'q0': 0})
        self.assertEqual(history['items'][0]['answered'], 1)
        self.assertEqual(summary['active'], 1)
        self.assertEqual(snapshot, self.app.db.execute('SELECT data FROM study_attempt_snapshots').fetchone()[0])
        state = self.app.db.execute('SELECT data FROM study_attempt_states').fetchone()[0]
        self.assertLess(len(state), len(snapshot)//100)
        self.assertLess(len(encode(heartbeat)), len(encode(a))//100)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_attempts').fetchone()[0], 0)

    def test_concurrent_revisions_and_elapsed_cap(self):
        with patch('study.time.time', return_value=1000): a = self.app._start(paper(), 'practice')
        second = Service(self.path); self.addCleanup(second.close)
        with patch('study.time.time', return_value=1100):
            first = self.app.study_save(dict(id=a['id'], revision=0, answers={'q0': 0}))
        self.assertEqual(first['elapsed'], 30)
        with self.assertRaises(AppError): second.study_save(dict(id=a['id'], revision=0, answers={'q1': 1}))
        refreshed = second.study_attempt(dict(id=a['id']))
        with patch('study.time.time', return_value=1105):
            second.study_save(dict(id=a['id'], revision=refreshed['revision'], answers={'q1': 0}))
        final = self.app.study_attempt(dict(id=a['id']))
        self.assertEqual(final['answers'], {'q0': 0, 'q1': 0}); self.assertEqual(final['elapsed'], 35)

    def test_expiration_advances_absolute_deadlines_and_rejects_late_answers(self):
        with patch('study.time.time', return_value=1000):
            a = self.app._start(paper(), 'timed')
            untouched = self.app._start(paper('practice'), 'practice')
        self.assertNotIn('answer', a['paper']['questions'][0]); self.assertEqual(a['paper']['resources'], [])
        with patch('study.time.time', return_value=1035):
            advanced = self.app.study_save(dict(id=a['id'], revision=0, answers={'q0': 0}))
        self.assertEqual(advanced['section_index'], 1); self.assertEqual(advanced['deadline'], 1060)
        self.assertEqual(advanced['answers'], {})
        with patch('study.time.time', return_value=1036):
            with self.assertRaises(AppError):
                self.app.study_save(dict(id=a['id'], revision=advanced['revision'], answers={'q0': 0}))
        ticks = []
        original = self.app._tick
        def tick(value): ticks.append(value['id']); return original(value)
        with patch('study.time.time', return_value=1061), patch.object(self.app, '_tick', side_effect=tick):
            self.app.study_history({}); summary = self.app.study_summary({})
        self.assertEqual(ticks, [a['id']]); self.assertEqual(summary['submitted'], 1)
        finished = self.app.study_attempt(dict(id=a['id']))
        self.assertEqual(finished['status'], 'submitted'); self.assertIn('answer', finished['paper']['questions'][0])
        self.assertEqual(self.app._attempt(untouched['id'])['revision'], 0)

    def test_paged_metadata_catalog_triggers_and_history_do_not_read_bodies(self):
        for i in range(35):
            p = paper('p'+str(i), large=True)
            with self.app.db:
                self.app.db.execute('INSERT INTO study_papers VALUES (?,?,?)', (p['id'], encode(p), i))
            self.app._start(p, 'practice')
        loads = json.loads
        def no_snapshot(value, *args, **kwargs):
            self.assertNotIn('SNAPSHOT_ONLY_', value)
            return loads(value, *args, **kwargs)
        with patch('study.json.loads', side_effect=no_snapshot):
            first = self.app.study_catalog(dict(level='N5', limit=10))
            second = self.app.study_catalog(dict(level='N5', limit=10, paper_offset=10, history_offset=10))
        self.assertEqual(len(first['papers']), 10); self.assertEqual(len(first['history']), 10)
        self.assertEqual(first['papers_page']['total'], 37); self.assertEqual(first['history_page']['total'], 35)
        self.assertFalse(set(x['id'] for x in first['papers']) & set(x['id'] for x in second['papers']))
        self.assertFalse(set(x['id'] for x in first['history']) & set(x['id'] for x in second['history']))
        self.app.study_delete({'id': 'p34'})
        self.assertIsNone(self.app.db.execute("SELECT 1 FROM study_paper_catalog WHERE id='p34'").fetchone())

    def test_retry_mistakes_and_export_preserve_deleted_paper_snapshot(self):
        p = paper(count=4)
        with self.app.db: self.app.db.execute('INSERT INTO study_papers VALUES (?,?,?)', (p['id'], encode(p), 1))
        with patch('study.time.time', return_value=1000):
            a = self.app._start(p, 'practice')
            self.app.study_save(dict(id=a['id'], revision=0, answers={'q0': 1,'q1': 0,'q2': 0,'q3': 0}, finish=True))
            retry = self.app.study_retry(dict(id=a['id'], wrong_only=True))
            self.assertEqual([q['id'] for q in retry['paper']['questions']], ['q0'])
            self.app.study_save(dict(id=retry['id'], revision=0, answers={'q0': 0}, finish=True))
        self.assertEqual(self.app.study_mistakes({'level': 'N5'}), [])
        self.app.study_delete({'id': p['id']})
        saved = self.app.study_attempt(dict(id=a['id']))
        self.assertEqual(saved['paper'], p); self.assertEqual(saved['result']['correct'], 3)
        export = self.app.study_export()
        self.assertEqual(len(export['attempts']), 2); self.assertEqual(export['attempts'][0]['paper'], p)
        self.assertNotIn('_rules', export['attempts'][0]); self.assertNotIn('delta', export['attempts'][0])

    def test_grammar_scoring_and_repeated_submission_are_unchanged(self):
        a = self.app.grammar_practice({'id': 'n5-001'})
        original = self.app._attempt(a['id'])
        answers = {q['id']: q['answer'] for q in original['paper']['questions']}
        saved = self.app.study_save(dict(id=a['id'], revision=0, answers=answers, finish=True))
        progress = json.loads(self.app.db.execute("SELECT data FROM grammar_progress WHERE id='n5-001'").fetchone()[0])
        self.assertEqual(progress['attempts'], 1); self.assertEqual(progress['streak'], 1)
        self.assertTrue(progress['mastered']); self.assertEqual(progress['score'], 100)
        self.app.study_save(dict(id=a['id'], revision=saved['revision'], finish=True))
        again = json.loads(self.app.db.execute("SELECT data FROM grammar_progress WHERE id='n5-001'").fetchone()[0])
        self.assertEqual(progress, again)

    def make_legacy(self, count=52):
        self.app.close(); self.app = None
        with closing(sqlite3.connect(self.path / 'haru.sqlite3')) as db, db:
            for trigger in ('insert','update','delete'): db.execute('DROP TRIGGER study_catalog_'+trigger)
            for table in ('study_attempt_states','study_attempt_snapshots','study_paper_catalog'): db.execute('DROP TABLE '+table)
            db.execute("DELETE FROM kv WHERE key='study_attempt_layout'")
            db.execute("UPDATE kv SET value='1' WHERE key='study_schema'")
            for i in range(count):
                a = dict(id=f'old{i}', paper=paper('p'+str(i)), mode='practice', started=1000,
                    answers={'q0': 1}, flags=['q0'], section_index=0, deadline=None,
                    revision=3, status='active', elapsed=11, last_active=1001)
                if i == 1:
                    a.update(status='submitted', finished=1002,
                        result=dict(correct=0, total=8, percent=0, unanswered=7,
                            results=[dict(id=q['id'], selected=a['answers'].get(q['id']),
                                answer=0, correct=False, skill='grammar') for q in a['paper']['questions']],
                            skills={'grammar': dict(correct=0, total=8)}))
                db.execute('INSERT INTO study_attempts VALUES (?,?,?,?)', (a['id'], a['paper']['id'], encode(a), 1001+i))

    def test_legacy_migration_backup_interruption_recovery_and_reopen(self):
        self.make_legacy()
        original = Study._insert_attempt; calls = 0
        def interrupted(app, attempt, updated):
            nonlocal calls
            calls += 1
            if calls == 52: raise RuntimeError('simulated migration interruption')
            return original(app, attempt, updated)
        with patch.object(Study, '_insert_attempt', interrupted):
            with self.assertRaisesRegex(RuntimeError, 'migration interruption'): Service(self.path)
        backup = self.path / 'backups/before-study-layout-v2.sqlite3'
        self.assertTrue(backup.exists()); backup_mtime = backup.stat().st_mtime_ns
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM study_attempts').fetchone()[0], 52)
        with closing(sqlite3.connect(self.path / 'haru.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM study_attempt_states').fetchone()[0], 50)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM study_attempt_snapshots').fetchone()[0], 50)
            self.assertEqual(db.execute("SELECT value FROM kv WHERE key='study_schema'").fetchone()[0], '1')
        self.app = Service(self.path)
        self.assertEqual(backup.stat().st_mtime_ns, backup_mtime)
        self.assertEqual(self.app.get('study_schema'), 2)
        self.assertEqual(self.app.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM study_attempts').fetchone()[0], 52)
        export = self.app.study_export()
        self.assertEqual(len(export['attempts']), 52)
        old_submitted = json.loads(self.app.db.execute("SELECT data FROM study_attempts WHERE id='old1'").fetchone()[0])
        self.assertEqual(self.app._attempt('old1'), old_submitted)
        self.assertEqual(len(self.app.study_mistakes({'level': 'N5'})), 8)
        original_row = json.loads(self.app.db.execute("SELECT data FROM study_attempts WHERE id='old0'").fetchone()[0])
        self.assertEqual(self.app._attempt('old0'), original_row)
        self.app.study_save(dict(id='old0', revision=3, answers={'q1': 0}))
        self.assertEqual(json.loads(self.app.db.execute("SELECT data FROM study_attempts WHERE id='old0'").fetchone()[0]), original_row)
        self.app.close(); self.app = Service(self.path)
        self.assertEqual(self.app._attempt('old0')['revision'], 4)
        self.assertEqual(self.app._attempt('old0')['answers'], {'q0': 1, 'q1': 0})


if __name__ == '__main__': unittest.main()
