"""Local Google speech settings. API keys never cross the read-only UI boundary."""
import json
import os
from pathlib import Path
import re
import tempfile

import portalocker

from app_paths import storage_root
from llm import AppError


FILENAME = '.speech-settings.json'
MODEL = 'gemini-3.8-flash-lite-tts'
PRESETS = (
    {
        'id': 'girl', 'name': '可爱年轻女声', 'gender': 'female',
        'description': 'A Japanese woman in her early twenties with a bright, light, sweet voice, natural standard Japanese pronunciation and a friendly, youthful tone.',
        'theme': '春日野餐',
        'sample': '今日は友だちと公園へお花見に行きます。朝からわくわくして、いつもより早く起きました。お弁当には小さなおにぎりと卵焼き、それから甘いイチゴも入れました。公園に着くと、桜の花びらが風に乗って、ゆっくり空を舞っていました。友だちは「ここで写真を撮ろうよ」と言って、みんなで大きな桜の木の下に集まりました。お昼になったら、お弁当を広げて、一緒に「いただきます」。帰り道には、今日いちばんきれいだった景色について話しながら歩きました。また来年も、同じ場所で春を迎えたいです。',
    },
    {
        'id': 'student', 'name': '大学女生', 'gender': 'female',
        'description': 'A Japanese university-age woman with a clear, warm, relaxed voice and natural standard Japanese pronunciation, like a friendly classmate.',
        'theme': '校园一天',
        'sample': '今日は朝から大学の図書館でレポートを書いています。窓の近くの席を見つけたので、柔らかい光が机の上に入ってきます。午前中に資料を三つ読み、気になったところをノートにまとめました。お昼には友だちと学食でカレーを食べて、次の授業の話をしました。「午後の発表、少し緊張するね」と言ったら、友だちが笑って「大丈夫、一緒に練習しよう」と答えてくれました。授業が終わったら、駅前の小さなカフェに寄るつもりです。温かい飲み物を飲みながら、今日学んだことをもう一度ゆっくり振り返ります。',
    },
    {
        'id': 'boy', 'name': '清爽年轻男声', 'gender': 'male',
        'description': 'A Japanese man in his early twenties with a youthful, clear, energetic voice, natural standard Japanese pronunciation and an easygoing, friendly tone.',
        'theme': '周末出游',
        'sample': '今週の土曜日は、友だちと電車に乗って海の近くの町へ行きます。駅で待ち合わせる時間は朝の九時です。僕は少し早めに着いて、切符を買っておこうと思います。電車の窓から山や川が見えるので、移動する時間も楽しみです。町に着いたら、まず港まで歩いて、船を見たり、写真を撮ったりします。そのあとで近くの公園へ行き、みんなで軽くサッカーをする予定です。「お昼は何を食べようか」と聞くと、友だちは「地元のおいしいものを探そう」と言いました。夕方の電車に乗る前に、お土産も一つ選びたいです。',
    },
)
PACE = {'slow': 'speak slowly', 'slightly_slow': 'speak a little slowly',
        'normal': 'speak at a natural pace', 'slightly_fast': 'speak a little briskly',
        'fast': 'speak briskly'}
MOOD = {'calm': 'with a calm, reassuring tone', 'gentle': 'with a gentle, friendly tone',
        'lively': 'with a lively, cheerful tone'}
CLARITY = {'natural': 'with natural Japanese pronunciation',
           'learning': 'articulate Japanese clearly for a language learner, with clear pauses between sentences'}


def config_path(root=None):
    return Path(root) / FILENAME if root is not None else storage_root() / FILENAME


def defaults():
    return {'version': 1, 'revision': 0, 'engine': 'edge', 'key': '',
            'selected': 'girl', 'pace': 'normal', 'mood': 'gentle',
            'clarity': 'learning', 'style': '', 'timeout': 90, 'retries': 0,
            'voices': [{**item, 'voice_id': ''} for item in PRESETS]}


def _text(value, label, limit, *, required=False, multiline=False):
    if (not isinstance(value, str) or len(value) > limit or
            any(ord(ch) < 32 and (not multiline or ch not in '\n\t') for ch in value)):
        raise AppError(f'{label}格式无效。')
    value = value.strip()
    if required and not value:
        raise AppError(f'请填写{label}。')
    return value


