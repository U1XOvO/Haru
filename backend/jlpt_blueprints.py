"""Original-question blueprints based on the 2018 official JLPT workbook.

The workbook's answer sheet is the single source of section/count/option data.
These are publication-specific practice blueprints, not promised current exam
counts. No official question text or answer is sent to the generation model.
"""
import copy
import json
from app_paths import resource_root

from llm import AppError


LEVELS = ('N5', 'N4', 'N3', 'N2', 'N1')
SOURCE_URL = 'https://www.jlpt.jp/samples/sampleindex.html'
GUIDE_URL = 'https://www.jlpt.jp/reference/pdf/guidebook1e.pdf'
NOTES = ('题型、题量与分区依据《日本語能力試験公式問題集》第二集（2018年出版）；'
         '2018是出版年，不是考试年份，也不代表现行考试的固定题数或时长。'
         '生成内容为原创模拟题；图形场景使用明确的文字描述替代，听力使用合成语音。')

# id -> (Chinese title, official Japanese type name, material field, format, rule).
# Material units below describe generation grouping, without copying a source text.
TYPE_DEFINITIONS = {
    'kanji_reading': ('汉字读音', '漢字読み', '', 'choice',
        '在完整日语句子中用【】标明一个汉字词，选择该词在此语境中的假名读音；不得在题干注音中泄露答案。'),
    'orthography': ('词语表记', '表記', '', 'choice',
        '在日语句子中用【】标明假名词，选择对应汉字或片假名表记；上下文必须限定目标词义。'),
    'word_formation': ('词语构成', '語形成', '', 'choice',
        '考查前后缀、派生词或复合词构词，以有明确语境的词内空格出题，不替换为一般语法题。'),
    'context_vocabulary': ('语境词汇', '文脈規定', '', 'choice',
        '在完整句子或简短对话的空格选择符合语境的词。若原题型可用图形，改为完整文字场景；不得写看图却没有图片。'),
    'paraphrase': ('近义替换', '言い換え類義', '', 'choice',
        '用【】标明句中的目标词或表达，选择在该具体上下文中最接近原意的表达；排除可同时成立的近义项。'),
    'word_usage': ('词语用法', '用法', '', 'choice',
        '给出一个目标词，所有选项为包含该词的完整日语句子，只有一项词义、搭配和语域自然。'),
    'grammar_form': ('语法形式判断', '文の文法1（文法形式の判断）', '', 'choice',
        '以完整句子或对话的一个空格考查语法形式，时态、人物关系、肯否等判断依据必须在题干中出现。'),
    'grammar_order': ('句子排序', '文の文法2（文の組み立て）', '', 'ordering',
        '题干必须呈现四个连续待填位置，例如「＿＿ ＿＿ ★ ＿＿」，★只出现一次；四个选项是待排序片段，'
        '每段必须恰好使用一次，答案是放入★的片段。解析给出完整正确句及各片段顺序，不能用普通选词填空替代。'),
    'grammar_text': ('文章文法', '文章の文法', 'passage', 'cloze',
        '同一材料单元的题共享完整连贯文章，正文含与题目一一对应的编号空格；考查篇章衔接、指代、时态和句际逻辑，'
        '每题题干清楚指定空格编号，不能把文章写成互不关联的单句列表。'),
    'reading_short': ('短文理解', '内容理解（短文）', 'passage', 'choice',
        '每题提供独立、完整的短文或通知，问题考查文中事实、原因或作者意思；答案须能从材料推出。'),
    'reading_medium': ('中篇阅读理解', '内容理解（中文）', 'passage', 'choice',
        '同一材料单元共享完整中等长度文章，各题分别考查事实、因果、指代或作者观点，避免反复问同一事实。'),
    'reading_long': ('长文理解', '内容理解（長文）', 'passage', 'choice',
        '同一材料单元共享多段完整长文，题目覆盖局部理解和整体逻辑，推断必须有明确文本依据。'),
    'reading_integrated': ('综合阅读', '統合理解', 'passage', 'choice',
        '材料必须同时包含标明A、B的两篇观点相关的完整文本；题目要求比较、综合异同，不能只读其中一篇便答完全部题。'),
    'reading_argument': ('长文主张理解', '主張理解（長文）', 'passage', 'choice',
        '共享完整论说长文，包含论点、依据及让步或转折；考查作者核心主张、论据和论证关系。'),
    'reading_search': ('信息检索', '情報検索', 'passage', 'choice',
        '提供完整通知、指南、日程或价格资料，使用清晰文字条目表达表格信息；根据题设条件筛选答案，'
        '所有日期、费用、例外及限制必须在材料中出现，不得依赖未提供的图表。'),
    'listening_task': ('听力任务理解', '課題理解', 'audio_text', 'choice',
        '音频稿包含场景、人物对话和问题；考查听后应做的事情、顺序或下一步行动。视觉选项改为具体文字描述。'),
    'listening_point': ('听力要点理解', 'ポイント理解', 'audio_text', 'choice',
        '题干先明确要听取的目标，音频稿提供完整对话及干扰信息，考查原因、条件或关键细节。'),
    'listening_summary': ('听力概要理解', '概要理解', 'audio_text', 'choice',
        '提供完整独白或对话及末尾提问，考查整体话题、目的或说话者意图，不能只凭重复关键词选择答案。'),
    'listening_expression': ('听力情景表达', '発話表現', 'audio_text', 'choice',
        '原型涉及插图，本模拟改为明确的文字情景；音频稿说明人物关系、场景及何と言いますか，'
        '选项为该人物此刻可说的三句日语，只有一句合乎情境与礼貌程度，不得声称展示了插图。'),
    'listening_response': ('听力即时应答', '即時応答', 'audio_text', 'choice',
        '音频稿提供自然的简短提问或发话，三个选项是应答句，考查即时理解与语用；避免常识争议或两个自然回应。'),
    'listening_integrated': ('听力综合理解', '統合理解', 'audio_text', 'choice',
        '音频稿为较长完整对话，包含多个人物意见与多个条件；题目要求综合比较后作决定。'
        '同一材料单元的多个问题必须共享同一音频稿，并分别问不同人物或决策。'),
}

