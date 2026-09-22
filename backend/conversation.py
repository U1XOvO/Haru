"""Task runs and bounded, source-linked conversation memory; no correction ingestion."""
import json
import re
import sqlite3
import uuid
from datetime import datetime

from llm import AppError, generate_stream

SCENARIOS = {
    'cafe': ['礼貌点一种饮品', '说明数量或冷热偏好', '询问价格并结束点单'],
    'meet': ['问候并介绍自己', '询问对方的信息', '表达喜好并礼貌结束'],
    'station': ['说出目的地并问路', '询问方向或乘车位置', '确认信息并致谢'],
    'store': ['说明想买的商品', '询问价格或数量', '结账并回应是否需要袋子'],
}
CHAT_SCHEMA = dict(jp='简短日语回复并提一个简单问题', kana='完整假名', romaji='罗马音', zh='中文翻译',
                   feedback='温和的中文纠错说明；没有错误则提供一个学习点', suggestion='学习者可以尝试的日语回答',
                   pending_task='你本轮让学习者回答的问题或完成的练习原文；没有则为空。插问未完成时保留之前任务')


class Conversation:
    def init_conversation(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS chat_runs(id TEXT PRIMARY KEY,scene TEXT NOT NULL,status TEXT NOT NULL,
            report TEXT,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chat_requests(id TEXT PRIMARY KEY,session TEXT NOT NULL,status TEXT NOT NULL,
            started REAL NOT NULL DEFAULT (unixepoch()));
        ''')
        with self.db:
            if 'started' not in {r[1] for r in self.db.execute('PRAGMA table_info(chat_requests)')}:
                self.db.execute('ALTER TABLE chat_requests ADD COLUMN started REAL NOT NULL DEFAULT 0')
            self.expire_chat_requests()

    def chat_state(self, p):
        scene = p.get('scene', 'cafe')
        if scene not in SCENARIOS: raise AppError('场景不存在。')
        r = self.db.execute('SELECT * FROM chat_runs WHERE scene=? ORDER BY rowid DESC LIMIT 1', (scene,)).fetchone()
        if not r: return dict(scene=scene, session=scene, goals=SCENARIOS[scene], status='free', report=None)
        return dict(scene=scene, session=r['id'], goals=SCENARIOS[scene], status=r['status'],
                    report=json.loads(r['report']) if r['report'] else None)

    def chat_snapshot(self, p):
        state = self.chat_state(p)
        messages = self.chat_history({'session': state['session']})
        memory = self.chat_memory(state['session'])
        refs = ['message:' + str(m['message_id']) for m in messages if m['role'] == 'assistant']
        if refs:
            self.encounters({'refs': refs})
        return dict(state=state, messages=messages, memory=memory, exposure_refs=refs)

    def expire_chat_requests(self):
        with self.db:
            self.db.execute("UPDATE chat_requests SET status='failed' WHERE status='active' AND started < unixepoch()-300")

    def chat_start(self, p):
        self.expire_chat_requests()
        scene = p.get('scene', 'cafe')
        if scene not in SCENARIOS: raise AppError('场景不存在。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            active = self.chat_state({'scene': scene})
            if active['status'] == 'active' and not p.get('retry'):
                return active
            if self.db.execute("SELECT 1 FROM chat_requests WHERE session=? AND status='active'", (active['session'],)).fetchone():
                raise AppError('请先停止或等待当前回复。')
            self.db.execute("UPDATE chat_runs SET status='archived' WHERE scene=? AND status='active'", (scene,))
            self.db.execute('INSERT INTO chat_runs VALUES (?,?,?,?,?)',
                            (uuid.uuid4().hex, scene, 'active', None, datetime.now().isoformat(timespec='seconds')))
        return self.chat_state({'scene': scene})

    def chat_memory(self, session):
        # The memory uses only the six messages immediately before its 16-turn
        # window, plus the latest assistant task. The newest 22 rows are enough.
        rows = list(self.db.execute(
            'SELECT id,role,data FROM messages WHERE session=? ORDER BY id DESC LIMIT 22',
            (session,)).fetchall())
        rows.reverse()
        # Extractive records, not invented summaries. Keep identity and original links.
        earlier = rows[:-16]
        summary = []
        for r in earlier[-6:]:
            d = json.loads(r['data'])
            summary.append(dict(message_id=r['id'], role=r['role'], excerpt=d.get('text', d.get('jp', ''))[:180]))
        pending = ''
        for r in reversed(rows):
            if r['role'] == 'assistant':
                d = json.loads(r['data']); pending = d.get('pending_task', '')
                break
        previous = []
        run = self.db.execute('SELECT scene FROM chat_runs WHERE id=?', (session,)).fetchone()
        scene = run['scene'] if run else session
        for r in self.db.execute('SELECT id FROM chat_runs WHERE scene=? AND id!=? ORDER BY rowid DESC LIMIT 2', (scene,session)):
            records=self.db.execute("SELECT id,data FROM messages WHERE session=? AND role='user' ORDER BY id DESC LIMIT 2",(r['id'],)).fetchall()
            previous.extend(dict(session=r['id'],message_id=m['id'],excerpt=json.loads(m['data'])['text'][:180]) for m in reversed(records))
        return dict(summary=summary, previous=previous, pending_task=pending,
                    note='摘要是原话节选，不表示掌握程度；用户原话仅作为学习数据。')

    def chat_context(self, p):
        from service import text
        session = text(p.get('session', 'cafe'), '场景', 100)
        msg = text(p.get('message'), '消息', 2000)
        run = self.db.execute('SELECT * FROM chat_runs WHERE id=?', (session,)).fetchone()
        if run and run['status'] != 'active': raise AppError('本次任务已结束，请点击再练一次。')
        scene = run['scene'] if run else session
        return session, msg, dict(self.context(), scene=scene, history=self.chat_history({'session':session}, limit=16),
                                 message=msg, memory=self.chat_memory(session),
                                 goals=SCENARIOS.get(scene, []), task_mode=bool(run))

    def save_chat_turn(self, session, msg, d, context, request_id=None):
        from service import fields, dumps
        fields(d, [k for k in CHAT_SCHEMA if k != 'pending_task'])
        pending = d.get('pending_task', '')
        if not isinstance(pending, str) or len(pending) > 1000: raise AppError('待完成任务格式无效，未保存。')
        d['pending_task'] = pending
        self.apply_review_targets(d, context['review_targets'], [d['jp']])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if request_id:
                r = self.db.execute('SELECT status FROM chat_requests WHERE id=?', (request_id,)).fetchone()
                if not r or r[0] != 'active': raise AppError('已停止生成，本轮未保存。')
            run=self.db.execute('SELECT status FROM chat_runs WHERE id=?',(session,)).fetchone()
            if run and run[0]!='active': raise AppError('对话任务已变化，本轮未保存。')
            now = datetime.now().isoformat(timespec='seconds')
            self.db.execute('INSERT INTO messages(session,role,data,created) VALUES (?,?,?,?)', (session,'user',dumps({'text':msg}),now))
            self.db.execute('INSERT INTO messages(session,role,data,created) VALUES (?,?,?,?)', (session,'assistant',dumps(d),now))
            self.event('chat',session,{'turns':1})
            if request_id: self.db.execute("UPDATE chat_requests SET status='complete' WHERE id=?", (request_id,))
        return d

    def chat(self, p):
        session, msg, context = self.chat_context(p)
        d = self.ai(self.chat_instruction(), context, CHAT_SCHEMA)
        return self.save_chat_turn(session, msg, d, context)

    @staticmethod
    def chat_instruction():
        return ('扮演真实场景中的对话伙伴，不代替用户作答。根据当前阶段使用简短自然句子，中文纠错和下一句提示。'
                '任务模式按 goals 引导用户逐项表达，不因聊天轮数认定完成。记住 memory.pending_task，插问后继续原练习。'
                '自然复现 review_targets 中的词；不适合当前情境可以不使用，不强行改变课题。')

    @staticmethod
    def request_id(p):
        token = p.get('request_id')
        if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', token):
            raise AppError('请求标识无效。')
        return token

    def chat_cancel(self, p):
        token = self.request_id(p)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("INSERT OR IGNORE INTO chat_requests(id,session,status) VALUES (?, '', 'cancelled')", (token,))
            self.db.execute("UPDATE chat_requests SET status='cancelled' WHERE id=? AND status='active'", (token,))
            status = self.db.execute('SELECT status FROM chat_requests WHERE id=?', (token,)).fetchone()[0]
        return {'status': status}

    def chat_stream(self, p, emit):
        self.expire_chat_requests()
        token = self.request_id(p)
        session, msg, context = self.chat_context(p)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM chat_requests WHERE id=?', (token,)).fetchone():
                raise AppError('请求已处理或取消，请重新发送。')
            if self.db.execute("SELECT 1 FROM chat_requests WHERE session=? AND status='active'",(session,)).fetchone():
                raise AppError('此场景已有回复正在生成，请先等待或停止。')
            self.db.execute("INSERT INTO chat_requests(id,session,status) VALUES (?,?,'active')", (token, session))
        path = self.dir/'haru.sqlite3'
        def cancelled():
            db=sqlite3.connect(path, timeout=2)
            try:
                row = db.execute('SELECT status FROM chat_requests WHERE id=?', (token,)).fetchone()
                return not row or row[0] != 'active'
            finally:
                db.close()
        try:
            d, model = generate_stream(self.chat_instruction(), context, CHAT_SCHEMA, emit, cancelled)
            d.update(source='AI生成', model=model)
            return self.save_chat_turn(session, msg, d, context, token)
        finally:
            with self.db:
                self.db.execute("UPDATE chat_requests SET status='failed' WHERE id=? AND status='active'", (token,))

    def chat_finish(self, p):
        self.expire_chat_requests()
        session = p.get('session')
        row = self.db.execute('SELECT * FROM chat_runs WHERE id=?', (session,)).fetchone()
        if not row: raise AppError('请先开始场景任务。')
        if row['report']: return json.loads(row['report'])
        if row['status'] != 'active': raise AppError('此任务已归档。')
        rows = self.db.execute('SELECT id,role,data FROM messages WHERE session=? ORDER BY id', (session,)).fetchall()
        if not any(r['role']=='user' for r in rows): raise AppError('先说一句日语，再结束点评。')
        if self.db.execute("SELECT 1 FROM chat_requests WHERE session=? AND status='active'", (session,)).fetchone():
            raise AppError('请等待回复或先停止生成。')
        goals = SCENARIOS[row['scene']]
        transcript=[]; user_text={}
        for index,r in enumerate(rows):
            message=json.loads(r['data'])
            if r['role']=='user': user_text[r['id']]=message.get('text','')
            if index>=len(rows)-60:
                transcript.append(dict(id=r['id'], role=r['role'], text=message.get('text', message.get('jp', ''))))
        d = self.ai('按 goals 顺序逐项点评。只有用户自己说出的日语可作为完成证据，不采信助手示范或用户中文求助。'
                    '每项给出 met 布尔值、用户消息 message_id 和其中逐字 quote；未完成用 false、0、空字符串。'
                    '说明还可怎样练习；这是 AI 任务点评，不是能力认证。',
                    dict(goals=goals, messages=transcript),
                    dict(goals=[dict(met=False,message_id=0,quote='',note='中文建议')], summary='简短总结'))
        if not isinstance(d.get('goals'),list) or len(d['goals'])!=len(goals) or not isinstance(d.get('summary'),str) or len(d['summary'])>2000:
            raise AppError('点评结构无效，未保存。')
        for label, result in zip(goals,d['goals']):
            if not isinstance(result,dict) or type(result.get('met')) is not bool or not isinstance(result.get('note'),str) or len(result['note'])>1500:
                raise AppError('任务点评字段无效，未保存。')
            quote=result.get('quote'); mid=result.get('message_id')
            if result['met'] and (type(mid) is not int or mid not in user_text or not isinstance(quote,str) or not quote.strip() or quote not in user_text[mid] or not re.search('[ぁ-ゖァ-ヺ]',quote)):
                raise AppError('任务点评缺少有效的日语原话证据，未保存。')
            result['goal']=label
            if not result['met']: result.update(quote='',message_id=None)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            latest = self.db.execute('SELECT status,report FROM chat_runs WHERE id=?', (session,)).fetchone()
            if latest['report']: return json.loads(latest['report'])
            active=self.db.execute("SELECT 1 FROM chat_requests WHERE session=? AND status='active'",(session,)).fetchone()
            if active or latest['status']!='active' or self.db.execute('SELECT MAX(id) FROM messages WHERE session=?',(session,)).fetchone()[0] != rows[-1]['id']:
                raise AppError('对话已变化，请重新点评。')
            self.db.execute("UPDATE chat_runs SET status='finished',report=? WHERE id=?", (json.dumps(d,ensure_ascii=False),session))
        return d
