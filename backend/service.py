"""Local teaching agent, persisted content, evidence-based progress and SM-2 cards."""
import copy
import json
import math
import os
import re
import random
import sqlite3
import portalocker
import uuid
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from curriculum import TOPICS, KANA, SEEDS, CARDS
from llm import AppError, generate, public_config
from llm_config import editable_config, save_config
from app_paths import storage_root
from learning import Learning, pronunciation
from progression import stage_info, course_spec, stage_courses
from study import Study
from lesson_design import LESSON, RULES as LESSON_RULES, VERSION as LESSON_VERSION, validate_design

EXAMPLE = dict(jp='日语句子',kana='完整假名读音',romaji='罗马音',zh='中文意思')
QUESTION = dict(prompt='题目',options=['选项一','选项二','选项三'],answer=0,explanation='中文解析',skill='grammar',audio='听力题必须有日语音频文本，否则空字符串')
CARD = dict(word='日语单词',reading='假名',romaji='罗马音',meaning='中文词义',example='日语例句',translation='中文例句翻译',mnemonic='初学者记忆技巧，非伪造词源')

def dumps(obj): return json.dumps(obj, ensure_ascii=False)

def card_key(word):
    return unicodedata.normalize('NFKC', word).strip().casefold()

def text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value)>limit: raise AppError(f'{name}不能为空且不能超过{limit}字。')
    return value.strip()

def integer(value, low, high, name):
    if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high: raise AppError(f'{name}应在{low}到{high}之间。')
    return value

def fields(obj, keys):
    if not isinstance(obj,dict): raise AppError('AI 返回结构不完整，请重试。')
    for key in keys: text(obj.get(key), key, 6000)
    return obj

def validate_examples(items):
    if not isinstance(items,list) or not 1<=len(items)<=12: raise AppError('例句数量不正确。')
    for x in items: fields(x, EXAMPLE)

def validate_questions(items):
    if not isinstance(items,list) or not 1<=len(items)<=12: raise AppError('测验应包含1到12题。')
    for q in items:
        fields(q,['prompt','explanation','skill'])
        opts=q.get('options')
        if not isinstance(opts,list) or not 2<=len(opts)<=5: raise AppError('测验选项不完整。')
        for o in opts: text(o,'选项',1000)
        if len(set(opts))!=len(opts): raise AppError('AI 返回了重复选项，请重试。')
        integer(q.get('answer'),0,len(opts)-1,'正确答案')
        if q['skill'] == 'vocabulary': q['skill']='grammar'
        if q['skill'] not in ('grammar','kana','listening'): raise AppError('测验技能类型无效。')
        if q['skill']=='listening': text(q.get('audio'),'听力原文',2000)
        else: q['audio']=''

def validate_lesson(obj, *, rich=False):
    fields(obj,['title','goal','grammar','tip','speaking'])
    validate_examples(obj.get('examples')); fields(obj.get('listening'),EXAMPLE)
    validate_questions(obj.get('questions'))
    if rich: validate_design(obj)
    return obj

def visible_test(obj):
    o=copy.deepcopy(obj)
    for q in o.get('questions',[]):
        q.pop('answer',None); q.pop('explanation',None)
    return o