_VOCAB = {
    'N5': ('kanji_reading', 'orthography', 'context_vocabulary', 'paraphrase'),
    'N4': ('kanji_reading', 'orthography', 'context_vocabulary', 'paraphrase', 'word_usage'),
    'N3': ('kanji_reading', 'orthography', 'context_vocabulary', 'paraphrase', 'word_usage'),
    'N2': ('kanji_reading', 'orthography', 'word_formation', 'context_vocabulary', 'paraphrase', 'word_usage'),
    'N1': ('kanji_reading', 'context_vocabulary', 'paraphrase', 'word_usage'),
}
_GRAMMAR = ('grammar_form', 'grammar_order', 'grammar_text')
_READING = {
    'N5': ('reading_short', 'reading_medium', 'reading_search'),
    'N4': ('reading_short', 'reading_medium', 'reading_search'),
    'N3': ('reading_short', 'reading_medium', 'reading_long', 'reading_search'),
    'N2': ('reading_short', 'reading_medium', 'reading_integrated', 'reading_argument', 'reading_search'),
    'N1': ('reading_short', 'reading_medium', 'reading_long', 'reading_integrated', 'reading_argument', 'reading_search'),
}
_LISTENING = {
    'N5': ('listening_task', 'listening_point', 'listening_expression', 'listening_response'),
    'N4': ('listening_task', 'listening_point', 'listening_expression', 'listening_response'),
    'N3': ('listening_task', 'listening_point', 'listening_summary', 'listening_expression', 'listening_response'),
    'N2': ('listening_task', 'listening_point', 'listening_summary', 'listening_response', 'listening_integrated'),
    'N1': ('listening_task', 'listening_point', 'listening_summary', 'listening_response', 'listening_integrated'),
}
_READING_LENGTHS = {
    'N5': {'reading_short': '约80字', 'reading_medium': '约250字', 'reading_search': '约250字'},
    'N4': {'reading_short': '约100至200字', 'reading_medium': '约450字', 'reading_search': '约400字'},
    'N3': {'reading_short': '约150至200字', 'reading_medium': '约350字', 'reading_long': '约550字', 'reading_search': '约600字'},
    'N2': {'reading_short': '约200字', 'reading_medium': '约500字', 'reading_integrated': '两篇合计约600字',
           'reading_argument': '约900字', 'reading_search': '约700字'},
    'N1': {'reading_short': '约200字', 'reading_medium': '约500字', 'reading_long': '约1000字',
           'reading_integrated': '两篇合计约600字', 'reading_argument': '约1000字', 'reading_search': '约700字'},
}


