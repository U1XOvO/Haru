"""Resumable JLPT author/reviewer jobs. One bounded model call per RPC step.

The reviewer solves a segment without the author's key or explanation. Only
approved segments are assembled, and publishing a paper is atomic with the job.
"""
import copy
import json
import math
import random
import re
import time
import unicodedata
import uuid

from llm import AppError, public_config
from jlpt_blueprints import get_blueprint
from jlpt_quality import (VERSION as QUALITY_VERSION, RUBRIC, GLOBAL_ORDERING, LEVEL_HINTS, guidance,
                          check_order, check_order_review, check_option_analysis, ordering_candidates)


MAX_CONSECUTIVE_TRANSIENT_FAILURES = 5
MAX_SEGMENT_REPAIRS = 4
MAX_TOTAL_REPAIRS = 20
MAX_GLOBAL_REVISIONS = 3
MAX_GLOBAL_REVIEW_ROUNDS = 6
SKILL_TITLES = {'vocabulary':'文字词汇','grammar':'语法','reading':'阅读','listening':'听力'}


class ReviewRejected(AppError):
    def __init__(self, message, question_ids=(), feedback=()):
        super().__init__(message)
        self.question_ids = list(question_ids)
        self.feedback = list(feedback)


class GenerationRequestError(AppError):
    """Retry a model request without discarding its validated input."""

    def __init__(self, message, retry_class='transient'):
        super().__init__(message)
        self.retry_class = retry_class


def classify_sanitized_generation_error(message):
    """Classify only the provider adapter's public, sanitized error text."""
    text = str(message or '')
    if any(marker in text for marker in (
        'LLM_BASE_URL 必须', 'API 返回重定向', 'API 鉴权失败',
        '请先在偏好设置 → AI 连接中填写', '请填写模型 ID', '请填写 API 密钥',
        'API 地址格式无效',
    )):
        return 'configuration'
    match = re.search(r'API 请求失败（HTTP (\d{3})）', text)
    if match:
        status = int(match.group(1))
        return 'transient' if status in (408, 425, 429) or status >= 500 else 'configuration'
    if any(marker in text for marker in (
        '网络连接失败或超时', 'API 调用受限或额度不足',
        '请求暂未完成', '模拟请求暂未完成',
    )):
        return 'transient'
    # Unknown provider/application errors fail closed. The user can explicitly
    # resume after inspecting the sanitized message and correcting configuration.
    return 'permanent'


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def fingerprint(q):
    return '\n'.join(
        ''.join(unicodedata.normalize('NFKC', q.get(k, '')).split())
        for k in ('prompt', 'passage', 'audio_text')) + '\n' + '\n'.join(sorted(
            ''.join(unicodedata.normalize('NFKC', option).split()) for option in q.get('options',[])))


def make_segments(blueprint, mode, type_id=None, count=None):
    types = blueprint['types']
    if mode == 'targeted':
        types = [t for t in types if t['id'] == type_id]
        if not types: raise AppError('请选择当前等级的有效专项题型。')
        if type(count) is not int or count not in (5, 10):
            raise AppError('专项练习请选择 5 或 10 题。')
    elif mode not in ('full', 'compat'):
        raise AppError('请选择整卷或专项出题。')
    result = []; sequence = 0
    for t in types:
        wanted = t['count'] if mode in ('full', 'compat') else count
        # Keep shared passages intact; targeted practice repeats the same unit
        # pattern, with a shorter final unit when the requested count requires it.
        unit_sizes = t.get('units', [1] * t['count'])
        units = []; used = 0; unit_no = 0
        while used < wanted:
            size = min(unit_sizes[unit_no % len(unit_sizes)], wanted-used)
            group = f"{t['id']}-u{unit_no+1}"
            unit = []
            for offset in range(size):
                i = used + offset; sequence += 1
                unit.append(dict(id=f'q{sequence}', type_id=t['id'], skill=t['skill'],
                    section=t['section'] if mode in ('full', 'compat') else 'practice',
                    options_count=t['options_counts'][i % t['count']], material_id=group,
                    locator=t['locators'][i % t['count']] if mode in ('full', 'compat') else f"{t['title']} · {i+1}"))
            units.append(unit); used += size; unit_no += 1
        batches = []; batch = []
        for unit in units:
            if len(unit) > 1:
                if batch: batches.append(batch); batch = []
                batches.append(unit)
            else:
                batch.extend(unit)
                # Ordering reviews enumerate 24 permutations per question;
                # isolate these to keep reasoning/output below the RPC budget.
                if len(batch) == (1 if t.get('format')=='ordering' else 3):
                    batches.append(batch); batch = []
        if batch: batches.append(batch)
        for i, plan in enumerate(batches):
            result.append(dict(title=t['title'] + (f' · {i+1}/{len(batches)}' if len(batches)>1 else ''),
                type_id=t['id'], type_title=t['title'], skill=t['skill'],
                requirements=t['requirements'], material=t.get('material', ''),
                format=t.get('format', ''), plan=plan, count=len(plan), status='pending'))
    return result