def _voice(raw, previous=None):
    if not isinstance(raw, dict):
        raise AppError('音色配置无效。')
    identity = _text(raw.get('id'), '音色标识', 64, required=True)
    if not re.fullmatch(r'[a-z0-9_-]{1,64}', identity):
        raise AppError('音色标识无效。')
    preset = next((p for p in PRESETS if p['id'] == identity), None)
    if preset:
        # Built-in persona definitions stay stable across app updates and UI edits.
        base = dict(preset)
        base['voice_id'] = (previous or {}).get('voice_id', '')
        return base
    name = _text(raw.get('name'), '音色名称', 40, required=True)
    description = _text(raw.get('description'), '声音描述', 500, required=True, multiline=True)
    theme = _text(raw.get('theme', ''), '试音主题', 60)
    sample = _text(raw.get('sample', ''), '试音文本', 1200, required=True, multiline=True)
    gender = raw.get('gender')
    if gender not in ('female', 'male', 'neutral'):
        raise AppError('请选择有效的音色类型。')
    voice_id = (previous or {}).get('voice_id', '')
    if previous and (previous['description'] != description or previous['gender'] != gender):
        voice_id = ''
    return dict(id=identity, name=name, gender=gender, description=description,
                theme=theme, sample=sample, voice_id=voice_id)


def validate(raw, previous=None):
    if not isinstance(raw, dict):
        raise AppError('朗读配置格式无效。')
    old = {voice['id']: voice for voice in (previous or {}).get('voices', [])}
    engine = raw.get('engine', 'edge')
    if engine not in ('edge', 'gemini'):
        raise AppError('朗读引擎无效。')
    key = _text(raw.get('key', ''), 'Google API Key', 4096)
    if raw.get('clear_key') is True:
        key = ''
    elif not key:
        key = (previous or {}).get('key', '')
    options = {}
    for field, values in (('pace', PACE), ('mood', MOOD), ('clarity', CLARITY)):
        value = raw.get(field, defaults()[field])
        if value not in values:
            raise AppError('朗读参数无效。')
        options[field] = value
    timeout, retries = raw.get('timeout', 90), raw.get('retries', 0)
    if type(timeout) is not int or not 10 <= timeout <= 90:
        raise AppError('Google 朗读超时须为 10–90 秒。')
    if type(retries) is not int or not 0 <= retries <= 1:
        raise AppError('Google 朗读重试次数须为 0 或 1。')
    voices = raw.get('voices')
    if not isinstance(voices, list) or not 3 <= len(voices) <= 20:
        raise AppError('请保留三个预制音色，最多添加 17 个自定义音色。')
    ids = [v.get('id') if isinstance(v, dict) else None for v in voices]
    if not all(isinstance(identity, str) for identity in ids):
        raise AppError('音色标识无效。')
    if len(set(ids)) != len(ids) or not {p['id'] for p in PRESETS} <= set(ids):
        raise AppError('音色标识重复或缺少预制音色。')
    normalized = [_voice(item, old.get(item['id'])) for item in voices]
    selected = raw.get('selected')
    if selected not in ids:
        raise AppError('请选择有效的朗读音色。')
    return dict(version=1, revision=(previous or {}).get('revision', 0) + 1,
                engine=engine, key=key, selected=selected, voices=normalized,
                style=_text(raw.get('style', ''), '朗读方式', 200, multiline=True),
                timeout=timeout, retries=retries, **options)


def read_settings(root=None):
    path = config_path(root)
    if path.is_symlink():
        raise AppError('朗读配置文件无效，请检查本机配置。')
    if not path.exists():
        return defaults()
    try:
        if path.stat().st_size > 40_000:
            raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('version') != 1
                or type(data.get('revision')) is not int or data['revision'] < 0):
            raise ValueError()
        # Validate stored data without rewriting it or revealing its key.
        checked = validate({**data, 'key': data.get('key', '')}, {'revision': data['revision'] - 1})
        checked['revision'] = data['revision']
        for voice, stored in zip(checked['voices'], data['voices']):
            value = stored.get('voice_id', '')
            if not isinstance(value, str) or (value and not re.fullmatch(r'voice_[A-Za-z0-9_-]{1,180}', value)):
                raise ValueError()
            voice['voice_id'] = value if (stored.get('description') == voice['description']
                                           and stored.get('gender') == voice['gender']) else ''
        return checked
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AppError) as error:
        raise AppError('朗读配置文件无法读取，原文件已保留。') from error


def editable_settings(root=None):
    data = read_settings(root)
    return {key: value for key, value in data.items() if key != 'key'} | {'key_configured': bool(data['key'])}


def _atomic_save(root, data):
    root.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='.speech-settings.',
                                         dir=root, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(root / FILENAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_settings(params, root=None):
    folder = Path(root) if root is not None else storage_root()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(folder / '.speech-settings.lock', mode='a', timeout=10):
            previous = read_settings(folder)
            if not isinstance(params, dict) or params.get('revision') != previous['revision']:
                raise AppError('朗读配置已更新，请重新读取后再保存。')
            data = validate(params, previous)
            _atomic_save(folder, data)
        return editable_settings(folder)
    except (OSError, portalocker.exceptions.LockException) as error:
        raise AppError('无法保存朗读配置，请检查本地文件权限。') from error


def style_for(settings):
    return ', '.join((PACE[settings['pace']], MOOD[settings['mood']],
                      CLARITY[settings['clarity']], settings['style'])).strip(', ')