class Service(Learning, Study):
    def __init__(self, data_dir=None):
        self.dir=Path(data_dir or os.environ.get('HARU_DATA_DIR') or storage_root()/'runtime')
        self.dir.mkdir(parents=True,exist_ok=True)
        from maintenance import lock, recover
        self._storage_lock = lock(self.dir if data_dir else storage_root(), shared=True)
        self._storage_lock.acquire()
        lock_root = self.dir if data_dir else storage_root()
        if (lock_root / '.migration.json').exists():
            self._storage_lock.release()
            with lock(lock_root): recover(lock_root)
            self._storage_lock.acquire()
        try:
            with portalocker.Lock(self.dir / '.schema.lock', mode='a', timeout=15):
                self._initialize_database()
        except Exception:
            if hasattr(self, 'db'): self.db.close()
            self._storage_lock.release()
            raise

    def _initialize_database(self):
        self.db=sqlite3.connect(self.dir/'haru.sqlite3',timeout=15)
        self.db.row_factory=sqlite3.Row
        # Back up an existing learning database before additive schema migration.
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        from maintenance import SCHEMA_VERSION
        if version > SCHEMA_VERSION:
            raise AppError('学习数据库来自更新版本，请升级 Haru；当前程序不会降级或修改它。')
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='kv'").fetchone():
            study_schema = self.get('study_schema')
            if study_schema is not None and study_schema > 2:
                raise AppError('JLPT 数据来自更新版本，请升级 Haru 后再打开。')
            if version == 3 and study_schema == 2 and self.get('performance_schema') == 1:
                return
        if version<3 and self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kv'").fetchone():
            backup_dir=self.dir/'backups';backup_dir.mkdir(exist_ok=True)
            backup=backup_dir/('before-progression-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.sqlite3')
            temp=backup.with_suffix('.tmp')
            target=sqlite3.connect(temp)
            try: self.db.backup(target)
            finally: target.close()
            temp.replace(backup)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS content(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,kind TEXT NOT NULL,ref TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY,word TEXT UNIQUE NOT NULL,data TEXT NOT NULL,ease REAL NOT NULL DEFAULT 2.5,repetitions INTEGER NOT NULL DEFAULT 0,interval INTEGER NOT NULL DEFAULT 0,due TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS unique_completion ON events(kind,ref) WHERE kind IN ('lesson','quiz');
        CREATE TABLE IF NOT EXISTS stage_passes(stage INTEGER PRIMARY KEY,assessment_id TEXT NOT NULL,passed_at TEXT NOT NULL,policy_version INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS unique_stage_completion ON events(kind,ref) WHERE kind IN ('stage_assessment','remedial');
        -- Retain removed conversation records for non-destructive learning-data export.
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,session TEXT NOT NULL,role TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chat_runs(id TEXT PRIMARY KEY,scene TEXT NOT NULL,status TEXT NOT NULL,report TEXT,created TEXT NOT NULL);
        ''')
        self.init_learning()
        self.init_study(backup_needed=version>=3)
        from performance import initialize
        initialize(self.db)
        if version < 3:
            self.db.execute('PRAGMA user_version=3')
        with self.db:
            if self.get('profile') is None:
                self.set('profile', {'name':'学习者','minutes':20,'time':'20:30','goal':'日常交流',
                         'start':date.today().isoformat(),'romaji':True,'speech_rate':1.0})

    def close(self):
        try: self.db.close()
        finally: self._storage_lock.release()
    def get(self,key,default=None):
        r=self.db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def set(self,key,value): self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,dumps(value)))
    def save(self,kind,data):
        data=dict(data,id=uuid.uuid4().hex,kind=kind,created=datetime.now().isoformat(timespec='seconds'))
        with self.db: self.db.execute('INSERT INTO content VALUES (?,?,?,?)',(data['id'],kind,dumps(data),data['created']))
        return data
    def content(self,id,kind=None):
        r=self.db.execute('SELECT data FROM content WHERE id=?',(id,)).fetchone()
        if not r: raise AppError('找不到这份学习内容，请重新打开。')
        d=json.loads(r[0])
        if kind and d['kind']!=kind: raise AppError('学习内容类型不匹配。')
        return d
    def event(self,kind,ref,data):
        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(uuid.uuid4().hex,kind,ref,dumps(data),datetime.now().isoformat(timespec='seconds')))
    def history(self,kind):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM content WHERE kind=? ORDER BY created DESC,rowid DESC LIMIT 40',(kind,))]
    def completed_lessons(self, details=False):
        """Legacy `day` is retained; course variants count once by lesson number."""
        done={}
        columns = 'c.data' if details else "json_object('id',c.id,'lesson_no',COALESCE(json_extract(c.data,'$.lesson_no'),json_extract(c.data,'$.day')))"
        for row in self.db.execute(f"SELECT {columns} FROM events e JOIN content c ON c.id=e.ref WHERE e.kind='lesson' ORDER BY e.created,e.rowid"):
            d=json.loads(row[0]); number=d.get('lesson_no',d.get('day'))
            if isinstance(number,int) and not isinstance(number,bool) and number>0: done[number]=d
        return done

    def outcome(self,ref):
        r=self.db.execute('SELECT data FROM events WHERE ref=? ORDER BY created DESC,rowid DESC LIMIT 1',(ref,)).fetchone()
        return json.loads(r[0]) if r else None

    def progression(self, done=None):
        # Bootstrap supplies the already computed course completion map so
        # stats and curriculum can use the same snapshot without re-reading it.
        if done is None:
            done = self.completed_lessons()
        passed={r[0] for r in self.db.execute('SELECT stage FROM stage_passes')}
        stage=1
        while stage in passed: stage+=1
        info=stage_info(stage)
        pending=[n for n in range(info['start'],info['end']+1) if n not in done]
        assessments=[d for d in self.history('stage_assessment') if d['stage']==stage]
        latest=assessments[0] if assessments else None
        result=self.outcome(latest['id']) if latest else None
        status='learning' if pending else 'assessment_due'
        review=None; review_result=None
        if not pending and result and not result.get('passed',False):
            review=next((d for d in self.history('remedial') if d.get('assessment_id')==latest['id']),None)
            review_result=self.outcome(review['id']) if review else None
            status='reassessment_ready' if review_result and review_result.get('passed') else 'review_required'
        next_no=pending[0] if pending else None
        return dict(info,completed=info['total']-len(pending),completed_total=len(done),pending=pending,
                    status=status,next_lesson=next_no,next_focus=course_spec(next_no)['focus'] if next_no else '阶段评估与针对性复习',
                    assessment_id=latest['id'] if latest else None,last_result=result,
                    remedial_id=review['id'] if review else None,remedial_result=review_result,
                    pass_rule='所有课程完成后，阶段评估总正确率≥80%，且每个受测类别≥60%；未通过先完成补强练习。仅用于课程进阶。')

    def curriculum(self,p,progress=None,done=None):
        progress=progress if progress is not None else self.progression(done)
        stage=integer(p.get('stage',progress['stage']),1,progress['stage'],'阶段')
        done=done if done is not None else self.completed_lessons()
        generated=set()
        for row in self.db.execute("SELECT COALESCE(json_extract(data,'$.lesson_no'),json_extract(data,'$.day')) FROM content WHERE kind='lesson' AND json_extract(data,'$.source')='AI生成'"):
            generated.add(row[0])
        return dict(stage_info(stage),courses=[dict(c,completed=c['lesson_no'] in done,generated=c['lesson_no'] in generated) for c in stage_courses(stage)])

    def stage_assessment(self,p):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._stage_assessment(p)

    def _stage_assessment(self,p):
        progress=self.progression()
        if progress['pending']: raise AppError('先完成当前阶段的所有课程，再进行阶段评估。')
        if progress['status']=='review_required': raise AppError('请先完成补强练习，再开始新的阶段评估。')
        if progress['assessment_id'] and not progress['last_result']:
            return visible_test(self.content(progress['assessment_id'],'stage_assessment'))
        stage=progress['stage']
        lessons=[d for n,d in sorted(self.completed_lessons(details=True).items()) if progress['start']<=n<=progress['end']]
        attempt=1+self.db.execute("SELECT COUNT(*) FROM content WHERE kind='stage_assessment' AND json_extract(data,'$.stage')=?",(stage,)).fetchone()[0]
        rng=random.Random(f'haru-stage-{stage}-attempt-{attempt}')
        pool=[];seen=set()
        for lesson in lessons:
            for original in lesson['questions']:
                key=dumps([original['prompt'],original.get('audio',''),original['options']])
                if key in seen: continue
                seen.add(key)
                pool.append(dict(copy.deepcopy(original),source_id=lesson['id'],lesson_no=lesson.get('lesson_no',lesson['day'])))
        if len(pool)<6: raise AppError('阶段题库不足6道不同题目，请完成更多本阶段AI课程变体后再评估。')
        # Stratify by available skill, then spread the remaining questions over courses.
        chosen=[]
        for skill in ('grammar','kana','listening'):
            candidates=[q for q in pool if q['skill']==skill]
            rng.shuffle(candidates); chosen.extend(candidates[:2])
        courses=lessons[:];rng.shuffle(courses)
        for lesson in courses:
            if len(chosen)>=12: break
            candidates=[q for q in pool if q['source_id']==lesson['id'] and q not in chosen]
            if candidates: chosen.append(rng.choice(candidates))
        remaining=[q for q in pool if q not in chosen];rng.shuffle(remaining)
        chosen=(chosen+remaining)[:12];rng.shuffle(chosen)
        validate_questions(chosen)
        sources={q['source_id'] for q in chosen}
        return visible_test(self.save('stage_assessment',dict(stage=stage,attempt=attempt,title=f'第{stage}阶段 · {progress["name"]}评估',
            questions=chosen,scope=[l['title'] for l in lessons if l['id'] in sources],source='本阶段已学课程抽样',model=None,
            rule=progress['pass_rule'],coverage=len(sources))))

    def remedial(self,p):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._remedial(p)

    def _remedial(self,p):
        progress=self.progression()
        if progress['status'] not in ('review_required','reassessment_ready'): raise AppError('当前没有待补强的阶段评估。')
        if progress['remedial_id']: return visible_test(self.content(progress['remedial_id'],'remedial'))
        assessment=self.content(progress['assessment_id'],'stage_assessment');result=progress['last_result']
        questions=[copy.deepcopy(q) for q,r in zip(assessment['questions'],result['results']) if not r['correct']]
        ids=list(dict.fromkeys(q['source_id'] for q in questions));lessons=[self.content(id,'lesson') for id in ids]
        examples=[]
        for l in lessons:
            for x in l['examples']:
                if x not in examples: examples.append(x)
        data=dict(stage=progress['stage'],assessment_id=assessment['id'],title=f'第{progress["stage"]}阶段 · 错题补强',
            goal='回看评估中未掌握的知识点，完成针对性练习后重新评估。',
            grammar='\n\n'.join(f'第{l.get("lesson_no",l["day"])}课 · {l["title"]}\n{l["grammar"]}' for l in lessons),
            tip='\n'.join(dict.fromkeys(l['tip'] for l in lessons)),examples=examples[:12],
            speaking='跟读下面的例句，再换成自己的信息表达一次。',listening=lessons[0]['listening'],
            questions=questions,source='阶段错题与原课程讲解',model=None)
        validate_questions(questions)
        return visible_test(self.save('remedial',data))

    def evidence(self):
        start=(date.today()-timedelta(days=date.today().weekday())).isoformat()
        events=self.db.execute('SELECT * FROM events WHERE created>=? ORDER BY created',(start,)).fetchall()
        completed={r['ref'] for r in events if r['kind']=='lesson'}
        cards={r['ref'] for r in events if r['kind']=='review'}
        lessons=[self.content(x,'lesson') for x in completed]
        learned=[json.loads(r[0]) for x in cards for r in self.db.execute('SELECT data FROM cards WHERE id=?',(x,))]
        return dict(start=start,lessons=lessons,cards=learned)
    def context(self):
        p=self.get('profile'); ev=self.evidence(); progress=self.progression()
        return {'native_language':'简体中文','level':progress['level'],'stage':progress['stage'],'stage_name':progress['name'],'goal':p['goal'],'minutes':p['minutes'],'completed_this_week':[{'title':x['title'],'goal':x['goal']} for x in ev['lessons']], 'recent_mistakes':self.get('mistakes',[])[:8], 'review_targets':self.review_targets()}
    def ai(self,task,context,schema):
        data,model=generate(task,context,schema)
        data['source']='AI生成'; data['model']=model
        return data
    def stats(self,done=None,progress=None):
        days={r['day']:r['count'] for r in self.db.execute('SELECT day,count FROM activity_daily WHERE count>0')}
        cursor=date.today()
        if cursor.isoformat() not in days: cursor-=timedelta(days=1)
        streak=0
        while cursor.isoformat() in days: streak+=1; cursor-=timedelta(days=1)
        week=[date.today()-timedelta(days=6-i) for i in range(7)]
        totals={r['skill']:dict(correct=r['correct'],total=r['total']) for r in self.db.execute('SELECT * FROM activity_skill')}
        skills={s:totals.get(s,dict(correct=0,total=0)) for s in ('grammar','kana','listening')}
        attempts=self.db.execute("SELECT COALESCE(SUM(count),0) FROM activity_kind WHERE kind IN ('lesson','quiz','stage_assessment','remedial')").fetchone()[0]
        if done is None: done=self.completed_lessons()
        progress=progress if progress is not None else self.progression(done)
        return dict(streak=streak,next_day=progress['next_lesson'],next_lesson=progress['next_lesson'],lessons=len(done),cards=self.db.execute('SELECT COUNT(*) FROM cards').fetchone()[0],due=self.db.execute('SELECT COUNT(*) FROM cards WHERE due<=?',(datetime.now().isoformat(timespec='seconds'),)).fetchone()[0],activity=[{'date':d.isoformat(),'count':days.get(d.isoformat(),0)} for d in week],skills=skills,today=days.get(date.today().isoformat(),0),attempts=attempts)
    def route(self,action,p):
        if not isinstance(p,dict): raise AppError('请求格式无效。')
        study_actions = ('grammar_catalog','grammar_detail','grammar_mark','grammar_practice',
            'study_catalog','study_import','study_generate','study_start','study_attempt',
            'study_generation_start','study_generation_step','study_generation_status',
            'study_generation_cancel','study_delete',
            'study_save','study_history','study_mistakes','study_retry','study_summary','study_image')
        if action in study_actions: return getattr(self, action)(p)
        handlers={'config_get':lambda p:editable_config(),'config_save':save_config,'annotate':self.annotate,'dictionary':self.dictionary,'dictionary_add':self.dictionary_add,'encounter':self.encounter,'knowledge':self.knowledge,'daily_word':self.daily_word,'daily_word_add':self.daily_word_add,'bootstrap':self.bootstrap,'profile':self.profile,'lesson':self.lesson,'grade':self.grade,'cards':self.cards,'card_create':self.card_create,'card_random':self.card_random,'card_seed':self.card_seed,'review':self.review,'decode':self.decode,'quiz':self.quiz,'immersion':self.immersion,'export':self.export,'ping':self.ping,'history':self.list_history,'curriculum':self.curriculum,'stage_assessment':self.stage_assessment,'remedial':self.remedial}
        handlers.update(card_queue=self.card_queue,cards_page=self.cards_page,card_detail=self.card_detail,
                        reading_lookup=self.reading_lookup,encounters=self.encounters)
        if action not in handlers: raise AppError('不支持的操作。')
        return handlers[action](p)
    def bootstrap(self,p):
        done=self.completed_lessons()
        progress=self.progression(done)
        stats=self.stats(done=done,progress=progress)
        curriculum=self.curriculum({},progress=progress,done=done)
        return dict(profile=self.get('profile'),config=public_config(),stats=stats,study=self.study_summary({}),topics=TOPICS,kana=KANA,
                    progression=progress,curriculum=curriculum,stages=[stage_info(n) for n in range(1,progress['stage']+1)],
                    knowledge=self.knowledge({'limit':100}) if p.get('include_knowledge') else [],mistakes=self.get('mistakes',[]),recent=[dict(r) for r in self.db.execute("SELECT id,json_extract(data,'$.title') AS title,COALESCE(json_extract(data,'$.lesson_no'),json_extract(data,'$.day')) AS lesson_no,json_extract(data,'$.day') AS day,json_extract(data,'$.source') AS source FROM content WHERE kind='lesson' ORDER BY created DESC,rowid DESC LIMIT 5")])
    def profile(self,p):
        current=self.get('profile')
        current['name']=text(p.get('name'),'称呼',30)
        current['minutes']=integer(p.get('minutes'),5,90,'每日分钟数')
        current['goal']=text(p.get('goal'),'目标',150)
        current['time']=text(p.get('time',current['time']),'学习时间',5)
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',current['time']): raise AppError('时间格式应为HH:MM。')
        current['romaji']=bool(p.get('romaji',True))
        # 语速倍数，0.5–2.0；1.0 为默认。非数字或越界一律报错，不静默兜底。
        rate = p.get('speech_rate', current.get('speech_rate', 1.0))
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0.5 <= rate <= 2.0:
            raise AppError('语速倍数应在 0.5 到 2.0 之间。')
        current['speech_rate'] = round(float(rate), 2)
        with self.db: self.set('profile',current)
        return current
    def lesson(self,p):
        progress=self.progression()
        if p.get('id'):
            d=self.content(text(p['id'],'课程ID',100),'lesson')
            if d.get('lesson_no',d['day'])>progress['end']: raise AppError('请先通过前一阶段评估，再学习此课程。')
            return visible_test(d)
        number=integer(p.get('lesson_no',p.get('day',progress['next_lesson'])),1,progress['end'],'课次（先通过阶段评估以解锁后续课程）')
        source=p.get('source','builtin')
        if source not in ('builtin','ai'): raise AppError('课程来源无效。')
        cached_only=p.get('cached_only',False)
        if not isinstance(cached_only,bool): raise AppError('课程读取方式无效。')
        if cached_only and p.get('regenerate'): raise AppError('只读课程不能同时重建。')
        # Look up across all saved courses, not only the 40 most recent results.
        label='AI生成' if source=='ai' else '内置原创'
        for row in self.db.execute("SELECT data FROM content WHERE kind='lesson' ORDER BY created DESC,rowid DESC"):
            cached=json.loads(row[0])
            if cached.get('lesson_no',cached['day'])==number and cached['source']==label and not p.get('regenerate'):
                # Upgrade built-in content by adding a version, never rewriting a
                # saved lesson or its score. AI upgrades remain explicit paid actions.
                if source=='ai' or cached.get('design_version')==LESSON_VERSION:
                    return visible_test(cached)
        if cached_only: return None
        spec=course_spec(number)
        if source=='builtin':
            if number>len(SEEDS): raise AppError('内置课覆盖前7课；第8课起请使用AI创建课程。')
            data=copy.deepcopy(SEEDS[number-1]); data.update(source='内置原创',model=None)
        else:
            target=stage_info(spec['stage'])
            context=dict(self.context(),lesson_no=number,topic=spec,level=target['level'],stage=target['stage'],stage_name=target['name'],
                         teaching_outline={k:v for k,v in SEEDS[number-1].items() if k in ('goal','grammar','tip','sections','vocabulary')} if number<=len(SEEDS) else None,
                         lesson_design=dict(version=LESSON_VERSION,objectives=3,sections='3–4，每节至少80字，共至少300字',
                                            vocabulary='6–10',examples='6–8',dialogue_turns='4–8',production=2,
                                            questions=8,listening_questions_min=2,application_or_transfer_min=4,
                                            pacing='保留完整内容；时间少时分两次完成，先理解再练习'),
                         scope_rule='以本课所属阶段和主题为准，复习旧课时不要提升难度。前7课不提前引入后续课句型。后续持续应用阶段复用已学基础，不自动声称达到考试等级。')
            for attempt in range(2):
                data=self.ai(LESSON_RULES,context,LESSON)
                try:
                    validate_lesson(data,rich=True)
                    break
                except AppError as error:
                    if attempt: raise AppError('AI课程两次未通过内容校验，未保存新版本。'+str(error)) from None
                    context['revision_requirement']=str(error)+' 请重新生成完整课程并自查全部规则。'
        validate_lesson(data,rich=True); data.update(day=number,lesson_no=number,stage=spec['stage'],design_version=LESSON_VERSION)
        if source=='ai': self.apply_review_targets(data,context['review_targets'],[x['jp'] for x in data['examples']+data['dialogue']['lines']]+[data['listening']['jp']])
        return visible_test(self.save('lesson',data))
    def grade(self,p):
        d=self.content(text(p.get('id'),'测验ID',100))
        if d['kind'] not in ('lesson','quiz','stage_assessment','remedial'): raise AppError('此内容不是测验。')
        answers=p.get('answers'); qs=d['questions']
        if not isinstance(answers,list) or len(answers)!=len(qs): raise AppError('请先完成所有题目。')
        results=[]
        for q,a in zip(qs,answers):
            integer(a,0,len(q['options'])-1,'答案')
            results.append(dict(prompt=q['prompt'],selected=a,answer=q['answer'],correct=a==q['answer'],explanation=q['explanation'],skill=q['skill']))
        correct=sum(r['correct'] for r in results)
        out=dict(score=round(100*correct/len(results)),results=results,title=d['title'],kind=d['kind'])
        gated=d['kind'] in ('stage_assessment','remedial')
        if gated:
            skills={skill:dict(correct=sum(r['correct'] for r in results if r['skill']==skill),total=sum(r['skill']==skill for r in results)) for skill in sorted({r['skill'] for r in results})}
            out.update(stage=d['stage'],skills=skills,passed=correct*100>=80*len(results) and all(v['correct']*100>=60*v['total'] for v in skills.values()))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing=self.outcome(d['id'])
            if existing and (d['kind']!='remedial' or existing.get('passed')):
                if gated: out=existing
                out['recorded']=False
            else:
                progress=self.progression()
                if gated and d['stage']!=progress['stage']: raise AppError('阶段已变化，请打开当前阶段的学习安排。')
                if d['kind']=='stage_assessment' and (progress['pending'] or progress['assessment_id']!=d['id']): raise AppError('请使用当前阶段的有效评估。')
                if d['kind']=='remedial' and d['assessment_id']!=progress['assessment_id']: raise AppError('请打开最近一次评估对应的补强课程。')
                # Remedial retries are real learning attempts; update that pack's result
                # without duplicating course completion or bypassing a fresh assessment.
                if existing:
                    self.db.execute('UPDATE events SET data=?,created=? WHERE kind=? AND ref=?',(dumps(out),datetime.now().isoformat(timespec='seconds'),d['kind'],d['id']))
                else: self.event(d['kind'],d['id'],out)
                mistakes=[{'prompt':r['prompt'],'explanation':r['explanation']} for r in results if not r['correct']]
                self.set('mistakes',(mistakes+self.get('mistakes',[]))[:30]);out['recorded']=True
                if d['kind']=='stage_assessment' and out['passed']:
                    self.db.execute('INSERT OR IGNORE INTO stage_passes VALUES (?,?,?,?)',(d['stage'],d['id'],datetime.now().isoformat(timespec='seconds'),1))
        if gated: out['progression']=self.progression()
        return out
    def cards(self,p):
        order='rowid DESC' if p.get('order')=='recent' else 'due,word'
        now=datetime.now().isoformat(timespec='seconds')
        return [self._card_payload(r,now) for r in self.db.execute('SELECT * FROM cards ORDER BY '+order)]
    def _card_payload(self,row,now=None,bounded=False):
        data=json.loads(row['data'])
        if bounded:
            # Ignore arbitrary legacy metadata in interactive responses. Export
            # retains the complete saved object through the legacy cards method.
            data={k:data.get(k,'') for k in (*CARD,'source','model','created')}
            for key in CARD:
                text(data[key],key,100 if key=='word' else 6000)
            for key,limit in (('source',500),('model',200),('created',50)):
                if data[key] is not None and (not isinstance(data[key],str) or len(data[key])>limit):
                    raise AppError('词卡资料过长或格式无效，请通过学习档案导出检查。')
        result=dict(data,pronunciation=pronunciation(row['word'],data['reading']),id=row['id'],
                    due=row['due'],interval=row['interval'],repetitions=row['repetitions'],
                    ready=row['due']<=(now or datetime.now().isoformat(timespec='seconds')))
        if bounded and len(dumps(result).encode('utf-8'))>190_000:
            raise AppError('词卡内容过大，请通过学习档案导出检查。')
        return result
    def card_counts(self,now=None):
        now=now or datetime.now().isoformat(timespec='seconds')
        return dict(total=self.db.execute('SELECT COUNT(*) FROM cards').fetchone()[0],
                    due=self.db.execute('SELECT COUNT(*) FROM cards WHERE due<=?',(now,)).fetchone()[0])
    def card_detail(self,p):
        row=self.db.execute('SELECT * FROM cards WHERE id=?',(text(p.get('id'),'词卡ID',100),)).fetchone()
        if not row: raise AppError('学习卡不存在。')
        return self._card_payload(row,bounded=True)
    def card_queue(self,p):
        limit=integer(p.get('limit',5),1,10,'复习数量')
        now=datetime.now().isoformat(timespec='seconds')
        rows=self.db.execute('SELECT * FROM cards WHERE due<=? ORDER BY due,word LIMIT ?',(now,limit))
        return dict(items=[self._card_payload(r,now,bounded=True) for r in rows],counts=self.card_counts(now))
    def cards_page(self,p):
        limit=integer(p.get('limit',30),1,50,'每页数量')
        before=p.get('before')
        if before is not None: integer(before,1,2**63-1,'分页游标')
        query=p.get('query','')
        if not isinstance(query,str) or len(query)>100: raise AppError('搜索词不能超过100字。')
        query=card_key(query)
        self.db.create_function('haru_card_search',1,lambda value:card_key(value or ''),deterministic=True)
        clauses=[];args=[]
        if before is not None: clauses.append('rowid<?');args.append(before)
        if query:
            clauses.append("instr(haru_card_search(word||char(10)||coalesce(json_extract(data,'$.reading'),'')||char(10)||coalesce(json_extract(data,'$.romaji'),'')||char(10)||coalesce(json_extract(data,'$.meaning'),'')),?)>0")
            args.append(query)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        rows=self.db.execute("SELECT rowid AS cursor,id,substr(word,1,100) AS word,substr(json_extract(data,'$.meaning'),1,160) AS meaning,due FROM cards"+where+' ORDER BY rowid DESC LIMIT ?',(*args,limit+1)).fetchall()
        now=datetime.now().isoformat(timespec='seconds');items=rows[:limit]
        return dict(items=[dict(id=r['id'],word=r['word'],meaning=r['meaning'] or '',due=r['due'],ready=r['due']<=now) for r in items],
                    next_cursor=items[-1]['cursor'] if len(rows)>limit else None,counts=self.card_counts(now))
    def store_cards(self,items,require_new=False):
        prepared=[]
        for d in items:
            fields(d,CARD)
            prepared.append(dict(d,word=text(d['word'],'单词',100)))
        # Serialize the check and insert across native IPC / preview requests.
        # Normalize only spelling/width, never merge unrelated homophones.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing={card_key(r['word']):r['id'] for r in self.db.execute('SELECT id,word FROM cards')}
            keys=[card_key(d['word']) for d in prepared]
            if require_new and (len(set(keys))!=len(keys) or any(k in existing for k in keys)):
                return []
            saved=[]
            for d,key in zip(prepared,keys):
                if key in existing:
                    saved.append(existing[key]);continue
                card_id=uuid.uuid4().hex;now=datetime.now().isoformat(timespec='seconds')
                self.db.execute('INSERT INTO cards(id,word,data,due) VALUES (?,?,?,?)',(card_id,d['word'],dumps(dict(d,created=now)),now))
                existing[key]=card_id;saved.append(card_id)
        return saved
    def add_card(self,d,compact=False):
        saved=self.store_cards([d])
        if compact: return dict(card=self.card_detail({'id':saved[0]}),counts=self.card_counts())
        return self.cards({})
    def daily_word(self,p):
        refresh=p.get('refresh',False)
        if type(refresh) is not bool: raise AppError('更换方式无效。')
        try:
            with portalocker.Lock(self.dir/'.daily-word.lock',mode='a',timeout=10):
                today=date.today().isoformat();stage=self.progression()['stage']
                cached=self.get('daily_word_cache',{})
                if not refresh and cached.get('date')==today and cached.get('stage')==stage:
                    return self.content(cached['id'],'daily_word')
                recent=[d['word'] for d in self.history('daily_word')[:10]]
                d=self.ai('为首页“今日的一点日语”选择一个适合当前阶段的常用日语单词或简短表达，生成完整学习卡。word不超过30字，meaning用一句简短中文解释含义及使用场景；提供假名、罗马音、例句及翻译。请尽量避开recent_words，不要原样复制占位符。',
                          dict(self.context(),recent_words=recent),CARD)
                fields(d,CARD)
                data={key:text(d[key],key,30 if key=='word' else 6000) for key in CARD}
                data.update(source=d['source'],model=d['model'])
                result=self.save('daily_word',data)
                with self.db: self.set('daily_word_cache',dict(date=today,stage=stage,id=result['id']))
                return result
        except portalocker.exceptions.LockException:
            raise AppError('今日词正在另一处生成，请稍后重试。') from None
    def daily_word_add(self,p):
        d=self.content(text(p.get('id'),'内容ID',100),'daily_word')
        return self.add_card({key:d[key] for key in (*CARD,'source','model')},compact=p.get('compact') is True)
    def card_seed(self,p):
        saved=self.store_cards([dict(c,source='内置原创',model=None) for c in CARDS])
        if p.get('compact') is True:
            return dict(generated=[self.card_detail({'id':identity}) for identity in saved],counts=self.card_counts())
        return self.cards({})
    def card_create(self,p):
        word=text(p.get('word'),'单词',100)
        d=self.ai('把此日语词汇变成初学者学习卡。若输入中文，提供最常见日语对应词。每次仅一个词；纠正明显拼写错误。',dict(self.context(),word=word),CARD)
        return self.add_card(d,compact=p.get('compact') is True)
    def card_random(self,p):
        count=integer(p.get('count',1),1,10,'随机生成数量')
        topics=['饮食','交通','家居','学校','工作','天气','自然','购物','时间','兴趣','旅行','日常动作']
        candidates={}
        rejected=[]
        for _ in range(3):
            existing=[r['word'] for r in self.db.execute('SELECT word FROM cards ORDER BY rowid DESC')]
            known={card_key(w) for w in existing}
            candidates={k:d for k,d in candidates.items() if k not in known}
            remaining=count-len(candidates)
            excluded=list(dict.fromkeys(rejected+[d['word'] for d in candidates.values()]+existing))[:100]
            result=self.ai('随机选择适合当前学习阶段的常用日语单词，生成 count 张完整学习卡。优先参考主题，但可换主题。必须避开 exclude_words 中的已有词条及其假名、汉字等同词异写，不要只换拼写来伪装新词。本批次每个词必须不同；一张卡仅一个词，不要用例句代替词条。',dict(self.context(),topic=random.choice(topics),count=remaining,exclude_words=excluded),{'cards':[CARD]})
            items=result.get('cards')
            if not isinstance(items,list) or not 1<=len(items)<=remaining:
                raise AppError('AI 返回的词卡数量不正确，本批未保存，请重试。')
            for d in items:
                fields(d,CARD);word=text(d['word'],'单词',100);key=card_key(word)
                if key not in known and key not in candidates:
                    candidates[key]=dict(d,word=word,source=result['source'],model=result['model'])
                elif key in known:
                    rejected.append(word)
            if len(candidates)==count:
                saved=self.store_cards(list(candidates.values()),require_new=True)
                if saved:
                    if p.get('compact') is True:
                        return dict(generated=[self.card_detail({'id':identity}) for identity in saved],counts=self.card_counts())
                    cards=self.cards({'order':'recent'});by_id={c['id']:c for c in cards}
                    return dict(generated=[by_id[x] for x in saved],cards=cards)
        raise AppError(f'三次尝试后仍未凑齐 {count} 个不重复词条，本批未保存。请减少数量或稍后重试。')
    def review(self,p):
        q=integer(p.get('quality'),0,5,'记忆评分')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            r=self.db.execute('SELECT * FROM cards WHERE id=?',(p.get('id'),)).fetchone()
            if not r: raise AppError('学习卡不存在。')
            if r['due']>datetime.now().isoformat(timespec='seconds'): raise AppError('这张卡已安排复习，无需重复评分。')
            reps=r['repetitions']; interval=r['interval']; ease=r['ease']
            if q<3: reps=0; interval=0; due=datetime.now()+timedelta(minutes=10)
            else:
                interval=1 if reps==0 else 6 if reps==1 else math.ceil(interval*ease)
                reps+=1; due=datetime.now()+timedelta(days=interval)
            ease=max(1.3,ease+0.1-(5-q)*(0.08+(5-q)*0.02))
            self.db.execute('UPDATE cards SET ease=?,repetitions=?,interval=?,due=? WHERE id=?',(ease,reps,interval,due.isoformat(timespec='seconds'),r['id']))
            self.event('review',r['id'],{'quality':q,'word':r['word']})
        return dict(due=due.isoformat(timespec='seconds'),interval=interval,
                    card=self.card_detail({'id':r['id']}),counts=self.card_counts(),stats=self.stats())
    def decode(self,p):
        sentence=text(p.get('text'),'日语句子',1200)
        schema=dict(translation='完整中文翻译',reading='完整假名读音',romaji='完整罗马音',structure='一句话说明句子结构',parts=[dict(text='成分',reading='假名',role='语法作用',explanation='中文解释')],pitfall='中文母语者易错点',examples=[EXAMPLE])
        d=self.ai('逐成分解释此日语句子，准确区分助词、时态、敬体；不确定读音时在解释中说明。',{'sentence':sentence,'learner':self.context()},schema)
        fields(d,['translation','reading','romaji','structure','pitfall'])
        if not isinstance(d.get('parts'),list) or not 1<=len(d['parts'])<=30: raise AppError('句子分解结构不完整。')
        for x in d['parts']: fields(x,['text','reading','role','explanation'])
        validate_examples(d.get('examples')); d['sentence']=sentence
        return self.save('decode',d)
    def quiz(self,p):
        ev=self.evidence()
        if not ev['lessons'] and not ev['cards']: raise AppError('本周还没有完成的课程或复习记录。先完成一节课，再来生成测验。')
        if p.get('source')=='builtin':
            questions=[q for l in ev['lessons'] for q in l['questions']][:8]
            if not questions: raise AppError('请先完成一节课程，或选择AI根据已复习单词出题。')
            d=dict(title='本周学习回顾',questions=questions,source='已学课程题目',model=None)
        else:
            safe={'start':ev['start'],'lessons':[{'id':l['id'],'title':l['title'],'grammar':l['grammar'],'examples':l['examples']} for l in ev['lessons']], 'cards':ev['cards']}
            d=self.ai('严格只用提供的本周已学材料，出5道有唯一正确答案的单选测验，覆盖语法/词汇/听力。每题source_id必须为所给课程id或单词word，用于范围校验。至少1道听力。每题skill只能是grammar、kana或listening三者之一（词汇也归grammar）。不要引入未学句型。',safe,dict(title='本周测验',questions=[dict(QUESTION,source_id='对应课程id或单词word')]))
            allowed={l['id'] for l in ev['lessons']}|{c['word'] for c in ev['cards']}
            if not isinstance(d.get('questions'),list) or any(q.get('source_id') not in allowed for q in d['questions'] if isinstance(q,dict)): raise AppError('AI测验来源不在本周学习范围，未保存，请重试。')
        fields(d,['title']); validate_questions(d.get('questions'))
        d['week_start']=ev['start']; d['scope']=[l['title'] for l in ev['lessons']]+[c['word'] for c in ev['cards']]
        return visible_test(self.save('quiz',d))
    def immersion(self,p):
        topic=text(p.get('topic','咖啡店'),'主题',100)
        schema=dict(title='故事标题',sentences=[EXAMPLE],words=[dict(word='生词',meaning='中文',reading='假名')],task='一个现实生活中可以完成的小任务')
        context=dict(self.context(),topic=topic,immersion_story=True)
        task=(
            '以 topic 为故事核心，参考 level、stage、stage_name、goal 和真实学习记录，'
            '写一个10-12句的原创沉浸式日语故事。难度比当前阶段的常见练习略高，'
            '但仍能结合上下文和读音、中文提示理解；不要把阶段编号当作考试等级。'
            '给人物一个具体目的或困扰，让一次小意外、误会、选择或发现推动情节，'
            '写出可感知的场景细节、人物反应和自然对话，结尾回应开头或留下余味。'
            '避免流水账、重复句式、空泛赞美和生硬反转，不要只罗列基础问候。'
            '长短句交替，适度使用符合当前阶段的连接、原因、比较或想法表达；'
            '可加入1-2个从情境能推断的稍难表达，不要突然跳到远超当前阶段的语法。'
            '自然复现适合主题的 review_targets 词语，不适合的可以跳过。'
            'sentences 每项是一句完整日语，并提供准确的完整假名读音、Hepburn 罗马音和中文意思。'
            'words 收录故事中实际出现的5-6个有学习价值的生词，附假名和中文；'
            'task 给出一个与故事呼应、现实生活中可实践的小任务。'
        )
        d=self.ai(task,context,schema)
        fields(d,['title','task']); validate_examples(d.get('sentences'))
        if len(d['sentences'])<8: raise AppError('故事篇幅不足，请重试。')
        if not isinstance(d.get('words'),list) or not 1<=len(d['words'])<=8: raise AppError('生词结构不完整。')
        for w in d['words']: fields(w,['word','meaning','reading'])
        self.apply_review_targets(d,context['review_targets'],[x['jp'] for x in d['sentences']])
        return self.save('immersion',d)
    def export(self,p):
        kind=p.get('type','json'); folder=self.dir/'exports'; folder.mkdir(exist_ok=True)
        if kind=='json':
            data={'version':3,'knowledge':self.knowledge({}),'encounters':[dict(r) for r in self.db.execute('SELECT * FROM encounters')],'annotations': [json.loads(r[0]) for r in self.db.execute('SELECT data FROM annotations')],'dictionary':[json.loads(r[0]) for r in self.db.execute('SELECT data FROM lexical_entries')],'chat_runs':[dict(r) for r in self.db.execute('SELECT * FROM chat_runs')],'progression':self.progression(),'stage_passes':[dict(r) for r in self.db.execute('SELECT * FROM stage_passes ORDER BY stage')],'exported':datetime.now().isoformat(),'profile':self.get('profile'),'stats':self.stats(),'cards':self.cards({}),'plan':self.get('plan'),'content':[json.loads(r[0]) for r in self.db.execute('SELECT data FROM content')], 'events':[dict(r) for r in self.db.execute('SELECT * FROM events')], 'messages':[dict(r) for r in self.db.execute('SELECT id,session,role,data,created FROM messages ORDER BY id')]}
            data['study']=self.study_export()
            raw=json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'); ext='json'
        else: raise AppError('不支持的导出格式。')
        path=folder/('haru-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.'+ext)
        temp=path.with_suffix('.tmp'); temp.write_bytes(raw); temp.replace(path)
        return {'path':str(path),'filename':path.name}
    def ping(self,p):
        d,model=generate('连通性测试，返回{"ok":true}。',{}, {'ok':True})
        if d.get('ok') is not True: raise AppError('API响应不符合测试结构。')
        return {'ok':True,'model':model}
    def list_history(self,p):
        kind=p.get('kind','lesson')
        if kind not in ('lesson','quiz','decode','immersion','stage_assessment','remedial'): raise AppError('类型无效。')
        return [visible_test(d) for d in self.history(kind)]
