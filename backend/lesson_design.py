"""Versioned daily-lesson contract; legacy saved courses remain readable."""
import re
import unicodedata

from llm import AppError

VERSION = 2
EXAMPLE = dict(jp='日语', kana='完整假名读音', romaji='Hepburn罗马音', zh='中文')
LESSON = dict(
    title='课程标题', goal='本课综合Can-do目标',
    objectives=['可观察、可完成的学习目标，3项'],
    grammar='核心规则概览', tip='中文母语者易错点及纠正方法',
    sections=[dict(title='讲解小节标题', explanation='规则、适用情境、限制与中日对比，至少80字')],
    vocabulary=[dict(word='词语', reading='假名', romaji='罗马音', meaning='本课词义')],
    examples=[EXAMPLE],
    dialogue=dict(scene='人物关系、地点与交际目的', lines=[dict(EXAMPLE, speaker='角色')]),
    speaking='从分块跟读到脱稿表达的步骤',
    production=[dict(situation='新的交际情境', task='需要自己组织表达的任务',
                     checklist=['可自行核对的完成标准'], sample=EXAMPLE)],
    listening=EXAMPLE,
    questions=[dict(prompt='中文任务说明；新词附读音和意思', options=['选项'], answer=0,
                    explanation='答案理由、干扰项为什么不合适、对应知识点', skill='grammar/kana/listening',
                    task_type='recognition/application/correction/comprehension/transfer',
                    audio='听力题独立日语材料，其余为空')])

RULES = '''创建一节内容充实、循序渐进的日语每日课程。严格遵守 lesson_design 中的数量与范围。
教学：3个可观察的目标；3–4个讲解小节，每节至少80字，总计至少300字，分别讲形式与意义、适用情境与边界、易错对比，不能重复凑字数。
6–10个带读音和词义的词汇；6–8个不同例句，展示不同人物、对象、语用或正反对比；一段4–8轮连贯对话，角色清楚且有交际目的。
提供分块跟读步骤，以及2个自主表达任务，每个有新情境、明确产出要求、2–4条自查标准及独立参考答案。参考答案只能放在sample。
听读示范与测验材料分开。8道单选题，每题3–4个不重复选项、唯一正确答案；至少2道听力，全部题目至少覆盖4种task_type。
至少4题为application或transfer；最多2题纯recognition。其余结合纠错、阅读理解、语用判断、回应选择、句序重组等任务。
考查已教知识，但更换交际目的、人物关系、信息组合或表达形式，不能只改名字/数字或将例句挖空。
听力原文不得重复例句、对话、自主表达答案或听读示范；两题材料也须不同。题干不得出现听力原文、翻译或答案提示。
问候等固定短语可以复用，但必须进入新情境或新的信息组合；不能为避重复而引入未教语法。必要的新词在题干给注释，不用生词难倒学生。
每题解析解释正确答案、干扰项及可迁移的判断依据；题型标签不能冒充真实任务设计。skill只能是grammar、kana、listening，词汇归grammar。
先自查教学覆盖、唯一答案、题材变化、材料重复和数量，再返回JSON；不输出测验以外的答案表。
自然复现review_targets中适合本课的词语，不改变阶段和主题。按照每日时长建议分段学习，不因时长短而删掉课程模块。
'''


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def validate_design(obj):
    # Imported lazily because Service loads the shared schema at module import.
    from service import fields, text, validate_examples

    def items(key, low, high):
        value = obj.get(key)
        if not isinstance(value, list) or not low <= len(value) <= high:
            raise AppError(f'新版课程的 {key} 应包含{low}–{high}项，未保存。')
        return value

    for x in items('objectives', 3, 3): text(x, '学习目标', 300)
    sections = items('sections', 3, 4)
    for x in sections:
        fields(x, ['title', 'explanation'])
        if len(x['explanation'].strip()) < 80: raise AppError('课程讲解小节过短，未保存。')
    if sum(len(x['explanation'].strip()) for x in sections) < 300:
        raise AppError('课程讲解应至少300字，未保存。')
    vocab = items('vocabulary', 6, 10)
    for x in vocab: fields(x, ['word', 'reading', 'romaji', 'meaning'])
    if len({normalized(x['word']) for x in vocab}) != len(vocab):
        raise AppError('课程词汇重复，未保存。')
    examples = items('examples', 6, 8)
    if len({normalized(x['jp']) for x in examples}) != len(examples):
        raise AppError('课程例句重复，未保存。')
    dialogue = fields(obj.get('dialogue'), ['scene'])
    lines = dialogue.get('lines')
    if not isinstance(lines, list) or not 4 <= len(lines) <= 8:
        raise AppError('课程对话应包含4–8轮，未保存。')
    validate_examples(lines)
    for x in lines: fields(x, ['speaker'])
    for x in items('production', 2, 2):
        fields(x, ['situation', 'task'])
        fields(x.get('sample'), EXAMPLE)
        checks = x.get('checklist')
        if not isinstance(checks, list) or not 2 <= len(checks) <= 4:
            raise AppError('自主表达任务需要2–4项自查标准，未保存。')
        for check in checks: text(check, '自查标准', 500)
    questions = items('questions', 8, 8)
    kinds = {'recognition', 'application', 'correction', 'comprehension', 'transfer'}
    for q in questions:
        if q.get('task_type') not in kinds: raise AppError('课程练习任务类型无效，未保存。')
        if not 3 <= len(q['options']) <= 4: raise AppError('课程练习需要3–4个选项，未保存。')
        if len({normalized(o) for o in q['options']}) != len(q['options']):
            raise AppError('课程练习选项仅标点或空白不同，未保存。')
    if len({q['task_type'] for q in questions}) < 4 or sum(q['task_type'] in ('application', 'transfer') for q in questions) < 4:
        raise AppError('课程练习需要至少4种任务，至少4道应用或迁移题，未保存。')
    if sum(q['task_type'] == 'recognition' for q in questions) > 2:
        raise AppError('课程直接识记题过多，未保存。')
    if len({normalized(q['prompt']) for q in questions}) != len(questions):
        raise AppError('课程练习题干重复，未保存。')
    teaching = [x['jp'] for x in examples + lines] + [obj['listening']['jp']] + [x['sample']['jp'] for x in obj['production']]
    material = {normalized(x) for x in teaching}
    # Also catch a single teaching sentence copied from a longer dialogue turn.
    material.update(normalized(s) for x in teaching for s in re.split(r'[。！？!?\n]', x) if s.strip())
    audio_seen = set()
    for q in questions:
        if q['skill'] == 'listening':
            audio = normalized(q['audio'])
            if audio in material or audio in audio_seen:
                raise AppError('听力测验复用了教学材料或其他题原文，未保存。')
            if audio and audio in normalized(q['prompt']):
                raise AppError('听力题干泄露了原文，未保存。')
            audio_seen.add(audio)
        # Fixed greetings may be reused, but not whole model sentences as cloze tests.
        if re.search(r'（\s*）|\(\s*\)|＿+|_{2,}|［\s*］|\[\s*\]', q['prompt']):
            filled = re.sub(r'（\s*）|\(\s*\)|＿+|_{2,}|［\s*］|\[\s*\]', lambda _: q['options'][q['answer']], q['prompt'])
            if any(len(s) >= 8 and s in normalized(filled) for s in material):
                raise AppError('练习直接将教学例句挖空，未保存。')
    if len(audio_seen) < 2: raise AppError('课程应有至少2道独立听力题，未保存。')
    return obj
