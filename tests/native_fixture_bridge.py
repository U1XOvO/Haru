"""QA-only subprocess bridge. Production Haru never points to this file."""
import sys
import copy
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import bridge
import conversation
import service
from curriculum import SEEDS
from test_study import fixture as study_fixture
from test_study_generation import generation_fixture
from llm import AppError


def stream(task,context,schema,emit,cancelled):
    emit({'type':'delta','text':'はい'})
    for _ in range(100 if context['message']=='停止テスト' else 4):
        if cancelled(): raise AppError('已停止生成，本轮未保存。')
        time.sleep(.05)
    return dict(jp='はい、水です。',kana='はい、みずです。',romaji='Hai, mizu desu.',zh='好的，是水。',
                feedback='原生界面测试数据',suggestion='ありがとうございます。',pending_task='请说明数量。'),'native-fixture'


conversation.generate_stream=stream
original_generate=service.generate
def generate(task,context,schema):
    if context.get('lesson_design'): return copy.deepcopy(SEEDS[context['lesson_no']-1]),'native-lesson-fixture'
    if context.get('review_scope') == 'global' or ('type_id' in context and 'question_plan' in context):
        return generation_fixture(task,context,schema)
    if context.get('level') in ('N5','N4','N3','N2','N1') and 'count' in context:
        return study_fixture(context['level'],context['count'],context['skill']),'native-study-fixture'
    return original_generate(task,context,schema)
service.generate=generate
bridge.main()
