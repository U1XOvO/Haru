"""Versioned grammar library and independent JLPT practice sessions.

Official workbooks use a source-linked answer sheet; original texts stay at the
publisher. Answers, snapshots and deadlines live in SQLite, never process memory.
"""
import copy
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from llm import AppError, ROOT, public_config
from study_generation import StudyGeneration
from jlpt_blueprints import get_blueprint

LEVELS = ('N5', 'N4', 'N3', 'N2', 'N1')
SKILLS = {'vocabulary': '文字词汇', 'grammar': '语法', 'reading': '阅读', 'listening': '听力'}
DATA = ROOT / 'data' / 'study'


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def string(value, name, limit=8000, optional=False):
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise AppError(f'{name}格式无效。')
    return value.strip()


def number(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise AppError(f'{name}应为{low}到{high}之间的整数。')
    return value


def level(value):
    if value not in LEVELS:
        raise AppError('请选择 N5 至 N1 的等级。')
    return value


def load_data(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


def validate_paper(raw, grammar_ids=()):
    """Whitelist fields so imported/AI metadata cannot leak an answer or inject UI."""
    if not isinstance(raw, dict):
        raise AppError('试卷应为 JSON 对象。')
    out = dict(title=string(raw.get('title'), '试卷名称', 150), level=level(raw.get('level')),
               version=string(str(raw.get('version', '1')), '内容版本', 60))
    sections = raw.get('sections')
    if not isinstance(sections, list) or not 1 <= len(sections) <= 8:
        raise AppError('试卷应包含1到8个分区。')
    out['sections'] = []
    for section in sections:
        if not isinstance(section, dict): raise AppError('分区格式无效。')
        out['sections'].append(dict(id=string(section.get('id'), '分区ID', 50),
            title=string(section.get('title'), '分区名称', 100),
            seconds=number(section.get('seconds'), 30, 14400, '分区时长（秒）')))
    sids = [s['id'] for s in out['sections']]
    if len(set(sids)) != len(sids): raise AppError('分区ID重复。')
    questions = raw.get('questions')
    if not isinstance(questions, list) or not 1 <= len(questions) <= 250:
        raise AppError('试卷应包含1到250题。')
    out['questions'] = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict): raise AppError('题目格式无效。')
        skill = q.get('skill')
        if skill not in SKILLS: raise AppError('题目技能类型无效。')
        opts = q.get('options')
        if not isinstance(opts, list) or not 2 <= len(opts) <= 5: raise AppError('每题应有2到5个选项。')
        opts = [string(o, '选项', 2000) for o in opts]
        if len(set(opts)) != len(opts): raise AppError('题目选项重复。')
        if q.get('section') not in sids: raise AppError('题目引用了不存在的分区。')
        gids = q.get('grammar_ids', [])
        if not isinstance(gids, list) or any(g not in grammar_ids for g in gids):
            raise AppError('题目引用了不存在的语法点。')
        item = dict(id=string(q.get('id', f'q{i+1}'), '题目ID', 100),
            prompt=string(q.get('prompt'), '题干'), options=opts,
            answer=number(q.get('answer'), 0, len(opts)-1, '答案序号'),
            explanation=string(q.get('explanation'), '解析'), skill=skill, section=q['section'],
            grammar_ids=gids)
        for key in ('passage', 'audio_text', 'locator', 'group'):
            item[key] = string(q.get(key, ''), key, 16000, optional=True)
        for key in ('audio_asset', 'image_asset'):
            value = q.get(key, '')
            if value and (not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}\.(?:mp3|m4a|wav|png|jpg)', value)):
                raise AppError('媒体标识无效，请先导入文件。')
            item[key] = value
        out['questions'].append(item)
    ids = [q['id'] for q in out['questions']]
    if len(set(ids)) != len(ids): raise AppError('题目ID重复。')
    if set(sids) != {q['section'] for q in out['questions']}: raise AppError('分区不能没有题目。')
    return out


class Study(StudyGeneration):
    def init_study(self, backup_needed=True):
        # Separate version marker: existing course schema remains version 3.
        if backup_needed and self.get('study_schema') is None and self.get('profile') is not None:
            folder = self.dir / 'backups'; folder.mkdir(exist_ok=True)
            import sqlite3
            path = folder / ('before-study-' + uuid.uuid4().hex[:8] + '.sqlite3')
            target = sqlite3.connect(path.with_suffix('.tmp'))
            try: self.db.backup(target)
            finally: target.close()
            path.with_suffix('.tmp').replace(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS grammar_progress(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS study_papers(id TEXT PRIMARY KEY,data TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS study_attempts(id TEXT PRIMARY KEY,paper_id TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS study_attempt_paper ON study_attempts(paper_id,updated);
        ''')
        self.init_generation()
        if self.get('study_schema') != 1:
            with self.db: self.set('study_schema', 1)

    def grammar_items(self):
        items=getattr(self,'_grammar_items_cache',None)
        if items is None:
            items=load_data('grammar.json')['items']
            self._grammar_items_cache=items
        return items

    def grammar_catalog(self, p):
        lv = level(p.get('level', 'N5'))
        progress = {r['id']: json.loads(r['data']) for r in self.db.execute('SELECT * FROM grammar_progress')}
        items = []
        for g in self.grammar_items():
            if g['level'] == lv:
                d = {k: g[k] for k in ('id', 'level', 'unit', 'title', 'meaning', 'connection')}
                d['progress'] = progress.get(g['id'], {})
                items.append(d)
        return dict(level=lv, items=items, levels=list(LEVELS), last=self.get('study_last_grammar'),
                    counts={n: sum(g['level'] == n for g in self.grammar_items()) for n in LEVELS})

    def grammar_detail(self, p):
        g = next((g for g in self.grammar_items() if g['id'] == p.get('id')), None)
        if not g: raise AppError('找不到这个语法点。')
        out = copy.deepcopy(g); out.pop('questions', None)
        row = self.db.execute('SELECT data FROM grammar_progress WHERE id=?', (g['id'],)).fetchone()
        out['progress'] = json.loads(row[0]) if row else {}
        with self.db: self.set('study_last_grammar', dict(id=g['id'], level=g['level']))
        return out

    def grammar_mark(self, p):
        g = self.grammar_detail(p)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT data FROM grammar_progress WHERE id=?', (g['id'],)).fetchone()
            d = json.loads(row[0]) if row else {}
            if 'favorite' in p:
                if type(p['favorite']) is not bool: raise AppError('收藏状态无效。')
                d['favorite'] = p['favorite']
            else: d.setdefault('read_at', datetime.now().isoformat())
            self.db.execute('INSERT OR REPLACE INTO grammar_progress VALUES (?,?)', (g['id'], encode(d)))
        return d

    def grammar_practice(self, p):
        g = next((g for g in self.grammar_items() if g['id'] == p.get('id')), None)
        if not g: raise AppError('找不到这个语法点。')
        paper = dict(id='grammar-' + g['id'], title=g['title']+' · 随堂练习', level=g['level'],
            version=g['version'], source_type='grammar', grammar_id=g['id'], source='Haru 原创语法练习',
            sections=[dict(id='grammar', title='随堂练习', seconds=600)], questions=g['questions'])
        return self._start(paper, 'practice')

    def study_catalog(self, p):
        lv = level(p.get('level', 'N5'))
        papers = load_data('papers.json')
        papers += [json.loads(r[0]) for r in self.db.execute('SELECT data FROM study_papers ORDER BY created DESC')]
        rows = []
        for d in papers:
            if d['level'] == lv:
                row = {k: d.get(k) for k in ('id', 'title', 'level', 'version', 'source_type', 'source', 'source_url', 'model', 'configured_model', 'resources', 'notes')}
                row.update(count=len(d['questions']), sections=d['sections'])
                rows.append(row)
        return dict(level=lv, papers=rows, history=self.study_history({'level': lv}),
                    summary=self.study_summary({}), config=public_config(),
                    blueprint=get_blueprint(lv), generation=self.generation_pending())

    def _paper(self, identity):
        for d in load_data('papers.json'):
            if d['id'] == identity: return d
        row = self.db.execute('SELECT data FROM study_papers WHERE id=?', (identity,)).fetchone()
        if not row: raise AppError('找不到这份试卷。')
        return json.loads(row[0])

    def study_import(self, p):
        raw = p.get('paper')
        ids = {g['id'] for g in self.grammar_items()}
        d = validate_paper(raw, ids)
        d.update(id='import-'+uuid.uuid4().hex, source_type='import',
            source=string(raw.get('source'), '来源说明', 500), source_url='',
            notes='用户导入；答案与来源由导入者核对。', resources=[])
        for q in d['questions']:
            for key in ('audio_asset','image_asset'):
                if q[key]: self.study_asset_path(q[key])
        pdf = raw.get('pdf_asset', '')
        if pdf:
            if self.study_asset_path(pdf).suffix != '.pdf': raise AppError('原卷应为PDF。')
            d['pdf_asset'] = pdf
        with self.db: self.db.execute('INSERT INTO study_papers VALUES (?,?,?)', (d['id'], encode(d), time.time()))
        return dict(id=d['id'], count=len(d['questions']))

    def study_generate(self, p):
        # Keep the former skill/count RPC available through a bounded adapter;
        # the UI and new clients use study_generation_start/step directly.
        if 'mode' in p or 'count' not in p:
            job = self.study_generation_start(p)
            return self._run_generation_compat(job)
        return self._study_generate_legacy(p)

    def _study_generate_legacy(self, p):
        lv = level(p.get('level'))
        count = number(p.get('count', 5), 5, 10, '生成题数')
        skill = p.get('skill', 'mixed')
        if skill not in (*SKILLS, 'mixed'):
            raise AppError('请选择有效题型。')
        job = self.study_generation_start_compat(lv, count, skill)
        return self._run_generation_compat(job)

    def _run_generation_compat(self, job):
        job_id = job['id']
        deadline = time.monotonic() + 180
        while job['status'] == 'active':
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppError('同步生成已达到单次调用时限，已保存进度；请在 JLPT 页面继续此任务。')
            delay = max(0, job.get('retry_after', 0))
            while delay > 0 and time.monotonic() < deadline:
                time.sleep(min(1, delay, max(0, deadline-time.monotonic())))
                job = self.study_generation_status({'id': job_id})
                if job['status'] != 'active':
                    break
                delay = max(0, job.get('retry_after', 0))
            if job['status'] != 'active':
                break
            job = self.study_generation_step({'id': job_id})
        if job['status'] == 'complete':
            return dict(id=job['paper_id'], count=job['count'], model=job['model'])
        if job['status'] == 'failed':
            raise AppError(job.get('error') or '生成任务已暂停，可在 JLPT 页面查看并继续。')
        if job['status'] == 'cancelled':
            raise AppError('生成任务已取消。')
        raise AppError('生成任务暂未完成，已保存进度；请在 JLPT 页面继续此任务。')

    def study_start(self, p):
        paper = copy.deepcopy(self._paper(p.get('id')))
        skill = p.get('skill', 'all')
        if skill != 'all':
            if skill not in SKILLS: raise AppError('专项类型无效。')
            paper['questions'] = [q for q in paper['questions'] if q['skill'] == skill]
            if not paper['questions']: raise AppError('此试卷没有该类型题目。')
            paper['sections'] = [dict(id='practice', title=SKILLS[skill]+'专项', seconds=len(paper['questions'])*120)]
            for q in paper['questions']: q['section'] = 'practice'
            paper['title'] += ' · '+SKILLS[skill]+'专项'
        return self._start(paper, p.get('mode', 'practice'))

    def _start(self, paper, mode):
        if mode not in ('practice', 'timed'): raise AppError('作答模式无效。')
        now = time.time()
        attempt = dict(id=uuid.uuid4().hex, paper=copy.deepcopy(paper), mode=mode, started=now,
            answers={}, flags=[], section_index=0, deadline=now+paper['sections'][0]['seconds'] if mode=='timed' else None,
            revision=0, status='active', elapsed=0, last_active=now)
        with self.db: self.db.execute('INSERT INTO study_attempts VALUES (?,?,?,?)', (attempt['id'], paper['id'], encode(attempt), now))
        return self._public_attempt(attempt)

    def _attempt(self, identity):
        row = self.db.execute('SELECT data FROM study_attempts WHERE id=?', (identity,)).fetchone()
        if not row: raise AppError('找不到这次作答。')
        return json.loads(row[0])

    def _persist_attempt(self, a):
        self.db.execute('UPDATE study_attempts SET data=?,updated=? WHERE id=?', (encode(a), time.time(), a['id']))

    def _tick(self, a):
        if a['status'] != 'active' or a['mode'] != 'timed': return
        now = time.time()
        while a['status'] == 'active' and now >= a['deadline']:
            if a['section_index']+1 == len(a['paper']['sections']):
                self._finish(a); break
            a['section_index'] += 1
            a['deadline'] += a['paper']['sections'][a['section_index']]['seconds']
            a['revision'] += 1

    def _public_attempt(self, a):
        out = copy.deepcopy(a)
        if a['status'] != 'submitted':
            for q in out['paper']['questions']:
                q.pop('answer', None); q.pop('explanation', None)
                q.pop('answer_source', None)
                if a['mode'] == 'timed': q['grammar_ids'] = []
            # Official answer/script URLs are revealed only after submission.
            out['paper']['resources'] = [r for r in out['paper'].get('resources', []) if r.get('kind') not in ('answer','script')]
        out['server_now'] = time.time()
        return out

    def study_attempt(self, p):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            a = self._attempt(p.get('id')); revision = a['revision']; self._tick(a)
            if a['revision'] != revision: self._persist_attempt(a)
        return self._public_attempt(a)

    def study_save(self, p):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            a = self._attempt(p.get('id')); self._tick(a)
            if a['status'] == 'submitted':
                self._persist_attempt(a)
                return self._public_attempt(a)
            if p.get('revision') != a['revision']: raise AppError('作答已在其他页面更新，请重新打开以恢复最新答案。')
            patch = p.get('answers', {})
            if not isinstance(patch, dict): raise AppError('答案格式无效。')
            byid = {q['id']: q for q in a['paper']['questions']}
            current = a['paper']['sections'][a['section_index']]['id']
            for identity, value in patch.items():
                if identity not in byid: raise AppError('题号无效。')
                q = byid[identity]
                if a['mode'] == 'timed' and q['section'] != current: raise AppError('计时模式只能修改当前分区的答案。')
                if value is not None: number(value, 0, len(q['options'])-1, '所选答案')
                a['answers'][identity] = value
            flags = p.get('flags', a['flags'])
            if not isinstance(flags, list) or any(f not in byid for f in flags): raise AppError('标记题号无效。')
            a['flags'] = sorted(set(flags)); a['revision'] += 1
            # UI sends heartbeats only while visible; cap a missing heartbeat gap.
            now = time.time(); a['elapsed'] += min(30, max(0, now-a['last_active'])); a['last_active'] = now
            if p.get('finish') is True: self._finish(a)
            elif p.get('next_section') is True:
                if a['mode'] != 'timed': raise AppError('仅计时模式需要提交分区。')
                if a['section_index']+1 == len(a['paper']['sections']): self._finish(a)
                else:
                    a['section_index'] += 1
                    a['deadline'] = now+a['paper']['sections'][a['section_index']]['seconds']
            self._persist_attempt(a)
        return self._public_attempt(a)

    def _finish(self, a):
        if a['status'] == 'submitted': return
        results = []
        for q in a['paper']['questions']:
            selected = a['answers'].get(q['id'])
            results.append(dict(id=q['id'], selected=selected, answer=q['answer'], correct=selected==q['answer'], skill=q['skill']))
        correct = sum(r['correct'] for r in results)
        a.update(status='submitted', finished=time.time(), revision=a['revision']+1,
            result=dict(correct=correct, total=len(results), percent=round(correct*100/len(results)),
                unanswered=sum(r['selected'] is None for r in results), results=results,
                skills={s: dict(correct=sum(r['correct'] for r in results if r['skill']==s), total=sum(r['skill']==s for r in results)) for s in SKILLS}))
        gid = a['paper'].get('grammar_id')
        if gid:
            row = self.db.execute('SELECT data FROM grammar_progress WHERE id=?', (gid,)).fetchone()
            d = json.loads(row[0]) if row else {}
            d.update(score=a['result']['percent'], attempts=d.get('attempts',0)+1, last_attempt=a['id'])
            success = correct*100 >= 80*len(results)
            d['streak'] = d.get('streak',0)+1 if success else 0
            d['mastered'] = success
            days = (1,3,7,14,30)[min(max(d['streak']-1,0),4)] if success else 0
            d['due'] = (datetime.now()+timedelta(days=days, minutes=0 if success else 10)).isoformat()
            self.db.execute('INSERT OR REPLACE INTO grammar_progress VALUES (?,?)', (gid, encode(d)))

    def study_history(self, p):
        lv = p.get('level'); result = []
        # Lazy expiration also applies to history, so a closed timed exam isn't "active" forever.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for row in self.db.execute('SELECT data FROM study_attempts ORDER BY updated DESC, rowid DESC').fetchall():
                a = json.loads(row[0]); revision = a['revision']; self._tick(a)
                if a['revision'] != revision: self._persist_attempt(a)
                paper = a['paper']
                if lv and paper['level'] != lv: continue
                result.append(dict(id=a['id'], title=paper['title'], level=paper['level'], status=a['status'],
                    started=a['started'], source_type=paper['source_type'], mode=a['mode'],
                    correct=a.get('result',{}).get('correct'), total=len(paper['questions']),
                    answered=sum(v is not None for v in a['answers'].values())))
        return result

    def study_mistakes(self, p):
        lv = level(p.get('level', 'N5')); latest = {}
        for row in self.db.execute('SELECT data FROM study_attempts ORDER BY updated'):
            a = json.loads(row[0])
            if a['status'] != 'submitted' or a['paper']['level'] != lv: continue
            for q, result in zip(a['paper']['questions'], a['result']['results']):
                latest[(a['paper']['id'], a['paper']['version'], q['id'])] = (a, q, result)
        return [dict(attempt=a['id'], paper=a['paper']['id'], title=a['paper']['title'], question=q, selected=r['selected'])
                for a,q,r in latest.values() if not r['correct']]

    def study_retry(self, p):
        a = self._attempt(p.get('id'))
        if a['status'] != 'submitted': raise AppError('请先完成本次练习。')
        paper = copy.deepcopy(a['paper'])
        if p.get('wrong_only'):
            wrong = {r['id'] for r in a['result']['results'] if not r['correct']}
            paper['questions'] = [q for q in paper['questions'] if q['id'] in wrong]
            if not wrong: raise AppError('本次没有错题。')
            paper['title'] += ' · 错题复习'
            paper['sections'] = [dict(id='practice', title='错题复习', seconds=len(wrong)*120)]
            paper.pop('grammar_id', None)  # A partial retry cannot mark the whole grammar point mastered.
            for q in paper['questions']: q['section'] = 'practice'
        return self._start(paper, 'practice')

    def study_summary(self, p):
        progress = [json.loads(r[0]) for r in self.db.execute('SELECT data FROM grammar_progress')]
        now = datetime.now().isoformat()
        attempts = [json.loads(r[0]) for r in self.db.execute('SELECT data FROM study_attempts')]
        return dict(read=sum(bool(g.get('read_at')) for g in progress), mastered=sum(bool(g.get('mastered')) for g in progress),
            due=sum(bool(g.get('due')) and g['due'] <= now for g in progress),
            submitted=sum(a['status']=='submitted' for a in attempts),
            active=sum(a['status']=='active' for a in attempts))

    def study_asset_path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}\.(?:pdf|mp3|m4a|wav|png|jpg)', identity):
            raise AppError('媒体标识无效。')
        folder = (self.dir/'study-assets').resolve(); path = (folder/identity).resolve()
        if path.parent != folder or not path.is_file(): raise AppError('媒体文件不存在，请重新导入。')
        return path

    def study_image(self, p):
        import base64
        path = self.study_asset_path(p.get('id'))
        if path.suffix not in ('.png','.jpg') or path.stat().st_size > 2_000_000: raise AppError('题图应为不超过2MB的PNG/JPG。')
        return dict(url='data:image/'+('png' if path.suffix=='.png' else 'jpeg')+';base64,'+base64.b64encode(path.read_bytes()).decode())

    def study_export(self):
        return dict(schema=1, grammar_progress=[dict(r) for r in self.db.execute('SELECT * FROM grammar_progress')],
            papers=[json.loads(r[0]) for r in self.db.execute('SELECT data FROM study_papers')],
            attempts=[json.loads(r[0]) for r in self.db.execute('SELECT data FROM study_attempts')],
            media_note='媒体保存在本机 study-assets 目录；JSON包含引用，不包含媒体二进制。')
