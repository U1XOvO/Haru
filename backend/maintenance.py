"""Local backups and recoverable migration. Never uploads or logs learner data."""
import json
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

import portalocker

from app_paths import storage_root
from llm import AppError

SCHEMA_VERSION = 3
MANAGED = ('.env', 'runtime/haru.sqlite3', 'runtime/study-assets',
           'runtime/exports', 'runtime/backups', 'runtime/speaking-latest.wav',
           'runtime/speaking-latest.m4a')


def atomic_json(path, value):
    pending = path.with_suffix('.pending')
    with pending.open('w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(path)


def lock(root, *, shared=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    flags = (portalocker.LOCK_SH if shared else portalocker.LOCK_EX) | portalocker.LOCK_NB
    return portalocker.Lock(root / '.maintenance.lock', mode='a', timeout=15, flags=flags)


def snapshot(source, destination):
    """SQLite online backup includes committed WAL records; bounded busy retries."""
    started = time.monotonic()
    def progress(status, remaining, total):
        if time.monotonic() - started > 30:
            raise AppError('数据库仍被占用，请退出旧版 Haru 后重试。')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as src:
        version = src.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise AppError('学习数据来自更新版本，请升级 Haru 后再打开，不能降级覆盖。')
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise AppError('数据库完整性检查失败，原数据未修改。')


def backup(root, data_dir=None):
    root = Path(root)
    data = Path(data_dir) if data_dir else root / 'runtime'
    destination = root / 'upgrade-backups' / (time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True)
    if (data / 'haru.sqlite3').is_file():
        snapshot(data / 'haru.sqlite3', destination / 'haru.sqlite3')
    if (root / '.env').is_file():
        shutil.copyfile(root / '.env', destination / '.env')
        (destination / '.env').chmod(0o600)
    atomic_json(destination / 'backup.json', {'schema': SCHEMA_VERSION, 'complete': True})
    return destination


def recover(root):
    """Rollback an interrupted import before the next backend request can write."""
    root = Path(root)
    journal = root / '.migration.json'
    if not journal.exists():
        return
    record = json.loads(journal.read_text(encoding='utf-8'))
    identity = record['id']
    if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
        raise AppError('迁移记录无效，请保留数据目录并联系维护者。')
    staging = root / 'migration-backups' / identity
    for item in reversed(record['items']):
        name = item['name']
        if name not in MANAGED:
            raise AppError('迁移记录包含无效路径。')
        destination, old, new = root / name, staging / 'old' / name, staging / 'new' / name
        restore = old.exists()
        if restore or (not item['existed'] and not new.exists()):
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink(missing_ok=True)
        if restore:
            destination.parent.mkdir(parents=True, exist_ok=True)
            old.replace(destination)
    journal.unlink()


def _empty_target(root):
    if (root / '.env').is_file() and (root / '.env').read_bytes().strip():
        raise AppError('当前安装已有 AI 配置。为避免覆盖，请在首次使用的空白安装中导入。')
    for name in MANAGED[2:]:
        path = root / name
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise AppError('当前安装已有学习文件，导入不会覆盖它们。')
    db = root / 'runtime/haru.sqlite3'
    if db.is_file():
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                if table == 'kv':
                    for key, value in connection.execute('SELECT key,value FROM kv'):
                        if key == 'profile':
                            profile = json.loads(value)
                            if any(profile.get(k) != v for k, v in {'name':'学习者', 'minutes':20, 'time':'20:30', 'goal':'日常交流', 'romaji':True}.items()):
                                raise AppError('当前安装已有个人偏好，导入不会覆盖它们。')
                        elif key != 'study_schema':
                            raise AppError('当前安装已有学习记录，导入不会覆盖它们。')
                elif not table.startswith('sqlite_'):
                    quoted = table.replace('"', '""')
                    if connection.execute(f'SELECT 1 FROM "{quoted}" LIMIT 1').fetchone():
                        raise AppError('当前安装已有学习记录，导入不会覆盖它们。')


def import_legacy(source, root):
    source, root = Path(source).resolve(), Path(root).resolve()
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise AppError('请选择另一处旧版仓库，不能导入当前数据目录。')
    if not (source / 'runtime/haru.sqlite3').is_file():
        raise AppError('所选目录没有 runtime/haru.sqlite3，请选择旧版 Haru 仓库根目录。')
    recover(root)
    _empty_target(root)
    identity = uuid.uuid4().hex
    stage = root / 'migration-backups' / identity
    items = []
    for name in MANAGED:
        src = source / name
        if not src.exists():
            continue
        # Resolve only after checking components: do not copy data outside the selected tree.
        if any(p.is_symlink() for p in (src, *src.parents)) or any(p.is_symlink() for p in src.rglob('*')):
            raise AppError('旧版数据包含符号链接，请先整理为普通文件后导入。')
        dest = stage / 'new' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name == 'runtime/haru.sqlite3':
            snapshot(src, dest)
        elif src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copyfile(src, dest)
            if name == '.env':
                dest.chmod(0o600)
        items.append({'name': name, 'existed': (root / name).exists()})
    # Do not let stale WAL files from the empty target modify the imported snapshot.
    db = root / 'runtime/haru.sqlite3'
    if db.exists():
        with closing(sqlite3.connect(db)) as connection:
            if connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                raise AppError('请关闭其他 Haru 窗口后重试导入。')
        for suffix in ('-wal', '-shm'):
            Path(str(db) + suffix).unlink(missing_ok=True)
    atomic_json(root / '.migration.json', {'id': identity, 'items': items})
    try:
        for item in items:
            name = item['name']
            target = root / name
            if item['existed']:
                old = stage / 'old' / name
                old.parent.mkdir(parents=True, exist_ok=True)
                target.replace(old)
            target.parent.mkdir(parents=True, exist_ok=True)
            (stage / 'new' / name).replace(target)
        (root / '.migration.json').unlink()
    except Exception:
        recover(root)
        raise
    return {'imported': True, 'restart_required': True}


def dispatch(action, params):
    root = storage_root()
    with lock(root):
        recover(root)
        if action == 'import_legacy':
            return import_legacy(params['source'], root)
        if action == 'prepare_update':
            backup(root, os.environ.get('HARU_DATA_DIR'))
            return {'ready': True}
        if action == 'recover_storage':
            return {}
        raise AppError('维护操作无效。')
