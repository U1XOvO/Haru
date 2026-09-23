"""Validate and persist local OpenAI-compatible provider profiles."""
import json
import math
import os
import re
import tempfile
import urllib.parse
from pathlib import Path

import portalocker

from app_paths import storage_root


class AppError(Exception):
    pass


TEXT_FIELDS = {'id': 80, 'name': 80, 'base': 2048, 'model': 200, 'key': 4096}
NUMBER_FIELDS = {
    'timeout': (10, 90, 60, False),
    'retries': (0, 2, 1, True),
    'temperature': (0, 2, None, False),
    'top_p': (0, 1, None, False),
    'max_tokens': (1, 131072, None, True),
}


def config_root():
    return storage_root()


def profile_defaults():
    return {
        'reasoning': 'omit', 'reasoning_custom': '', 'temperature': None,
        'top_p': None, 'max_tokens': None, 'omit_temperature': False,
        'omit_token_limit': False, 'retries': 1,
    }


def validate_base(base):
    try:
        parsed = urllib.parse.urlsplit(base)
        valid = (
            parsed.scheme == 'https' and parsed.hostname
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and not any(char.isspace() or ord(char) < 32 for char in base)
        )
        parsed.port
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise AppError('API 地址必须为不含用户名、密码和查询参数的 HTTPS 地址。')
    return parsed


def _text(raw, field, limit):
    value = raw.get(field, '')
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise AppError('服务商字段格式无效，请使用单行文本。')
    return value.strip()


def normalize_profile(raw, previous=None):
    if not isinstance(raw, dict):
        raise AppError('服务商配置格式无效。')
    profile = profile_defaults() | {
        field: _text(raw, field, limit) for field, limit in TEXT_FIELDS.items()
    }
    custom = _text(raw, 'reasoning_custom', 80)
    profile['reasoning_custom'] = custom
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', profile['id']):
        raise AppError('服务商标识无效。')
    if not profile['name'] or not profile['model']:
        raise AppError('请填写服务商名称和模型 ID。')
    validate_base(profile['base'])
    profile['base'] = profile['base'].rstrip('/')

    if not profile['key'] and previous:
        if previous['base'].rstrip('/') != profile['base']:
            raise AppError('API 地址已改变，请重新填写该服务商的密钥。')
        profile['key'] = previous['key']
    if not profile['key']:
        raise AppError('请填写该服务商的 API 密钥。')

    reasoning = raw.get('reasoning', 'omit')
    if reasoning == 'auto':
        reasoning = 'omit'
    if reasoning not in {'omit', 'off', 'low', 'medium', 'high', 'max', 'custom'}:
        raise AppError('模型参数选项无效。')
    if reasoning == 'custom' and not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', custom):
        raise AppError('请填写由字母、数字、点、下划线或连字符组成的自定义思考强度。')
    profile['reasoning'] = reasoning

    for field, (low, high, default, integral) in NUMBER_FIELDS.items():
        value = raw.get(field, default)
        if value is None and default is None:
            profile[field] = None
            continue
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not low <= value <= high
                or (integral and int(value) != value)):
            suffix = '，且为整数。' if integral else '。'
            raise AppError(f'{field} 应在 {low}–{high} 之间{suffix}')
        profile[field] = int(value) if integral else value

    for field in ('omit_temperature', 'omit_token_limit'):
        if type(raw.get(field, False)) is not bool:
            raise AppError('参数开关格式无效。')
        profile[field] = raw.get(field, False)
    return profile


def read_profiles(root=None):
    path = (Path(root) if root is not None else config_root()) / '.llm-providers.json'
    if path.is_symlink():
        raise AppError('无法读取 AI 服务商配置，请检查本机配置文件。')
    if not path.exists():
        provider = profile_defaults() | {
            'id': 'default', 'name': '默认配置', 'base': 'https://api.openai.com/v1',
            'key': '', 'model': '', 'timeout': 60,
        }
        return {'version': 1, 'revision': 0, 'active': 'default', 'providers': [provider]}
    try:
        if path.stat().st_size > 200_000:
            raise ValueError
        raw = json.loads(path.read_text(encoding='utf-8'))
        revision = raw.get('revision')
        rows = raw.get('providers')
        if (raw.get('version') != 1 or type(revision) is not int or revision < 0
                or not isinstance(rows, list) or not 1 <= len(rows) <= 20):
            raise ValueError
        providers = [normalize_profile(row) for row in rows]
        ids = [profile['id'] for profile in providers]
        if len(set(ids)) != len(ids) or raw.get('active') not in ids:
            raise ValueError
        return {
            'version': 1, 'revision': revision,
            'active': raw['active'], 'providers': providers,
        }
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, AppError):
        raise AppError('无法读取 AI 服务商配置，请检查本机配置文件。') from None


def configuration():
    data = read_profiles()
    return next(profile for profile in data['providers'] if profile['id'] == data['active'])


def editable_config():
    data = read_profiles()
    providers = [
        {key: value for key, value in profile.items() if key != 'key'}
        | {'key_configured': bool(profile['key'])}
        for profile in data['providers']
    ]
    selected = next(profile for profile in providers if profile['id'] == data['active'])
    return selected | {
        'providers': providers,
        'active': data['active'],
        'revision': data['revision'],
    }


def save_config(params):
    if not isinstance(params, dict):
        raise AppError('AI 配置格式无效。')
    folder = config_root()
    temporary = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(folder / '.llm-providers.lock', mode='a', timeout=15):
            previous = read_profiles()
            if params.get('revision') != previous['revision']:
                raise AppError('配置已更新，请刷新后重新编辑。')
            rows = params.get('providers')
            if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
                raise AppError('请保留 1–20 个服务商配置。')
            old = {profile['id']: profile for profile in previous['providers']}
            providers = [
                normalize_profile(row, old.get(row.get('id')) if isinstance(row, dict) else None)
                for row in rows
            ]
            ids = [profile['id'] for profile in providers]
            if len(set(ids)) != len(ids) or params.get('active') not in ids:
                raise AppError('请选择有效的默认服务商，且服务商标识不可重复。')
            data = {
                'version': 1,
                'revision': previous['revision'] + 1,
                'active': params['active'],
                'providers': providers,
            }
            with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', prefix='.llm-providers.',
                    dir=folder, delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o600)
            temporary.replace(folder / '.llm-providers.json')
    except (OSError, UnicodeError, portalocker.exceptions.LockException):
        raise AppError('无法保存 AI 配置，请检查文件权限后重试。') from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return editable_config()
