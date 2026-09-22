"""WinSparkle owns download, EdDSA verification and installer execution."""
import ctypes
import json

from app_paths import resource_root
from desktop_bridge import DesktopError


class WindowsUpdater:
    def __init__(self, can_shutdown, shutdown, on_error=lambda: None):
        self.dll = None
        self.callbacks = []
        path = resource_root() / 'app-release.json'
        metadata = json.loads(path.read_text()) if path.exists() else {}
        if not metadata.get('updates_enabled'):
            return
        self.dll = ctypes.CDLL(str(resource_root() / 'WinSparkle.dll'))
        for name, args, result in (
            ('set_app_details', [ctypes.c_wchar_p] * 3, None),
            ('set_appcast_url', [ctypes.c_char_p], None),
            ('set_eddsa_public_key', [ctypes.c_char_p], None),
            ('set_automatic_check_for_updates', [ctypes.c_int], None),
            ('get_automatic_check_for_updates', [], ctypes.c_int),
            ('init', [], None), ('cleanup', [], None),
            ('check_update_with_ui', [], None),
        ):
            function = getattr(self.dll, 'win_sparkle_' + name)
            function.argtypes, function.restype = args, result
        self.dll.win_sparkle_set_app_details('Haru', 'Haru', metadata['version'])
        self.dll.win_sparkle_set_appcast_url(metadata['feed_url'].encode('utf-8'))
        self.dll.win_sparkle_set_eddsa_public_key(metadata['public_key'].encode('ascii'))
        for name, result, callback in [('can_shutdown', ctypes.c_int, can_shutdown),
                                       ('shutdown_request', None, shutdown), ('error', None, on_error)]:
            kind = ctypes.CFUNCTYPE(result)
            wrapper = kind(callback)
            self.callbacks.append(wrapper)  # ctypes callbacks must outlive the native thread.
            setter = getattr(self.dll, 'win_sparkle_set_' + name + '_callback')
            setter.argtypes, setter.restype = [kind], None
            setter(wrapper)
        self.dll.win_sparkle_init()

    def check(self):
        if not self.dll:
            raise DesktopError('此构建未启用自动更新，请从发布页面下载新版安装包。')
        self.dll.win_sparkle_check_update_with_ui()

    def automatic(self, value=None):
        if not self.dll:
            return False
        if value is not None:
            if type(value) is not bool:
                raise DesktopError('更新设置无效。')
            self.dll.win_sparkle_set_automatic_check_for_updates(int(value))
        return bool(self.dll.win_sparkle_get_automatic_check_for_updates())

    def close(self):
        if self.dll:
            self.dll.win_sparkle_cleanup()
            self.dll = None
