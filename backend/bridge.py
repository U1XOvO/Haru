#!/usr/bin/env python3
"""Single-request JSON IPC: optional progress lines, then one final response; no saved secrets."""
import json
import sys
from llm import AppError
from generation import progress_scope

def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)

def main():
    # Windows pipes otherwise use the active ANSI code page, losing Japanese.
    sys.stdout.reconfigure(encoding='utf-8')
    app=None
    try:
        raw=sys.stdin.buffer.read(100_001)
        if len(raw)>100_000: raise AppError('请求过大。')
        req=json.loads(raw)
        if not isinstance(req,dict): raise AppError('请求格式无效。')
        scope = progress_scope(req.get('action'), send if req.get('stream') is True else None)
        scope.__enter__()
        if req.get('action') == 'speech_prepare':
            from speech_worker import prepare
            data = prepare(req.get('params', {}))
        elif req.get('action') in {'speech_settings_get', 'speech_settings_save', 'speech_voice_create'}:
            action = req['action']
            if action == 'speech_voice_create':
                import asyncio
                from speech_google import create_voice
                params = req.get('params', {})
                if not isinstance(params, dict):
                    raise AppError('音色请求格式无效。')
                from speech import SpeechError
                try:
                    data = asyncio.run(create_voice(params.get('id'), force=params.get('force') is True))
                except SpeechError as error:
                    raise AppError(str(error)) from None
            else:
                from speech_config import editable_settings, save_settings
                data = editable_settings() if action == 'speech_settings_get' else save_settings(req.get('params', {}))
        elif req.get('action') in {'import_legacy', 'prepare_update', 'recover_storage'}:
            from maintenance import dispatch
            data = dispatch(req['action'], req.get('params', {}))
        else:
            from service import Service
            app=Service()
            data=app.route(req['action'],req.get('params',{}))
        out={'ok':True,'data':data}
    except AppError as e: out={'ok':False,'error':str(e)}
    except Exception: out={'ok':False,'error':'本地处理未完成，请检查输入或重试。已有记录已保留。'}
    finally:
        if app: app.close()
        if 'scope' in locals(): scope.__exit__(None, None, None)
    send(out)
if __name__=='__main__': main()
