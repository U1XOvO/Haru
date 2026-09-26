"""Request-scoped progress and safe, incomplete-JSON teaching previews."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import time

_sink = ContextVar('generation_sink', default=None)
_action = ContextVar('generation_action', default='')
PREVIEW_ACTIONS = frozenset(('lesson', 'daily_word', 'card_create', 'card_random',
                             'decode', 'immersion', 'annotate', 'dictionary', 'quiz'))
FIELDS = frozenset(('title', 'goal', 'word', 'reading', 'romaji', 'meaning', 'example',
                    'translation', 'mnemonic', 'structure', 'pitfall', 'grammar', 'tip',
                    'objectives', 'sections', 'vocabulary', 'examples', 'sentences',
                    'cards', 'parts', 'tokens', 'words', 'task'))
# Whitelist nested fields too: no answer keys, hidden reasoning or quiz explanations.
LEAVES = FIELDS | frozenset(('jp', 'kana', 'zh', 'text', 'role', 'explanation',
                            'surface', 'lemma', 'pos'))


@contextmanager
def progress_scope(action, sink):
    a, s = _action.set(action), _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(s); _action.reset(a)


def emit(event):
    sink = _sink.get()
    if sink is not None:
        sink(event)


def streaming_text():
    return _sink.get() is not None and _action.get() in PREVIEW_ACTIONS


def has_progress():
    return _sink.get() is not None


def clean(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, str):
        return value[:12000]
    if isinstance(value, list):
        return [clean(item, depth + 1) for item in value[:300]]
    if isinstance(value, dict):
        return {k: clean(v, depth + 1) for k, v in value.items() if k in LEAVES}
    return None


def preview_fields(raw):
    """Decode only complete values/items; never guess closing quotes or brackets."""
    raw = raw.lstrip()
    if raw.startswith('```'):
        raw = raw.partition('\n')[2].lstrip()
    if not raw.startswith('{'):
        return {}
    decoder, result, pos = json.JSONDecoder(), {}, 1
    try:
        while True:
            while pos < len(raw) and raw[pos].isspace(): pos += 1
            key, pos = decoder.raw_decode(raw, pos)
            if not isinstance(key, str): break
            while pos < len(raw) and raw[pos].isspace(): pos += 1
            if raw[pos] != ':': break
            pos += 1
            while pos < len(raw) and raw[pos].isspace(): pos += 1
            if raw[pos] == '[':
                items = []; pos += 1
                while True:
                    while pos < len(raw) and raw[pos].isspace(): pos += 1
                    if raw[pos] == ']': pos += 1; break
                    item, pos = decoder.raw_decode(raw, pos)
                    items.append(item)
                    if key in FIELDS: result[key] = clean(items)
                    while pos < len(raw) and raw[pos].isspace(): pos += 1
                    if raw[pos] == ']': pos += 1; break
                    if raw[pos] != ',': return result
                    pos += 1
                value = items
            else:
                value, pos = decoder.raw_decode(raw, pos)
            if key in FIELDS: result[key] = clean(value)
            while pos < len(raw) and raw[pos].isspace(): pos += 1
            if raw[pos] != ',': break
            pos += 1
    except (ValueError, IndexError, RecursionError):
        pass
    return result


class Preview:
    def __init__(self):
        self.last = None
        self.when = 0
        emit({'event': 'generation', 'phase': 'start', 'preview': {}})

    def update(self, raw, *, final=False):
        now = time.monotonic()
        if not final and now - self.when < .15:
            return
        self.when = now
        value = preview_fields(raw)
        if value and value != self.last:
            self.last = value
            emit({'event': 'generation', 'phase': 'content', 'preview': value})
