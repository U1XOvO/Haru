"""Offline regression checks for summaries, bounded IPC and AI request reuse."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from curriculum import CARDS
from service import Service
from llm import AppError


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.app = Service(self.folder.name)
        self.addCleanup(lambda: self.app.close())

    def test_activity_summary_preserves_updates_deletes_and_rollback(self):
        db = self.app.db
        rows = [
            ('a','lesson','x',dict(results=[dict(skill='kana',correct=True),dict(skill='listening',correct=False)]),'2026-09-01T09:00:00'),
            ('b','review','word',dict(quality=4,word='水'),'2026-09-02T09:00:00'),
            ('c','remedial','y',dict(results=[dict(skill='kana',correct=False)]),'2026-09-02T09:00:00')]
        with db:
            db.executemany('INSERT INTO events VALUES(?,?,?,?,?)', [(a,b,c,json.dumps(d),e) for a,b,c,d,e in rows])
        result = self.app.stats(done={},progress={'next_lesson':1})
        self.assertEqual(result['skills']['kana'], dict(correct=1,total=2))
        self.assertEqual(result['skills']['listening'], dict(correct=0,total=1))
        self.assertEqual(result['attempts'],2)
        with db:
            db.execute("UPDATE events SET data=?,created='2026-09-03T09:00:00' WHERE id='c'", (json.dumps(dict(results=[dict(skill='kana',correct=True)])),))
            db.execute("DELETE FROM events WHERE id='a'")
        try:
            with db:
                self.app.event('quiz','rollback',dict(results=[dict(skill='grammar',correct=True)]))
                raise RuntimeError('rollback')
        except RuntimeError:
            pass
        result = self.app.stats(done={},progress={'next_lesson':1})
        self.assertEqual(result['skills']['kana'], dict(correct=1,total=1))
        self.assertEqual(result['skills']['grammar'], dict(correct=0,total=0))
        self.assertEqual(result['attempts'],1)
        self.assertEqual(dict(db.execute('SELECT day,count FROM activity_daily WHERE count>0')), {'2026-09-02':1,'2026-09-03':1})

    def test_summary_backfill_is_idempotent_and_uses_original_records(self):
        with self.app.db:
            self.app.event('quiz','old',dict(results=[dict(skill='grammar',correct=True)]))
            self.app.db.execute("DELETE FROM kv WHERE key='performance_schema'")
            self.app.db.execute('UPDATE activity_skill SET correct=99')
        self.app.close()
        self.app = Service(self.folder.name)
        self.assertEqual(self.app.stats(done={},progress={'next_lesson':1})['skills']['grammar'],dict(correct=1,total=1))
        self.app.close()
        self.app = Service(self.folder.name)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
        self.assertEqual(self.app.stats(done={},progress={'next_lesson':1})['attempts'],1)

    def test_bootstrap_defers_annotation_and_knowledge_details(self):
        queries = []
        self.app.db.set_trace_callback(queries.append)
        try:
            with patch.object(self.app,'knowledge',side_effect=AssertionError('eager knowledge')):
                result = self.app.bootstrap({})
        finally:
            self.app.db.set_trace_callback(None)
        self.assertFalse(any('annotations' in query.lower() for query in queries))
        self.assertNotIn('annotations',result)
        self.assertEqual(result['knowledge'],[])
        with patch.object(self.app,'knowledge',return_value=[{'word':'水'}]) as knowledge:
            self.assertEqual(self.app.bootstrap({'include_knowledge':True})['knowledge'],[{'word':'水'}])
            knowledge.assert_called_once()

    def test_summary_reads_do_not_deserialize_event_bodies(self):
        with self.app.db:
            self.app.event('review','r',dict(quality=4,word='水',extra='large' * 20000))
        with patch('service.json.loads',side_effect=AssertionError('event JSON read')):
            result = self.app.stats(done={},progress={'next_lesson':1})
        self.assertEqual(result['attempts'],0)

    def test_chat_snapshot_batches_exposure_and_keeps_it_idempotent(self):
        d=dict(jp='水をください。',kana='みずをください。',zh='请给我水。',pending_task='どうぞ')
        with self.app.db:
            for _ in range(25):
                self.app.db.execute("INSERT INTO messages(session,role,data,created) VALUES('cafe','user',?,'2026-09-22')",(json.dumps({'text':'こんにちは'}),))
                self.app.db.execute("INSERT INTO messages(session,role,data,created) VALUES('cafe','assistant',?,'2026-09-22')",(json.dumps(d),))
        snapshot = self.app.route('chat_snapshot',{'scene':'cafe'})
        self.assertEqual(len(snapshot['messages']),40)
        self.assertEqual(len(snapshot['exposure_refs']),20)
        self.assertEqual(snapshot['memory']['pending_task'],'どうぞ')
        count = self.app.db.execute('SELECT COUNT(*) FROM encounters').fetchone()[0]
        self.app.route('chat_snapshot',{'scene':'cafe'})
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM encounters').fetchone()[0],count)
        self.assertEqual(count,20)

    def test_reading_lookup_only_returns_requested_saved_sentences(self):
        with self.app.db:
            for sentence in ['水','猫']:
                self.app.db.execute('INSERT INTO annotations VALUES(?,?)',(sentence,json.dumps(dict(sentence=sentence,tokens=[]))))
        self.assertEqual(self.app.route('reading_lookup',{'sentences':['水','未知']}),dict(items=[dict(sentence='水',tokens=[])],checked=['水','未知']))
        with self.assertRaises(AppError): self.app.reading_lookup({'sentences':['水']*17})

    def test_reading_budget_does_not_mark_deferred_sentences_checked(self):
        with self.app.db:
            for sentence in ['水','猫']:
                self.app.db.execute('INSERT INTO annotations VALUES(?,?)',(sentence,json.dumps(dict(sentence=sentence,tokens=[],padding='x'*800000))))
        first=self.app.reading_lookup({'sentences':['水','猫']})
        self.assertEqual(first['checked'],['水'])
        self.assertEqual(self.app.reading_lookup({'sentences':['猫']})['checked'],['猫'])

    def test_finish_clears_expired_request_after_reopen(self):
        session=self.app.chat_start({'scene':'cafe'})['session']
        with self.app.db:
            self.app.db.execute('INSERT INTO messages(session,role,data,created) VALUES(?,?,?,?)',(session,'user',json.dumps({'text':'こんにちは'}),'2026-09-22'))
            self.app.db.execute("INSERT INTO chat_requests VALUES('stale',?,'active',0)",(session,))
        self.app.close();self.app=Service(self.folder.name)
        report=dict(summary='再练一次',goals=[dict(met=False,message_id=0,quote='',note='继续练习') for _ in range(3)])
        with patch.object(self.app,'ai',return_value=report): self.app.chat_finish({'session':session})
        self.assertEqual(self.app.db.execute("SELECT status FROM chat_requests WHERE id='stale'").fetchone()[0],'failed')

    def test_daily_word_reuses_date_and_stage_but_explicit_refresh_changes_it(self):
        fixture=dict(CARDS[0],source='AI生成',model='fixture')
        with patch.object(self.app,'ai',return_value=fixture) as generate:
            first=self.app.daily_word({})
            self.assertEqual(self.app.daily_word({})['id'],first['id'])
            self.app.close()
            self.app=Service(self.folder.name)
            with patch.object(self.app,'ai',return_value=fixture) as after_restart:
                self.assertEqual(self.app.daily_word({})['id'],first['id'])
                after_restart.assert_not_called()
                changed=self.app.daily_word({'refresh':True})
                self.assertNotEqual(changed['id'],first['id'])
                after_restart.assert_called_once()
            generate.assert_called_once()

    def test_daily_cache_expiry_and_failed_refresh_keep_saved_content(self):
        fixture=dict(CARDS[0],source='AI生成',model='fixture')
        with patch.object(self.app,'ai',return_value=fixture): first=self.app.daily_word({})
        with patch.object(self.app,'ai',side_effect=AppError('offline')):
            with self.assertRaises(AppError): self.app.daily_word({'refresh':True})
            self.assertEqual(self.app.daily_word({})['id'],first['id'])
        cached=self.app.get('daily_word_cache');cached['date']='2000-01-01'
        with self.app.db: self.app.set('daily_word_cache',cached)
        with patch.object(self.app,'ai',return_value=fixture) as generate:
            self.assertNotEqual(self.app.daily_word({})['id'],first['id'])
            generate.assert_called_once()

    def test_concurrent_daily_requests_share_one_generation(self):
        entered=threading.Event();release=threading.Event();results=[];errors=[];calls=[]
        def generate(service,*args):
            calls.append(1);entered.set()
            if not release.wait(3): raise AssertionError('generation was not released')
            return dict(CARDS[0],source='AI生成',model='fixture')
        def request():
            app=None
            try:
                app=Service(self.folder.name);results.append(app.daily_word({})['id'])
            except Exception as error: errors.append(error)
            finally:
                if app:app.close()
        with patch.object(Service,'ai',generate):
            first=threading.Thread(target=request);first.start()
            self.assertTrue(entered.wait(3))
            second=threading.Thread(target=request);second.start();release.set()
            first.join(5);second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(len(calls),1)
        self.assertEqual(len(results),2);self.assertEqual(results[0],results[1])

    def test_random_prompt_is_bounded_but_local_dedup_checks_entire_collection(self):
        cards=[dict(CARDS[0],word=f'単語{i}') for i in range(125)]
        self.app.store_cards(cards)
        repeated=dict(CARDS[0],word='単語0')  # Outside the 100 recent prompt exclusions.
        fresh=dict(CARDS[0],word='新語')
        with patch.object(self.app,'ai',side_effect=[dict(cards=[repeated],source='AI生成',model='fixture'),dict(cards=[fresh],source='AI生成',model='fixture')]) as generate:
            result=self.app.card_random({'count':1,'compact':True})
        self.assertEqual(result['generated'][0]['word'],'新語')
        self.assertNotIn('cards',result)
        for call in generate.call_args_list: self.assertLessEqual(len(call.args[1]['exclude_words']),100)
        self.assertIn('単語0',generate.call_args_list[1].args[1]['exclude_words'])

    def test_real_subprocess_bridge_accepts_new_bounded_actions(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'native'))
        from desktop_bridge import Backend
        env={k:v for k,v in os.environ.items() if not k.startswith(('LLM_','OPENAI_','DEEPSEEK_'))}
        env.update(HARU_STORAGE_DIR=self.folder.name)
        with patch.dict(os.environ,env,clear=True):
            backend=Backend(Path(self.folder.name),lambda *_:None)
            try:
                for index,(action,params) in enumerate([
                    ('card_seed',{'compact':True}),('card_queue',{}),('cards_page',{}),
                    ('chat_snapshot',{'scene':'cafe'}),('reading_lookup',{'sentences':['水']}),('bootstrap',{})]):
                    result=backend.request(dict(id=index,action=action,params=params))
                    self.assertTrue(result.get('ok'),(action,result.get('error')))
                    self.assertLess(len(json.dumps(result,ensure_ascii=False).encode()),2_000_000)
            finally:backend.close()


if __name__ == '__main__':
    unittest.main()