class StudyGeneration:
    def init_generation(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS study_generation_jobs '
                        '(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)')

    def _generation(self, identity):
        if not isinstance(identity, str): raise AppError('生成任务编号无效。')
        row = self.db.execute('SELECT data FROM study_generation_jobs WHERE id=?', (identity,)).fetchone()
        if not row: raise AppError('找不到这次生成任务。')
        return json.loads(row[0])

    def _save_generation(self, job):
        job['updated'] = time.time()
        self.db.execute('INSERT OR REPLACE INTO study_generation_jobs VALUES (?,?,?)',
                        (job['id'], encode(job), job['updated']))

    def _public_generation(self, job):
        segments = job['segments']; done = sum(s['status']=='approved' for s in segments)
        index = next((i for i,s in enumerate(segments) if s['status']!='approved'), len(segments))
        global_review=job.get('global_review',{})
        phase = ('complete' if job['status']=='complete' else 'failed' if job['status']=='failed' else
                 ('assembling' if global_review.get('status')=='approved' else 'global_review') if index==len(segments)
                 else 'reviewing' if segments[index]['status']=='drafted' else 'generating')
        current = segments[index] if index<len(segments) else {}
        retrying = bool(current.get('retry_ids')) and job['status'] not in ('complete','cancelled','failed')
        waiting = job['status'] not in ('complete','cancelled','failed')
        return dict(id=job['id'], level=job['level'], mode=job['mode'], status=job['status'], phase=phase,
            client_request_id=job.get('client_request_id',''),
            recovery_count=job.get('recovery_count',0),
            recovery_message=job.get('recovery_message','') if waiting or job['status']=='failed' else '',
            retry_at=job.get('retry_at',0) if waiting else 0,
            retry_after=min(60,max(0,math.ceil(job.get('retry_at',0)-time.time()))) if waiting else 0,
            resume_allowed=bool(job.get('resume_allowed',job['status']=='failed')) if job['status']=='failed' else False,
            failure_kind=job.get('failure_kind','') if job['status']=='failed' else '',
            count=job['count'], completed_questions=sum(s['count'] for s in segments if s['status']=='approved'),
            completed_segments=done, total_segments=len(segments), current_segment=min(index+1,len(segments)),
            current_title=segments[index]['title'] if index<len(segments) else '全局审查' if phase=='global_review' else '整合试卷',
            segments=[dict({k:s[k] for k in ('title','count','status')},retry_count=s.get('retry_count',0)) for s in segments],
            retry_count=sum(s.get('retry_count',0) for s in segments),
            current_retry_count=current.get('retry_count',0), retrying=retrying,
            retry_question_count=len(current.get('retry_ids',[])),
            retry_message='正在自动重出未通过的题目并重新审题，已通过的内容保留。' if retrying else '',
            global_review=dict(status=global_review.get('status','pending'),round=global_review.get('round',0),
                issue_count=global_review.get('issue_count',0),model=global_review.get('model','')),
            global_revision_count=job.get('global_revision_count',0),
            global_review_message=global_review.get('message','所有分段完成后将进行整卷全局审查。'),
            error=job.get('error',''), paper_id=job.get('paper_id'), model=job.get('model',''),
            updated=job['updated'], busy=bool(job.get('lease') and job.get('lease_until',0)>time.time()))

    def generation_pending(self):
        for row in self.db.execute('SELECT data FROM study_generation_jobs ORDER BY updated DESC'):
            job = json.loads(row[0])
            if job['status'] in ('active','failed'): return self._public_generation(job)
        return None

    def study_generation_start(self, p):
        blueprint = get_blueprint(p.get('level'))
        mode = p.get('mode','full')
        if mode not in ('full','targeted'):
            raise AppError('请选择整卷或专项出题。')
        segments = make_segments(blueprint, mode, p.get('type_id'), p.get('count'))
        request_id=p.get('client_request_id','')
        if not isinstance(request_id,str) or (request_id and not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_id)):
            raise AppError('生成请求编号无效。')
        return self._create_generation_job(blueprint,mode,segments,request_id)

    def _create_generation_job(self, blueprint, mode, segments, request_id=''):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if request_id:
                for row in self.db.execute('SELECT data FROM study_generation_jobs ORDER BY updated DESC'):
                    existing=json.loads(row[0])
                    if existing.get('client_request_id')==request_id:
                        return self._public_generation(existing)
            if self.generation_pending(): raise AppError('已有未完成的生成任务，请先继续或取消。')
            job = dict(id=uuid.uuid4().hex, level=blueprint['level'], mode=mode, blueprint=blueprint,
                count=sum(s['count'] for s in segments), segments=segments, status='active',
                configured_model=public_config()['model'], created=time.time(), error='',client_request_id=request_id)
            self._save_generation(job)
        return self._public_generation(job)

    def study_generation_start_compat(self, level, count, skill):
        """Build legacy skill/count requests as a resumable, bounded job."""
        blueprint=copy.deepcopy(get_blueprint(level))
        skill_order=(['vocabulary','grammar','reading','listening','grammar']*2)[:count]
        if skill!='mixed': skill_order=[skill]*count
        type_by_skill={}
        for skill_name in skill_order:
            if skill_name in type_by_skill: continue
            preferred={'grammar':'grammar_form','reading':'reading_short','listening':'listening_response'}
            wanted=preferred.get(skill_name)
            type_by_skill[skill_name]=next((t for t in blueprint['types'] if t['id']==wanted),None) or next(
                (t for t in blueprint['types'] if t['skill']==skill_name),None)
            if type_by_skill[skill_name] is None:
                raise AppError('当前等级不支持所选练习类型。')
        counts={}
        for skill_name in skill_order:
            type_id=type_by_skill[skill_name]['id']
            counts[type_id]=counts.get(type_id,0)+1
        types=[]
        for source in blueprint['types']:
            wanted=counts.get(source['id'],0)
            if not wanted: continue
            target=copy.deepcopy(source);source_count=source['count']
            target['count']=wanted;target['section']='practice'
            target['title']=SKILL_TITLES.get(source['skill'],source['skill'])+'专项'
            for key in ('options_counts','locators'):
                values=source[key]
                target[key]=[values[i%len(values)] for i in range(wanted)]
            units=source.get('units',[1]*source_count)
            target['units']=[units[i%len(units)] for i in range(wanted)]
            types.append(target)
        blueprint['types']=types
        blueprint['count']=count
        blueprint['sections']=[dict(id='practice',title='模拟专项练习',seconds=count*120)]
        segments=make_segments(blueprint,'compat')
        return self._create_generation_job(blueprint,'compat',segments)

    def study_generation_status(self, p):
        return self._public_generation(self._generation(p.get('id')))

    def study_generation_cancel(self, p):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            job = self._generation(p.get('id'))
            if job['status'] not in ('complete','cancelled'):
                job.update(status='cancelled', lease=None, lease_until=0, error='',retry_at=0,recovery_message='')
                # Retain only progress metadata, not an unusable draft collection.
                for segment in job['segments']: segment.pop('questions', None)
                job.pop('candidate',None)
                job.pop('explanation_repairs',None)
                self._save_generation(job)
        return self._public_generation(job)

    def study_generation_step(self, p):
        token = uuid.uuid4().hex
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            job = self._generation(p.get('id'))
            if job['status'] in ('complete','cancelled'): return self._public_generation(job)
            if job['status']=='failed':
                if p.get('resume') is not True or not job.get('resume_allowed',True):
                    return self._public_generation(job)
                job.update(status='active',error='',failure_kind='',resume_allowed=False,
                    recovery_message='已按你的操作恢复任务，正在继续处理并保留已审定的分段。',
                    consecutive_errors=0,retry_at=0)
            if job.get('lease') and job.get('lease_until',0)>time.time():
                return self._public_generation(job)
            if job.get('retry_at',0)>time.time(): return self._public_generation(job)
            # Each step makes one provider call; its 90s timeout plus one SDK
            # retry fits inside this 240s lease. The native 600s watchdog is
            # the final process boundary, not the normal generation duration.
            job.update(status='active', error='', lease=token, lease_until=time.time()+240,retry_at=0)
            if all(s['status']=='approved' for s in job['segments']) and job.get('global_review',{}).get('status')!='approved':
                job.setdefault('global_review',{}).update(status='reviewing',message='全局 reviewer 正在审查完整试卷。')
            self._save_generation(job)
        segment = next((s for s in job['segments'] if s['status']!='approved'), None)
        previous_failures=job.get('consecutive_errors',0)
        paper = None
        try:
            if segment is None:
                if job.get('explanation_repairs'):
                    self._edit_generation_explanations(job)
                elif job.get('global_review',{}).get('status')!='approved':
                    self._global_review_generation(job)
                else:
                    paper = self._approved_generation(job)
                    job.update(status='complete', paper_id=paper['id'], model=paper['model'])
            elif segment['status']=='pending':
                self._author_segment(job, segment)
            else:
                self._review_segment(job, segment)
            if job['status']!='failed':
                job.update(recovery_message='',consecutive_errors=0)
        except ReviewRejected as e:
            # Review rejection is part of generation, not an interrupted job.
            # Keep the original plan and accepted questions across RPCs/restarts.
            self._repair_failed_generation(job,segment,e)
            if job['status']!='failed':
                job.update(status='active',error='',recovery_message='',consecutive_errors=0)
        except GenerationRequestError as e:
            self._schedule_generation_recovery(job,str(e),e.retry_class)
        except Exception:
            # Do not persist or expose internal exception text. Unknown errors
            # fail closed because they cannot be safely classified for retry.
            self._terminal_generation(job,'internal',
                '本次内容处理发生无法自动判断的内部错误，任务已暂停；已通过分段仍保留。',False)
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                current = self._generation(job['id'])
                # Cancelled or replaced leases cannot publish late API responses.
                if current.get('lease') != token: return self._public_generation(current)
                saved=copy.deepcopy(job)
                if paper is not None and job['status']=='complete':
                    self.db.execute('INSERT INTO study_papers VALUES (?,?,?)',
                                    (paper['id'], encode(paper), time.time()))
                    # Clear private drafts only in the transaction's final copy.
                    for item in saved['segments']: item.pop('questions',None)
                    saved.pop('candidate',None)
                saved.update(lease=None, lease_until=0)
                self._save_generation(saved)
            job=saved
        except Exception:
            # A rolled-back save must retain the reviewed candidate and release
            # its lease. If storage remains unavailable the UI retries the RPC.
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                current=self._generation(job['id'])
                if current.get('lease')!=token: return self._public_generation(current)
                job.pop('paper_id',None)
                job['consecutive_errors']=max(previous_failures,job.get('consecutive_errors',0))
                if job['status']!='failed':
                    self._schedule_generation_recovery(job,'进度或试卷暂未保存，正在自动重试保存。')
                job.update(lease=None,lease_until=0)
                self._save_generation(job)
        return self._public_generation(job)

    def _generation_ai(self, task, context, schema):
        try:
            return self.ai(task,context,schema)
        except Exception as e:
            # AppError messages are sanitized by the provider adapter; never
            # expose arbitrary exceptions or raw provider response bodies.
            message=str(e) if isinstance(e,AppError) else '模型请求未完成。'
            retry_class=classify_sanitized_generation_error(message) if isinstance(e,AppError) else 'internal'
            raise GenerationRequestError(message,retry_class) from None

    def _terminal_generation(self, job, kind, message, resume_allowed):
        job.update(status='failed',error=message,failure_kind=kind,
                    resume_allowed=bool(resume_allowed),retry_at=0,lease=None,lease_until=0,
                    recovery_message=message,consecutive_errors=job.get('consecutive_errors',0))

    def _schedule_generation_recovery(self, job, message, retry_class='transient'):
        if retry_class!='transient':
            self._terminal_generation(job,
                'internal' if retry_class=='internal' else 'configuration' if retry_class=='configuration' else 'provider_error',
                message,retry_class!='internal')
            return
        failures=job.get('consecutive_errors',0)+1
        job['recovery_count']=job.get('recovery_count',0)+1
        if failures>=MAX_CONSECUTIVE_TRANSIENT_FAILURES:
            self._terminal_generation(job,'transient_limit',
                '连续暂时性请求失败已达到自动重试上限。'+message+' 已保存的审定进度保留；检查网络、额度或服务状态后，点击“继续生成”显式恢复。',True)
            job['consecutive_errors']=failures
            return
        job.update(status='active',error='',consecutive_errors=failures,
            recovery_message=message+' 将按退避间隔自动重试，已有内容保留，可随时取消。',
            retry_at=time.time()+min(60,2**min(failures,6)))

    def _repair_failed_generation(self, job, segment, error):
        targets=[segment] if segment is not None else job['segments']
        ids=set(error.question_ids)
        if segment is not None and not ids:
            ids=set(segment.get('retry_ids',[])) | set(segment.get('pending_review_ids',[]))
        for target in targets:
            affected={p['id'] for p in target['plan']}
            if ids: affected &= ids
            if affected:
                feedback=error.feedback or [dict(id=identity,issues=str(error)) for identity in sorted(affected)]
                if not self._queue_generation_repair(job,target,affected,feedback): break

    def _queue_generation_repair(self, job, segment, question_ids, feedback):
        ids=set(question_ids) or {q['id'] for q in segment['plan']}
        total=sum(s.get('retry_count',0) for s in job['segments'])
        if segment.get('retry_count',0)>=MAX_SEGMENT_REPAIRS or total>=MAX_TOTAL_REPAIRS:
            self._terminal_generation(job,'repair_limit',
                '自动返修已达到本任务上限，任务已暂停并保留已通过分段。请取消此任务后调整范围再重新开始。',False)
            return False
        history=segment.setdefault('repair_history',[])
        history.append(dict(question_ids=sorted(ids),feedback=copy.deepcopy(feedback)))
        del history[:-3]
        segment.update(status='pending',retry_ids=[q['id'] for q in segment['plan'] if q['id'] in ids],
                       retry_count=segment.get('retry_count',0)+1,retry_feedback=feedback)
        job.pop('candidate',None)
        if job.get('global_review',{}).get('status')=='approved':
            job['global_review']['status']='changes_requested'
        return True

    def _segment_context(self, job, segment):
        return dict(level=job['level'], type_id=segment['type_id'], skill=segment['skill'],
            count=segment['count'], requirements=segment['requirements'],
            question_plan=segment['plan'], material=segment['material'],
            quality_contract=guidance(segment),
            level_expectation=LEVEL_HINTS[job['level']],
            blueprint='JLPT 官方问题集第二集（2018）题型；内容必须原创',
            grammar_reference=[{k:g[k] for k in ('id','title','connection','pitfall')}
                               for g in self.grammar_items() if g['level']==job['level'] and segment['skill']=='grammar'])

    def _author_segment(self, job, segment):
        retry_ids=set(segment.get('retry_ids',[]))
        plan=[p for p in segment['plan'] if not retry_ids or p['id'] in retry_ids]
        working=dict(segment,plan=plan,count=len(plan))
        context = self._segment_context(job,working)
        context['_jlpt_role']='author'
        context['previous_questions'] = [dict(id=q['id'],type_id=s['type_id'],prompt=q['prompt'],options=q['options'])
                                        for s in job['segments'] for q in s.get('questions',[])]
        retained=[q for q in segment.get('questions',[]) if q['id'] not in retry_ids] if retry_ids else []
        fixed_materials={q['group']:{'id':q['group'],'passage':q['passage'],'audio_text':q['audio_text']}
                         for q in retained if q['group'] in {p['material_id'] for p in plan}}
        if retry_ids:
            context.update(retry_feedback=segment.get('retry_feedback',[]),
                recent_failures=segment.get('repair_history',[]),
                repair_strategy=('同一题反复失败：更换考点和句子结构，从完整正确句重新设计，不做表面改词。'
                                 if segment.get('retry_count',0)>=2 else '对照具体反馈修正，并重新检验每个选项。'),
                rejected_questions=[q for q in segment.get('questions',[]) if q['id'] in retry_ids],
                retained_questions=retained,fixed_materials=list(fixed_materials.values()))
        schema = dict(level=job['level'], materials=[dict(id='question_plan中的material_id', passage='', audio_text='')],
            questions=[dict(id=p['id'], type_id=p['type_id'], skill=p['skill'], material_id=p['material_id'],
                prompt='完整日语题干', options=['日语选项']*p['options_count'], answer=0,
                explanation='中文解释正确答案与每个干扰项；仅引用选项原文，不写选项编号或字母', grammar_ids=[])
                for p in plan])
        for q in schema['questions']:
            if segment['type_id']=='grammar_order':
                q.update(solution_order=[0,1,2,3],completed_sentence='四个片段按正确顺序全部填入后的完整日语句子')
            else:
                q['option_checks']=[dict(option='对应选项原文',fits='布尔值：符合题意为true，否则false',reason='具体接续/词义/材料依据；正解为true，其余false')
                                    for _ in q['options']]
        draft = self._generation_ai('你是独立命题子智能体，按提供的官方题型要求创作一个试卷分段。仅遵守输入level目标难度，'
            '不使用日常课程阶段。不可复制官方原题或宣称真题。严格遵守question_plan的id、type_id、skill、material_id、选项数。'
            '所有题都必须提供足够上下文且只有一个可辩护答案。不得靠解析补充题干缺失的信息。'
            '相同material_id必须共用一篇完整材料，在materials仅写一次；各材料与题目应相互对应。'
            'material=passage时必须给日语文章；material=audio_text时给完整可独立朗读的日语听力脚本，含问题及口头选项，'
            '题干不可直接暴露听力答案。题型要求图示时写等价文字场景，不要依赖不存在的图片。'
            '排序题保留四个空位及一个★，答案是★对应片段。选项不可重复。语法ID只能来自参考目录，可为空。'
            '若有retry_feedback，针对审题指出的问题重新命题，只输出question_plan要求的题。'
            '反馈中的reviewed_question是全局实际审阅的版本，选项可能已打乱；对照选项原文修正，不要混淆不同版本的编号。'
            'retained_questions为已保留且不可修改的题，fixed_materials为它们共享的材料，必须逐字复用，不可重写。'
            'previous_questions包含所有已生成题型：跨题型也不要复用同一核心完整句，仅更换考查词或挖空位置仍是重复。'
            '相同语法考点或题干框架配不同具体内容可以构成不同题，不必为避免重复牺牲目标等级。'
            '确保接续、词义、时态、肯否和阅读推理严谨。'
            '执行quality_contract：排序题先构完整句再切四片段，返回solution_order与completed_sentence；'
            '其他题返回与options逐项同序的option_checks，fits只能有一个true，且与answer一致。'
            '无阅读/听力材料的题materials中的passage和audio_text必须为空，不能额外提供提示答案的句子。', context, schema)
        try:
            questions, feedback = self._validate_segment(job,working,draft)
            for q in questions:
                fixed=fixed_materials.get(q['group'])
                if fixed and any(q[key]!=fixed[key] for key in ('passage','audio_text')):
                    # Some repairs (especially spoken options) necessarily alter
                    # a shared material. Re-author/re-review its entire group.
                    group_ids={p['id'] for p in segment['plan'] if p['material_id']==q['group']}
                    raise ReviewRejected('共享材料需要随关联题目一起修正。',retry_ids|group_ids,
                        segment.get('retry_feedback',[])+[dict(id=q['id'],issues='修改涉及共享材料；请重新生成关联材料组，保证所有关联题与新材料一致。')])
            seen = {fingerprint(q) for s in job['segments'] for q in s.get('questions',[])
                    if s is not segment or q['id'] not in retry_ids}
            unique=[]
            for q in questions:
                if fingerprint(q) in seen:
                    feedback.append(dict(id=q['id'],issues='本题与已生成题目重复，请重新命题。'))
                else: unique.append(q)
            questions=unique
        except ReviewRejected:
            raise
        except AppError as e:
            raise ReviewRejected('命题内容需要修正。',[p['id'] for p in plan],
                                 [dict(id=p['id'],issues=str(e)) for p in plan]) from None
        byid={q['id']:q for q in retained+questions}
        models=segment.setdefault('question_models',{
            q['id']:dict(model=segment.get('author_model',''),configured_model=segment.get('configured_model',''))
            for q in retained})
        for p in plan:
            models.pop(p['id'],None)
            segment.get('question_review_models',{}).pop(p['id'],None)
            segment.get('question_explanation_edits',{}).pop(p['id'],None)
        for q in questions:
            models[q['id']]=dict(model=draft['model'],configured_model=public_config()['model'],
                quality_version=QUALITY_VERSION,request_profile=draft.get('_generation_meta',{}))
        # Authoring repairs and independent review have separate queues. Valid
        # siblings of a duplicate-option question have not been reviewed yet.
        pending_review=set(segment.get('pending_review_ids',[])) | {p['id'] for p in plan}
        segment.update(status='pending' if feedback else 'drafted',
                       questions=[byid[p['id']] for p in segment['plan'] if p['id'] in byid],
                       pending_review_ids=[p['id'] for p in segment['plan'] if p['id'] in pending_review],
                       author_model=' / '.join(dict.fromkeys(m['model'] for m in models.values())),
                       configured_model=public_config()['model'])
        if feedback:
            raise ReviewRejected('题目校验未通过，自动重新命题。',[f['id'] for f in feedback],feedback)

    def _validate_segment(self, job, segment, raw):
        from study import validate_paper
        if not isinstance(raw,dict) or raw.get('level')!=job['level'] or not isinstance(raw.get('questions'),list) or len(raw['questions'])!=segment['count']:
            raise AppError('本段等级或题数不符，请继续重新命题。')
        materials = raw.get('materials', [])
        if not isinstance(materials,list) or len(materials)>segment['count']: raise AppError('本段材料结构无效。')
        by_material = {}
        for m in materials:
            if not isinstance(m,dict) or not isinstance(m.get('id'),str) or m['id'] in by_material: raise AppError('本段材料编号无效。')
            by_material[m['id']] = m
        questions=[]; feedback=[]; seen=set()
        grammar_ids={g['id'] for g in self.grammar_items() if g['level']==job['level']}
        for q,p in zip(raw['questions'],segment['plan']):
            try:
                if not isinstance(q,dict) or any(q.get(k)!=p[k] for k in ('id','type_id','skill','material_id')):
                    raise AppError('本题题号、题型或题组与计划不符，请重新命题。')
                if not isinstance(q.get('options'),list) or len(q['options'])!=p['options_count']:
                    raise AppError(f"本题选项数与官方题型不符，必须恰好有 {p['options_count']} 个选项。")
                if all(isinstance(option,str) for option in q['options']):
                    keys=[' '.join(unicodedata.normalize('NFKC',option).split()) for option in q['options']]
                    if len(set(keys))!=len(keys): raise AppError('本题选项重复（包括空白或全半角差异）。')
                q=copy.deepcopy(q); material=by_material.get(p['material_id'],{})
                q.update(section=p['section'],group=p['material_id'],locator=p['locator'])
                for key in ('passage','audio_text'): q[key]=material.get(key,'')
                if segment['material'] and not q.get(segment['material']): raise AppError('本题阅读／听力材料不完整。')
                if segment.get('format')=='ordering':
                    prompt=q.get('prompt','')
                    slots=re.findall(r'[_＿]{2,}|★',prompt) if isinstance(prompt,str) else []
                    if len(slots)!=4 or slots.count('★')!=1:
                        raise AppError('句子排序题必须有四个位置，其中恰好一个为★。')
                    check_order(q,require_sentence=True)
                else:
                    check_option_analysis(q['options'],q.get('answer'),q.get('option_checks'))
                if not segment['material'] and (q['passage'] or q['audio_text']):
                    raise AppError('本题不需要额外材料，passage和audio_text必须为空，避免材料泄露答案。')
                if segment['type_id'] in ('kanji_reading','orthography','paraphrase'):
                    if len(re.findall(r'【[^【】]+】',q.get('prompt','')))!=1:
                        raise AppError('必须用一对【】明确标注唯一的考查词语。')
                if q.get('audio_asset') or q.get('image_asset'): raise AppError('AI 不能引用未导入媒体。')
                q=validate_paper(dict(title='生成分段',level=job['level'],
                    sections=[dict(id=p['section'],title='生成分段',seconds=600)],questions=[q]),grammar_ids)['questions'][0]
                q.update(type_id=segment['type_id'],type_title=segment['type_title'])
                if fingerprint(q) in seen: raise AppError('本题与本段其他题目重复。')
                seen.add(fingerprint(q));questions.append(q)
            except (AppError,TypeError,ValueError) as e:
                feedback.append(dict(id=p['id'],issues=str(e) if isinstance(e,AppError) else '本题字段格式无效，请完整按schema重新命题。'))
        return questions, feedback

    def _review_segment(self, job, segment):
        retry_ids=set(segment.get('pending_review_ids',[])) | set(segment.get('retry_ids',[]))
        questions=[q for q in segment['questions'] if not retry_ids or q['id'] in retry_ids]
        working=dict(segment,count=len(questions),plan=[p for p in segment['plan'] if not retry_ids or p['id'] in retry_ids])
        context=self._segment_context(job,working)
        context['_jlpt_role']='reviewer'
        context['previous_review_error']=segment.get('review_validation_error','')
        # Independent solving: the answer key and explanations are deliberately
        # absent, preventing a rubber-stamp review of the author's claimed answer.
        context['questions']=[{k:q[k] for k in ('id','type_id','skill','prompt','options','passage','audio_text','group')}
                              for q in questions]
        context['retained_questions']=[{k:q[k] for k in ('id','type_id','skill','prompt','options','passage','audio_text','group')}
                                       for q in segment['questions'] if retry_ids and q['id'] not in retry_ids]
        if segment['type_id']=='grammar_order':
            context['ordering_candidates']={q['id']:ordering_candidates(q) for q in questions}
        review_schema=dict(approved=True,reviews=[dict(id=q['id'],answer=0,approved=True,
            explanation='独立解题中文解析',issues='',material_issue=False) for q in questions])
        for r,q in zip(review_schema['reviews'],questions):
            if segment['type_id']=='grammar_order':
                r.update(solution_order=[0,1,2,3],possible_star_options=[0])
                r['permutation_checks']=[dict(solution_order=c['solution_order'],valid='布尔值：此完整拼接是否自然成立',
                    reason='具体接续或语义依据；不能仅说不常见或不符合命题顺序')
                    for c in context['ordering_candidates'][q['id']]['candidates']]
            else:
                r['option_checks']=[dict(option=o,fits='布尔值：符合题意为true，否则false',reason='逐项代入及具体判定依据') for o in q['options']]
        result=self._generation_ai('你是独立审题子智能体。你没有命题者的答案；请独立解答每一道题，逐个代入选项。'
            '检查日语自然度、目标等级、题型要求、材料充分性、唯一正确答案及组内逻辑。'
            '若需要补充题干外的信息才能排除其他答案，或依赖不存在的图片、材料，应判approved=false。'
            '不得因为存在一个较好答案就忽略其他也能成立的答案。返回每题独立得出的answer(0起始)、'
            'approved和中文explanation；explanation解释正确及错误选项，引用原文，不写选项字母或编号。'
            'issues只写具体问题，不修改题干。若问题出在共享文章或音频本身，material_issue=true，'
            '否则false。retained_questions仅供检查共享材料逻辑，不要再次审校或返回这些已通过题的结论。'
            'questions所有题都通过才总approved=true；未通过时仍逐题完整返回，明确具体不合格题。'
            '普通选择题用option_checks逐项判定；即使有多个成立选项也如实标记fits=true并拒绝，不要迎合schema。'
            '排序题的四项全部使用，没有干扰项；ordering_candidates为程序穷举的24种完整拼接，并非参考答案。'
            '逐个检验这些句子，返回自然正确的solution_order及所有可成立的★选项possible_star_options；'
            'permutation_checks须对24种排列不重不漏逐项返回布尔valid和具体理由，汇总所有valid=true的★索引。'
            '特别主动检验宾语、主语、状语前置的替代语序；不能仅因常见程度较低、没有逗号或不同于首选答案而判错。'
            '只有唯一★答案时才能通过。★算一个位置，不要把四个位置误数为三个。', context,review_schema)
        reviews=result.get('reviews')
        ids=[q['id'] for q in questions]
        if (not isinstance(reviews,list) or len(reviews)!=len(questions) or
            any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in reviews) or
            len({r['id'] for r in reviews})!=len(ids) or {r['id'] for r in reviews}!=set(ids)):
            raise ReviewRejected('审题未能逐题确认，自动重新命题。',ids,
                                 [dict(id=identity,issues='上次审题未能逐题确认，请重新检查题型、上下文与唯一答案。') for identity in ids])
        byid={r['id']:r for r in reviews}; rejected=set(); feedback=[]; bad_materials=set()
        # A self-contradictory review is not evidence that the question is bad.
        # Retry the unchanged blind input twice, then fall back to scoped repair.
        inconsistent=[]
        for r in reviews:
            checks=r.get('option_checks')
            if (segment['type_id']!='grammar_order' and r.get('approved') is True and
                isinstance(checks,list) and checks and
                all(isinstance(c,dict) and c.get('fits') is False for c in checks)):
                inconsistent.append(r['id'])
            permutations=r.get('permutation_checks')
            if (segment['type_id']=='grammar_order' and r.get('approved') is True and
                isinstance(permutations,list) and permutations and
                all(isinstance(c,dict) and c.get('valid') is False for c in permutations)):
                inconsistent.append(r['id'])
        if inconsistent and segment.get('review_format_retries',0)<2:
            segment['review_format_retries']=segment.get('review_format_retries',0)+1
            segment['review_validation_error']='上次审查在'+','.join(inconsistent)+'中approved=true却所有fits或valid=false。请独立重审，正确项或成立排列须标true，不能照抄schema占位符。'
            raise GenerationRequestError('审查返回的逐项结论自相矛盾，正在重新独立审查原题。')
        segment.pop('review_validation_error',None)
        explanations={}
        review_models=segment.setdefault('question_review_models',{})
        for q in questions:
            r=byid[q['id']]; explanation=r.get('explanation')
            evidence_error=''
            try:
                if segment['type_id']=='grammar_order':
                    check_order_review(q,r)
                else:
                    check_option_analysis(q['options'],r.get('answer'),r.get('option_checks'))
            except AppError as e: evidence_error=str(e)
            if (r.get('approved') is not True or type(r.get('answer')) is not int or r['answer']!=q['answer'] or
                not isinstance(explanation,str) or not explanation.strip() or len(explanation)>8000 or
                r.get('material_issue') is True or evidence_error):
                rejected.add(q['id'])
                issues=r.get('issues')
                feedback.append(dict(id=q['id'],issues=issues[:1200] if isinstance(issues,str) and issues.strip()
                                     else evidence_error or '独立审题答案不一致、解析无效或发现歧义；请重新命题，确保只有一个可辩护答案。',
                    author_answer=q['options'][q['answer']],
                    reviewer_answer=q['options'][r['answer']] if type(r.get('answer')) is int and 0<=r['answer']<len(q['options']) else None,
                    reviewer_explanation=explanation[:2400] if isinstance(explanation,str) else '',
                    option_checks=r.get('option_checks',[]),
                    valid_orderings=[c for c in r.get('permutation_checks',[]) if isinstance(c,dict) and c.get('valid') is True][:4]
                        if isinstance(r.get('permutation_checks',[]),list) else []))
                if r.get('material_issue') is True: bad_materials.add(q['group'])
            else:
                explanations[q['id']]=explanation.strip()
                review_models[q['id']]=result['model']
        if result.get('approved') is not True and not rejected:
            rejected.update(ids)
            feedback=[dict(id=identity,issues='本段整体未获批准，请重新核对题型、材料与唯一答案。') for identity in ids]
        if bad_materials:
            rejected.update(q['id'] for q in segment['questions'] if q['group'] in bad_materials)
        for q in questions:
            if q['id'] not in rejected and q['id'] in explanations: q['explanation']=explanations[q['id']]
        segment.pop('pending_review_ids',None)
        for identity in rejected: review_models.pop(identity,None)
        if rejected: raise ReviewRejected('未通过的题目将自动重出。',rejected,feedback)
        segment.pop('review_format_retries',None)
        segment.pop('retry_ids',None);segment.pop('retry_feedback',None)
        segment.update(status='approved',review_model=' / '.join(dict.fromkeys(review_models.values())), reviewed_at=time.time())

    def _global_review_generation(self, job):
        # Review exactly the candidate that will be saved, including shuffled
        # written options. Material text is included once, never truncated.
        audit=job.setdefault('global_review',{})
        if audit.get('round',0)>=MAX_GLOBAL_REVIEW_ROUNDS:
            self._terminal_generation(job,'global_review_limit',
                '整卷审查达到本任务轮数上限，任务已暂停并保留已审定分段。请取消此任务后重新开始。',False)
            return
        candidate=job.get('candidate') or self._assemble_generation(job)
        job['candidate']=candidate
        materials={}
        questions=[]
        for q in candidate['questions']:
            materials.setdefault(q['group'],dict(id=q['group'],passage=q['passage'],audio_text=q['audio_text']))
            questions.append({k:q[k] for k in ('id','section','type_id','type_title','skill','prompt','options','answer','explanation','group')})
        context=dict(review_scope='global',level=job['level'],mode=job['mode'],count=job['count'],
            _jlpt_role='global_reviewer',quality_contract=dict(version=QUALITY_VERSION,rubric=RUBRIC,ordering=GLOBAL_ORDERING),
            level_expectation=LEVEL_HINTS[job['level']],
            sections=candidate['sections'],questions=questions,materials=list(materials.values()),
            ordering_candidates={q['id']:ordering_candidates(q) for q in candidate['questions']
                                 if q['type_id']=='grammar_order'},
            question_types=[dict({k:t[k] for k in ('id','title','count','requirements')},
                                quality_contract=(dict(version=QUALITY_VERSION,ordering=GLOBAL_ORDERING)
                                    if t['id']=='grammar_order' else guidance(dict(type_id=t['id'])))) for t in job['blueprint']['types']
                            if any(q['type_id']==t['id'] for q in questions)],
            issue_format=dict(question_ids=['需修改的实际题目ID'],reason='具体修改原因',material_issue=False,
                              scope='explanation 或 question'),
            round=audit.get('round',0)+1,previous_review_error=audit.get('validation_error',''))
        result=self._generation_ai('你是独立的整卷全局 reviewer。所有题已局部审题；现在必须重新审查完整试卷的整体一致性，'
            '不能因已局部通过而直接批准。检查跨分段重复或近乎重复的题目、目标等级和难度分布、题型编排、'
            '共用阅读/听力材料与题目间矛盾、选项和答案/解析的一致性、听力口头选项与屏幕选项是否一致、'
            '单选唯一性以及排序题结构。questions含最终选项顺序、0起始答案与中文解析；group引用materials完整材料。'
            '专项mode=targeted仅要求选定题型和count，question_types中的count是整卷蓝图数量，不要求专项照搬。'
            '发现问题时approved=false并给issues（每项格式见issue_format），每项必须列出确实需要修改的question_ids、具体reason，'
            '若问题涉及共享材料本身或口头选项需material_issue=true。重复题只标记需要重写的那一题。'
            '每项scope须为explanation或question：仅解析语言、错字或说明不清，且题目与答案本身正确唯一时，'
            '用explanation；答案错误、歧义、重复、等级或材料问题均用question，不可用改解析掩盖题面缺陷。'
            'reason必须引用选项原文而非选项字母或编号，避免后续重写时顺序混淆。'
            '排序题的★本身占一个位置，三个下划线加一个★共四个位置；所有四个选项均须使用，无干扰项。'
            '专项中复现同一考点很正常；不得仅因相同考点或挖空句式就判重复。重复须有题干、选项或核心材料的实质重复。'
            '不要直接修改题目或调整题量，不要输出整卷副本。全部无须修改时approved=true且issues=[]。'
            'summary简要描述审查结果，不声称人工审核或官方认证。',context,
            dict(approved=True,summary='整卷审查简述',issues=[]))
        audit.update(round=context['round'],model=result['model'],reviewed_at=time.time())
        issues=result.get('issues');known={q['id']:q for q in candidate['questions']}
        valid=(type(result.get('approved')) is bool and isinstance(result.get('summary'),str) and
               0<len(result['summary'].strip())<=2000 and isinstance(issues,list) and len(issues)<=job['count'])
        if valid:
            for issue in issues:
                if (not isinstance(issue,dict) or not isinstance(issue.get('question_ids'),list) or
                    not issue['question_ids'] or len(issue['question_ids'])>job['count'] or
                    any(not isinstance(identity,str) or identity not in known for identity in issue['question_ids']) or
                    not isinstance(issue.get('reason'),str) or not 0<len(issue['reason'].strip())<=2000 or
                    type(issue.get('material_issue',False)) is not bool or
                    issue.get('scope','question') not in ('question','explanation')):
                    valid=False;break
        if not valid or (result.get('approved') is False and not issues):
            audit.update(status='pending',issue_count=0,
                validation_error='上次返回格式无效，或未批准但没有给出具体需修改题号；请完整按schema返回。',
                message='全局审查尚未提供有效修改结论，正在自动重新审查。')
            return
        audit.pop('validation_error',None)
        if issues:
            if job.get('global_revision_count',0)>=MAX_GLOBAL_REVISIONS:
                self._terminal_generation(job,'global_revision_limit',
                    '整卷修改已达到本任务上限，任务已暂停并保留已审定分段。请取消此任务后重新开始。',False)
                return
            rejected=set();feedback=[];explanation_ids=set()
            for issue in issues:
                if issue.get('scope')=='explanation' and not issue.get('material_issue'):
                    explanation_ids.update(issue['question_ids'])
                else:
                    rejected.update(issue['question_ids'])
                for identity in issue['question_ids']:
                    feedback.append(dict(id=identity,issues='全局审查：'+issue['reason'],
                        reviewed_question={k:known[identity][k] for k in ('prompt','options','answer','explanation')}))
                    if issue.get('material_issue'):
                        rejected.update(q['id'] for q in candidate['questions'] if q['group']==known[identity]['group'])
            for segment in job['segments']:
                affected={p['id'] for p in segment['plan']} & rejected
                if affected:
                    if not self._queue_generation_repair(job,segment,affected,
                            [f for f in feedback if f['id'] in {p['id'] for p in segment['plan']}]):
                        return
            # Content/material repair takes precedence if the same question has
            # several issues. Explanation-only edits preserve the reviewed key.
            explanation_ids-=rejected
            job['explanation_repairs']=[f for f in feedback if f['id'] in explanation_ids]
            job['global_revision_count']=job.get('global_revision_count',0)+1
            audit.update(status='changes_requested',issue_count=len(issues),
                message=f'全局审查发现 {len(issues)} 项需调整，重审 {len(rejected)} 题、修正 {len(explanation_ids)} 题解析；完成后重新审查整卷。')
        else:
            audit.update(status='approved',issue_count=0,message='全局审查已通过，正在整合保存试卷。')

    def _edit_generation_explanations(self, job):
        feedback=job['explanation_repairs']
        ids={f['id'] for f in feedback}
        questions=[q for s in job['segments'] for q in s['questions'] if q['id'] in ids]
        result=self._generation_ai('你是解析编辑。题目已经独立解答并确认答案；只按反馈修正中文解析。'
            '逐项引用选项原文说明正确或错误的材料/语法依据，不使用选项编号。禁止修改题干、选项、材料或答案，'
            '禁止为了维护给定答案而编造依据。若发现题面或答案实际有问题，返回can_edit=false和具体reason，'
            '交还命题流程处理；否则can_edit=true，返回完整中文explanation。',
            dict(_jlpt_role='explanation_editor',level=job['level'],count=len(questions),
                 questions=questions,feedback=feedback,quality_contract=RUBRIC),
            dict(edits=[dict(id=q['id'],can_edit=True,explanation='完整中文解析',reason='') for q in questions]))
        edits=result.get('edits')
        if (not isinstance(edits,list) or len(edits)!=len(ids) or
            any(not isinstance(e,dict) or set(e)!={'id','can_edit','explanation','reason'} or
                not isinstance(e['id'],str) or type(e['can_edit']) is not bool or
                not isinstance(e['explanation'],str) or len(e['explanation'])>8000 or
                (e['can_edit'] and not e['explanation'].strip()) or
                not isinstance(e['reason'],str) or len(e['reason'])>2000 or
                (not e['can_edit'] and not e['reason'].strip()) for e in edits) or
            {e['id'] for e in edits}!=ids):
            raise GenerationRequestError('解析修正格式无效，保留原题并重新请求。')
        byid={e['id']:e for e in edits};rejected={e['id'] for e in edits if not e['can_edit']}
        for segment in job['segments']:
            for q in segment['questions']:
                if q['id'] in ids-rejected:
                    q['explanation']=byid[q['id']]['explanation'].strip()
                    segment.setdefault('question_explanation_edits',{})[q['id']]=dict(
                        model=result['model'],quality_version=QUALITY_VERSION,
                        request_profile=result.get('_generation_meta',{}))
            affected={p['id'] for p in segment['plan']} & rejected
            if affected:
                if not self._queue_generation_repair(job,segment,affected,
                        [dict(id=i,issues=byid[i]['reason']) for i in sorted(affected)]):
                    return
        # If only explanations changed, keep the exact option order of the
        # previous global candidate. A fresh global review is still mandatory.
        if job.get('candidate'):
            for q in job['candidate']['questions']:
                if q['id'] in ids: q['explanation']=byid[q['id']]['explanation'].strip()
        job.pop('explanation_repairs',None)
        job['global_review'].update(status='pending',message='解析已修正，正在重新审查完整试卷。')

    def _approved_generation(self, job):
        if job.get('explanation_repairs') or job.get('global_review',{}).get('status')!='approved' or not job.get('candidate'):
            raise AppError('整卷尚未通过全局审查，不能保存。')
        paper=copy.deepcopy(job['candidate']);audit=job['global_review']
        paper['global_review_model']=audit['model']
        paper['generation']['global_review']=dict(approved=True,model=audit['model'],rounds=audit['round'],
            revision_count=job.get('global_revision_count',0),reviewed_at=audit['reviewed_at'],quality_version=QUALITY_VERSION)
        paper['generation']['recovery_count']=job.get('recovery_count',0)
        paper['generation']['explanation_edits']={identity:meta for s in job['segments']
            for identity,meta in s.get('question_explanation_edits',{}).items()}
        paper['notes']=paper['notes'].replace('经独立审题子智能体复核','经独立审题子智能体与整卷全局 reviewer 复核')
        return paper

    def _assemble_generation(self, job):
        from study import validate_paper
        blueprint=job['blueprint']; segments=job['segments']
        questions=[copy.deepcopy(q) for s in segments for q in s['questions']]
        invalid=[p['id'] for s in segments if s['status']!='approved' or
                 [q.get('id') for q in s['questions']]!=[p['id'] for p in s['plan']] for p in s['plan']]
        if invalid or len(questions)!=job['count']:
            raise ReviewRejected('分段题量或题号不完整，请按计划重新命题。',invalid)
        seen=set();duplicates=[]
        for q in questions:
            key=fingerprint(q)
            if key in seen: duplicates.append(q['id'])
            seen.add(key)
        if duplicates: raise ReviewRejected('整卷存在重复题目，请重新命题。',duplicates)
        if job['mode'] in ('full','compat'):
            sections=blueprint['sections']
            title=(f"{job['level']} AI 模拟试卷 · 第二集题型" if job['mode']=='full'
                   else f"{job['level']} AI 模拟练习 · 兼容专项")
            for t in blueprint['types']:
                if sum(q['type_id']==t['id'] for q in questions)!=t['count']: raise AppError('整卷题型数量不符。')
        else:
            title=f"{job['level']} · {segments[0]['type_title']}专项"
            sections=[dict(id='practice',title=segments[0]['type_title']+'专项',seconds=job['count']*120)]
        paper=validate_paper(dict(title=title,level=job['level'],version='2',sections=sections,questions=questions),
                             {g['id'] for g in self.grammar_items()})
        rng=random.SystemRandom()
        # Balance independently by option count (listening also has 3 choices).
        for option_count in {len(q['options']) for q in questions}:
            subset=[q for q in paper['questions'] if len(q['options'])==option_count and q['skill']!='listening']
            positions=[i%option_count for i in range(len(subset))];rng.shuffle(positions)
            for q,position in zip(subset,positions):
                answer=q['options'][q['answer']]; wrong=[o for i,o in enumerate(q['options']) if i!=q['answer']]
                rng.shuffle(wrong);wrong.insert(position,answer);q.update(options=wrong,answer=position)
        for q,original in zip(paper['questions'],questions):
            q.update(type_id=original['type_id'],type_title=original['type_title'])
        author_models=list(dict.fromkeys(s['author_model'] for s in segments))
        review_models=list(dict.fromkeys(s['review_model'] for s in segments))
        paper.update(id='ai-'+uuid.uuid4().hex,source_type='ai',source='AI 原创模拟题',source_url='',resources=[],
            model=' / '.join(author_models), generation_model=' / '.join(author_models),review_model=' / '.join(review_models),
            configured_model=job['configured_model'],generation_mode=job['mode'],
            blueprint_id=blueprint['id'],blueprint_url=blueprint['source_url'],
            notes=('按官方第二集（2018）题型编排；题目为 AI 原创，经独立审题子智能体复核，未经教师审校。'
                   '听力使用合成语音；图示题采用文字场景。题数及时长不是现行考试公告。'
                   if job['mode']=='full' else
                   '兼容旧版 skill/count 请求的 AI 原创专项练习；按所选题型进行独立审题，未经教师审校。'),
            generation=dict(job_id=job['id'],quality_version=QUALITY_VERSION,recovery_count=job.get('recovery_count',0),retry_count=sum(s.get('retry_count',0) for s in segments),
                segments=[dict({k:s[k] for k in
                ('title','type_id','count','author_model','review_model','configured_model','reviewed_at')},
                retry_count=s.get('retry_count',0),question_models=s.get('question_models',{}),
                question_review_models=s.get('question_review_models',{})) for s in segments]))
        return paper

    def study_delete(self, p):
        identity=p.get('id')
        if not isinstance(identity,str): raise AppError('试卷编号无效。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT data FROM study_papers WHERE id=?',(identity,)).fetchone()
            if not row: raise AppError('找不到可删除的 AI 模拟试卷。')
            paper=json.loads(row[0])
            if paper.get('source_type')!='ai': raise AppError('此操作仅用于删除 AI 生成的模拟试卷。')
            self.db.execute('DELETE FROM study_papers WHERE id=?',(identity,))
        return dict(id=identity,deleted=True,history_preserved=True)