def _units(level, type_id, count):
    """Keep related questions together; units may exceed a worker's batch target."""
    if type_id == 'reading_medium' and level in ('N1', 'N2', 'N3'):
        return [3] * (count // 3)
    if type_id == 'listening_integrated':
        return [1, 1, 2]
    if type_id == 'grammar_text' or type_id in (
            'reading_medium', 'reading_long', 'reading_integrated', 'reading_argument', 'reading_search'):
        return [count]
    return [1] * count


def get_blueprint(level, papers=None):
    """Return an independent JSON-ready generation plan for one JLPT level.

    ``types[].options_counts`` and ``locators`` align positionally with the source
    answer sheet. ``units`` contains lengths of inseparable generation material
    groups; it must be respected when batching. Callers must not use source IDs or
    locators to imply that generated questions are official questions.
    """
    if level not in LEVELS:
        raise AppError('请选择 N5 至 N1 的等级。')
    if papers is None:
        path = resource_root() / 'data' / 'study' / 'papers.json'
        papers = json.loads(path.read_text(encoding='utf-8'))
    source_id = 'official-2018-' + level
    paper = next((p for p in papers if p.get('id') == source_id), None)
    if paper is None:
        raise AppError('找不到该等级的官方第二集题量模板。')
    groups = {}
    for q in paper['questions']:
        groups.setdefault((q['skill'], q['group']), []).append(q)
    expected = []
    offset = 0
    for skill, names in (('vocabulary', _VOCAB[level]), ('grammar', _GRAMMAR),
                         ('reading', _READING[level]), ('listening', _LISTENING[level])):
        if skill == 'listening' or (skill == 'grammar' and level in ('N5', 'N4', 'N3')):
            offset = 0
        for i, type_id in enumerate(names, offset + 1):
            expected.append((skill, '問題' + str(i), type_id))
        offset += len(names)
    if set(groups) != {(skill, group) for skill, group, _ in expected}:
        raise AppError('官方题量模板的题型分组与蓝图不一致，请检查内容版本。')
    types = []
    for skill, group, type_id in expected:
        qs = groups[(skill, group)]
        name, name_ja, material, fmt, rules = TYPE_DEFINITIONS[type_id]
        sections = {q['section'] for q in qs}
        if len(sections) != 1:
            raise AppError('官方题型跨越多个分区，无法生成。')
        counts = [len(q['options']) for q in qs]
        units = _units(level, type_id, len(qs))
        if sum(units) != len(qs) or any(n < 1 for n in units):
            raise AppError('官方题量与共享材料单元不一致，请检查内容版本。')
        if type_id in _READING_LENGTHS[level]:
            rules += ' 每个材料单元的日语正文长度参考：' + _READING_LENGTHS[level][type_id] + '（长度为目标，不含选项与解析）。'
        locators = [q['locator'] for q in qs]
        types.append(dict(id=type_id, name=name, title=name, name_ja=name_ja, skill=skill,
            section=qs[0]['section'], group=group, count=len(qs),
            options_count=counts[0] if len(set(counts)) == 1 else list(counts), options_counts=counts,
            locator_template=locators[0].rsplit(' · ', 1)[0] + ' · {number}', locators=locators,
            source_question_ids=[q['id'] for q in qs], material=material, format=fmt,
            units=units, requirements=rules))
    return dict(id='jlpt-2018-' + level, level=level, title=level + ' 官方第二集题型蓝图',
        version=paper.get('version', '2018.1'), publication_year=2018, source_paper_id=source_id,
        source_url=SOURCE_URL, type_reference_url=GUIDE_URL, notes=NOTES, note=NOTES,
        sections=copy.deepcopy(paper['sections']), count=sum(t['count'] for t in types), types=types)


def get_blueprints():
    """Return all level templates, reading the local source only once."""
    path = resource_root() / 'data' / 'study' / 'papers.json'
    papers = json.loads(path.read_text(encoding='utf-8'))
    return [get_blueprint(level, papers) for level in LEVELS]
