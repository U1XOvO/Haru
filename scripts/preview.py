"""Development-only loopback preview; desktop app uses native IPC and no server."""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from service import Service
from llm import AppError
ROOT=Path(__file__).resolve().parents[1]
FILES={'/study.js':('study.js','text/javascript; charset=utf-8'),'/learning.js':('learning.js','text/javascript; charset=utf-8'),'/':('index.html','text/html; charset=utf-8'),'/index.html':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8'),'/garden.svg':('garden.svg','image/svg+xml'),'/brand-icon.png':('brand-icon.png','image/png')}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,status,body,typ='application/json'):
        self.send_response(status);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
    def host_ok(self): return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def do_GET(self):
        if not self.host_ok() or self.path not in FILES: self.respond(404,b'{}');return
        name,typ=FILES[self.path];self.respond(200,(ROOT/'ui'/name).read_bytes(),typ)
    def do_POST(self):
        origin=self.headers.get('Origin')
        if not self.host_ok() or self.path!='/rpc' or origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
            self.respond(403,b'{"ok":false,"error":"Forbidden"}');return
        app=None
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=100000: raise AppError('请求过大。')
            req=json.loads(self.rfile.read(length));app=Service()
            if req['action']=='chat_stream':
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.send_header('Cache-Control','no-store');self.end_headers()
                def emit(event):
                    self.wfile.write((json.dumps(event,ensure_ascii=False)+'\n').encode());self.wfile.flush()
                try:
                    result=app.chat_stream(req.get('params',{}),emit)
                    emit({'ok':True,'data':result})
                except AppError as e: emit({'ok':False,'error':str(e)})
                except (BrokenPipeError,ConnectionResetError):
                    app.chat_cancel(req.get('params',{}))
                except Exception: emit({'ok':False,'error':'对话未完成，本轮未保存。'})
                return
            out={'ok':True,'data':app.route(req['action'],req.get('params',{}))}
        except AppError as e: out={'ok':False,'error':str(e)}
        except Exception: out={'ok':False,'error':'本地处理失败。'}
        finally:
            if app: app.close()
        self.respond(200,json.dumps(out,ensure_ascii=False).encode())
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Haru preview: http://127.0.0.1:{server.server_port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()
