"""Offline-only fixture for desktop smoke tests, never selected by production."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import bridge
import conversation
import llm
import service

# Do not read the checkout's .env or contact any provider in native smoke tests.
llm.configuration = lambda: dict(base='https://example.invalid/v1', key='', model='', timeout=30)


def offline(*args, **kwargs):
    raise llm.AppError('离线桌面验证：未调用在线模型。')


def stream(task, context, schema, emit, cancelled):
    emit({'type': 'delta', 'text': 'はい、日本語です。'})
    return dict(jp='はい、日本語です。', kana='はい、にほんごです。', romaji='Hai, nihongo desu.',
                zh='是的，是日语。', feedback='离线桌面测试', suggestion='ありがとうございます。',
                pending_task='请继续对话。'), 'windows-offline-fixture'


service.generate = offline
llm.generate = offline
conversation.generate_stream = stream
bridge.main()
