#!/usr/bin/env python3
"""Single-request JSON IPC. stdout contains only the response; saved secrets are never returned."""
import json
import sys
from llm import AppError

def main():
    # Windows pipes otherwise use the active ANSI code page, losing Japanese.
    sys.stdout.reconfigure(encoding='utf-8')
    app=None
    try:
        raw=sys.stdin.buffer.read(100_001)
        if len(raw)>100_000: raise AppError('请求过大。')
        req=json.loads(raw)
        if not isinstance(req,dict): raise AppError('请求格式无效。')
        if req.get('action') == 'speech_prepare':
            from speech_worker import prepare
            data = prepare(req.get('params', {}))
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
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__': main()
