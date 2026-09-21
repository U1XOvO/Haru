"""Offline behavioral checks; fixtures do not establish live teaching quality."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from service import Service
from curriculum import CARDS, SEEDS
from learning import moras, pronunciation
from llm import AppError, partial_jp


def answer(**kwargs):
    return dict(jp='水をください。',kana='みずをください。',romaji='Mizu o kudasai.',zh='请给我水。',
                feedback='中文提示',suggestion='ありがとうございます。',pending_task='请说明数量。',**kwargs)


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Service(self.temp.name)
    def tearDown(self):self.app.close();self.temp.cleanup()

    def test_annotation_round_trip_cache_and_invalid_input(self):
        tokens=[dict(surface='水',reading='みず',lemma='水',meaning='水'),dict(surface='。',reading='',lemma='',meaning='')]
        with patch('service.generate',return_value=({'tokens':tokens},'fixture')) as ai:
            first=self.app.annotate({'text':'水。'})
            self.assertEqual(self.app.annotate({'text':'水。'}),first);self.assertEqual(ai.call_count,1)
            with self.assertRaisesRegex(AppError,'不一致'):self.app.annotate({'text':'猫。'})
        self.assertEqual(len(self.app.reading_cache()),1)
        self.app.close();self.app=Service(self.temp.name)
        self.assertEqual(self.app.reading_cache()[0],first)

    def test_dictionary_add_reuses_exact_saved_card_without_ai(self):
        before=self.app.stats()
        with patch('service.generate',side_effect=AssertionError('unneeded API')):
            d=self.app.dictionary({'word':'水','sentence':'水をください。'})
            self.assertEqual(d['word'],'水')
            self.app.dictionary_add({'word':'水'});self.app.dictionary_add({'word':'水'})
        self.assertEqual(len(self.app.cards({})),1)
        self.assertEqual(self.app.stats()['streak'],before['streak'])

    def test_inflected_lookup_preserves_surface_and_lemma(self):
        tokens=[dict(surface='食べました',reading='たべました',lemma='食べる',meaning='吃了')]
        card=dict(CARDS[0],word='食べる',reading='たべる',meaning='吃')
        self.app.add_card(card)
        with patch('service.generate',return_value=({'tokens':tokens},'fixture')):self.app.annotate({'text':'食べました'})
        with patch('service.generate',side_effect=AssertionError('unneeded API')):
            d=self.app.dictionary({'word':'食べました','sentence':'食べました'})
        self.assertEqual((d['word'],d['queried'],d['context_meaning']),('食べる','食べました','吃了'))

    def test_passive_exposure_is_idempotent_and_separate_from_review(self):
        lesson=self.app.lesson({'day':4});self.app.card_seed({})
        before=self.app.cards({})
        self.app.encounter({'ref':lesson['id']});self.app.encounter({'ref':lesson['id']})
        self.app.dictionary({'word':'水'});self.app.dictionary({'word':'水'})
        d=next(x for x in self.app.knowledge({}) if x['word']=='水')
        self.assertEqual((d['exposure'],d['lookup'],d['reviews']),(1,1,0))
        self.assertEqual(self.app.cards({}),before)
        card=next(c for c in before if c['word']=='水');self.app.review({'id':card['id'],'quality':1})
        d=next(x for x in self.app.knowledge({}) if x['word']=='水')
        self.assertEqual((d['reviews'],d['recalled']),(1,0))

    def test_exposure_does_not_count_kanji_substrings(self):
        d=self.app.save('immersion',{'sentences':[dict(jp='日本語です。')]})
        self.app.encounter({'ref':d['id']})
        self.assertFalse(any(x['word']=='本' for x in self.app.knowledge({})))

    def test_due_targets_are_given_to_generation_and_coverage_is_inspectable(self):
        self.app.card_seed({});contexts=[]
        def gen(task,context,schema):
            contexts.append(context);return copy.deepcopy(SEEDS[3]),'fixture'
        with patch('service.generate',side_effect=gen):d=self.app.lesson({'day':4,'source':'ai'})
        self.assertEqual(len(contexts[0]['review_targets']),3)
        self.assertTrue(all('covered' in t and t['card_id'] for t in d['review_targets']))
        self.assertEqual(self.app.stats()['today'],0)
        self.assertTrue(all(w['reviews']==0 for w in self.app.knowledge({})))

    def test_unknown_pitch_not_fabricated_and_moras(self):
        self.assertEqual(moras('きょう'),['きょ','う'])
        self.assertEqual(moras('コーヒー'),['コ','ー','ヒ','ー'])
        self.assertEqual(moras('きって'),['き','っ','て'])
        self.assertEqual(moras('本'),[])
        self.assertIsNone(pronunciation('猫','ねこ')['nucleus'])
        self.assertEqual(pronunciation('雨','あめ')['pattern'],['高','低'])
        self.assertEqual(pronunciation('飴','あめ')['nucleus'],0)
        self.assertIsNone(pronunciation('雨','さめ')['nucleus'])

    def test_task_memory_persistence_and_report_evidence(self):
        run=self.app.chat_start({'scene':'cafe'})
        self.assertEqual(self.app.chat_start({'scene':'cafe'})['session'],run['session'])
        with patch('service.generate',return_value=(answer(),'fixture')):
            for _ in range(10):self.app.chat({'session':run['session'],'message':'水をください。'})
        memory=self.app.chat_memory(run['session'])
        self.assertEqual(memory['pending_task'],'请说明数量。');self.assertTrue(memory['summary'])
        mid=self.app.db.execute("SELECT id FROM messages WHERE role='user' LIMIT 1").fetchone()[0]
        report=dict(summary='还有两项可以继续练习。',goals=[dict(met=True,message_id=mid,quote='水をください。',note='已点饮品'),dict(met=False,message_id=0,quote='',note='练习数量'),dict(met=False,message_id=0,quote='',note='练习价格')])
        with patch('service.generate',return_value=(report,'fixture')):d=self.app.chat_finish({'session':run['session']})
        self.assertTrue(d['goals'][0]['met']);self.assertFalse(d['goals'][1]['met'])
        self.assertEqual(self.app.get('mistakes',[]),[]) # Excluded feature stays excluded.
        self.assertEqual(self.app.chat_state({'scene':'cafe'})['status'],'finished')
        next_run=self.app.chat_start({'scene':'cafe','retry':True})
        self.assertNotEqual(next_run['session'],run['session'])
        self.assertTrue(self.app.chat_memory(next_run['session'])['previous'])
        self.assertEqual(len(self.app.chat_history({'session':run['session']})),20)

    def test_task_report_rejects_invented_or_assistant_evidence(self):
        run=self.app.chat_start({'scene':'cafe'})
        with patch('service.generate',return_value=(answer(),'fixture')):
            self.app.chat({'session':run['session'],'message':'请教我一句日语。'})
        mid=self.app.db.execute("SELECT id FROM messages WHERE role='assistant'").fetchone()[0]
        report=dict(summary='完成',goals=[dict(met=True,message_id=mid,quote='水をください。',note='完成')]*3)
        with patch('service.generate',return_value=(report,'fixture')):
            with self.assertRaisesRegex(AppError,'原话证据'):self.app.chat_finish({'session':run['session']})
        self.assertEqual(self.app.chat_state({'scene':'cafe'})['status'],'active')

    def test_stream_cancel_never_persists_half_turn(self):
        token='cancel-request-123456789'
        def stream(task,context,schema,emit,cancelled):
            emit({'type':'delta','text':'水'})
            other=Service(self.temp.name)
            try:other.chat_cancel({'request_id':token})
            finally:other.close()
            self.assertTrue(cancelled())
            return answer(),'fixture'
        emitted=[]
        with patch('conversation.generate_stream',side_effect=stream):
            with self.assertRaisesRegex(AppError,'停止'):self.app.chat_stream({'session':'cafe','message':'こんにちは','request_id':token},emitted.append)
        self.assertTrue(emitted);self.assertEqual(self.app.chat_history({'session':'cafe'}),[])
        self.assertEqual(self.app.stats()['streak'],0)

    def test_cancel_before_start_and_after_commit(self):
        token='cancel-before-123456789'
        self.app.chat_cancel({'request_id':token})
        with patch('conversation.generate_stream') as gen:
            with self.assertRaises(AppError):self.app.chat_stream({'session':'cafe','message':'こんにちは','request_id':token},lambda x:None)
            gen.assert_not_called()
        token='completed-request-123456789'
        with patch('conversation.generate_stream',return_value=(answer(),'fixture')):
            self.app.chat_stream({'session':'cafe','message':'こんにちは','request_id':token},lambda x:None)
        self.assertEqual(self.app.chat_cancel({'request_id':token})['status'],'complete')
        self.assertEqual(len(self.app.chat_history({'session':'cafe'})),2)

    def test_stream_failure_and_duplicate_request_are_not_saved(self):
        token='failed-request-123456789'
        with patch('conversation.generate_stream',side_effect=AppError('中断')):
            with self.assertRaises(AppError):self.app.chat_stream({'session':'cafe','message':'こんにちは','request_id':token},lambda x:None)
        with self.assertRaises(AppError):self.app.chat_stream({'session':'cafe','message':'こんにちは','request_id':token},lambda x:None)
        self.assertEqual(self.app.chat_history({'session':'cafe'}),[])

    def test_export_includes_new_records_without_request_payloads(self):
        self.app.dictionary({'word':'水'})
        session=self.app.chat_start({'scene':'store'})['session']
        with patch('conversation.generate_stream',return_value=(answer(),'fixture')):
            self.app.chat_stream({'session':session,'message':'水をください。',
                                  'request_id':'export-evidence-request-1'},lambda _:None)
        message_id=self.app.db.execute(
            "SELECT id FROM messages WHERE session=? AND role='user'",(session,)).fetchone()[0]
        results=[dict(met=True,message_id=message_id,quote='水をください。',note='表达了购买需求')]
        results.extend(dict(met=False,message_id=0,quote='',note='继续练习该场景表达') for _ in range(2))
        with patch('service.generate',return_value=(dict(goals=results,summary='已表达购买需求。'),'fixture')):
            self.app.chat_finish({'session':session})
        data=json.loads(Path(self.app.export({})['path']).read_text())
        self.assertEqual(data['version'],3);self.assertTrue(data['dictionary']);self.assertTrue(data['chat_runs'])
        messages=data['messages'];message_ids={row['id'] for row in messages}
        self.assertEqual(len(messages),2)
        self.assertEqual({row['role'] for row in messages},{'user','assistant'})
        self.assertTrue(all(row['session']==session for row in messages))
        run=next(row for row in data['chat_runs'] if row['id']==session)
        report=json.loads(run['report'])
        self.assertIn(report['goals'][0]['message_id'],message_ids)
        self.assertNotIn('chat_requests',data)

    def test_partial_json_escapes_do_not_leak_raw_json(self):
        self.assertEqual(partial_jp('{"jp":"水を\\u304'),'水を')
        self.assertEqual(partial_jp('{"jp":"水を\\u304fださい。","zh":'), '水をください。')
        self.assertEqual(partial_jp('{"jp":"a\\"b"}'),'a"b')

if __name__=='__main__':unittest.main()
