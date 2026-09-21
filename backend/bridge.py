#!/usr/bin/env python3
"""Single-request JSON IPC. stdout contains only the response; saved secrets are never returned."""
import json
import sys
from llm import AppError
from service import Service

def main():
    # Windows pipes otherwise use the active ANSI code page, losing Japanese.
    sys.stdout.reconfigure(encoding='utf-8')
    app=None
    try:
        raw=sys.stdin.buffer.read(100_001)
        if len(raw)>100_000: raise AppError('请求过大。')
        req=json.loads(raw)
        app=Service()
        if req['action']=='chat_stream':
            def emit(event): print(json.dumps(event,ensure_ascii=False),flush=True)
            out={'ok':True,'data':app.chat_stream(req.get('params',{}),emit)}
        else:
            out={'ok':True,'data':app.route(req['action'],req.get('params',{}))}
    except AppError as e: out={'ok':False,'error':str(e)}
    except Exception: out={'ok':False,'error':'本地处理未完成，请检查输入或重试。已有记录已保留。'}
    finally:
        if app: app.close()
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__': main()
