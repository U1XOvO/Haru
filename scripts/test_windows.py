"""Windows-only real WebView2 smoke check; temporary data, no network/AI calls."""
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native'))


def main():
    if sys.platform != 'win32':
        print('This check requires a Windows desktop with WebView2.', file=sys.stderr)
        return 1
    import webview
    from windows_app import API, Host, prepare_ui
    errors = []
    with tempfile.TemporaryDirectory(prefix='haru-windows-smoke-') as directory:
        host = Host(directory, prepare_ui())
        host.backend.bridge = ROOT / 'tests/windows_fixture_bridge.py'
        webview.settings['ALLOW_FILE_URLS'] = False
        host.window = webview.create_window('Haru Windows offline smoke', host.index.as_uri(),
            js_api=API(host), width=1280, height=900)
        host.window.events.before_show += host._before_show
        host.window.events.initialized += host._initialized
        host.window.events.closing += host._close

        def wait_for(expression, seconds=30):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if host.window.evaluate_js(expression): return
                time.sleep(.1)
            raise AssertionError('Timed out: ' + expression)

        def run():
            try:
                if not host.window.events.loaded.wait(30):
                    raise AssertionError('WebView2 did not load the local page')
                wait_for('typeof state !== "undefined" && state !== null')
                host.window.run_js('''
                    window.desktopSmoke = null;
                    (async()=>{
                      try {
                        const cards=await rpc('card_seed');
                        await navigate('cards');
                        const session=await rpc('chat_state',{scene:'cafe'});
                        let delta='';
                        await streamingRPC({session:session.session,message:'こんにちは',request_id:crypto.randomUUID()},event=>delta=event.text);
                        window.desktopSmoke={ok:cards.length>0 && delta.includes('日本語'),delta};
                      } catch(error) { window.desktopSmoke={ok:false,error:error.message}; }
                    })();
                ''')
                wait_for('window.desktopSmoke !== null')
                result = host.window.evaluate_js('window.desktopSmoke')
                if not result['ok']: raise AssertionError(json.dumps(result, ensure_ascii=False))
                # Block a navigation to a different local document before IPC injection.
                host.window.load_url((ROOT / 'ui/index.html').as_uri())
                time.sleep(.5)
                if host.window.get_current_url() != host.index.as_uri():
                    raise AssertionError('Navigation guard failed')
                print('PASS: WebView2 startup, UTF-8 RPC, cards UI, streaming and navigation guard.')
            except Exception as error:
                errors.append(str(error))
                print('FAIL:', error, file=sys.stderr)
            finally:
                host._close()
                host.window.destroy()

        try:
            webview.start(run, gui='edgechromium', user_agent='HaruDesktop/Windows', http_server=False,
                          private_mode=True, storage_path=str(Path(directory) / 'webview'))
        finally:
            host._close()
        if not host.renderer_ready:
            errors.append('WebView2 renderer unavailable')
    return 1 if errors else 0


if __name__ == '__main__': sys.exit(main())
