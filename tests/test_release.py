"""Release boundaries: resource paths, private data locations and stale builds."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts'), str(ROOT / 'native')]
import app_paths
import build_windows
import release_utils
from desktop_bridge import Backend


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='haru release 日本語 ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def frozen(self, platform='win32'):
        self.enterContext(patch.object(sys, 'frozen', True, create=True))
        self.enterContext(patch.object(sys, '_MEIPASS', str(self.root / '_internal'), create=True))
        self.enterContext(patch.object(sys, 'platform', platform))

    def test_source_paths_unchanged(self):
        self.assertEqual(app_paths.resource_root(), ROOT)
        self.assertEqual(app_paths.storage_root(), ROOT)

    def test_windows_distributed_bundle_separates_resources_and_user_data(self):
        self.frozen()
        with patch.object(sys, 'executable', str(self.root / 'Haru/Haru.exe')), patch.dict(
                os.environ, {'LOCALAPPDATA': str(self.root / 'user')}):
            self.assertEqual(app_paths.resource_root(), self.root / '_internal')
            self.assertEqual(app_paths.storage_root(), self.root / 'user/Haru')

    def test_local_windows_app_reuses_existing_project_without_copying_secrets(self):
        self.frozen()
        (self.root / 'start.cmd').touch()
        (self.root / 'pyproject.toml').touch()
        with patch.object(sys, 'executable', str(self.root / 'dist/Haru/Haru.exe')):
            self.assertEqual(app_paths.storage_root(), self.root)

    def test_macos_bundle_uses_application_support(self):
        self.frozen('darwin')
        with patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(app_paths.storage_root(), self.root / 'Library/Application Support/Haru')

    def test_frozen_ipc_uses_console_worker_instead_of_relaunching_gui(self):
        self.frozen()
        process = Mock()
        process.poll.return_value = 0
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(b'{"ok":true,"data":[]}\n')
        with patch.object(sys, 'executable', str(self.root / 'Haru.exe')), patch(
                'desktop_bridge.subprocess.Popen', return_value=process) as spawn:
            backend = Backend(self.root, lambda *args: None)
            self.assertTrue(backend.request({'id': 1, 'action': 'cards'})['ok'])
            self.assertEqual(spawn.call_args.args[0], [str(self.root / 'HaruBackend.exe')])
            self.assertEqual(spawn.call_args.kwargs['env']['HARU_DATA_DIR'], str(self.root))

    def test_versions_are_derived_from_project(self):
        (self.root / 'pyproject.toml').write_text('[project]\nversion="2.3.4"\n')
        self.assertEqual(release_utils.version(self.root), '2.3.4')
        self.assertIn('filevers=(2, 3, 4, 0)', release_utils.windows_version(self.root))
        (self.root / 'pyproject.toml').write_text('[project]\nversion="bad"\n')
        with self.assertRaises(ValueError): release_utils.version(self.root)

    def test_build_reuse_detects_changes_and_missing_bundle_resources(self):
        for name in ('pyproject.toml', 'uv.lock', 'scripts/build_windows.py',
                     'scripts/haru_windows.spec', 'scripts/release_utils.py',
                     'backend/bridge.py', 'ui/index.html', 'data/builtin_lessons.json'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        for name in ('Haru.exe', 'HaruBackend.exe', '_internal/python312.dll',
                     '_internal/ui/Haru.ico', '_internal/ui/index.html',
                     '_internal/data/builtin_lessons.json'):
            path = self.root / 'dist/Haru' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        manifest = self.root / 'build/windows/build.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'root': str(self.root.resolve()), 'inputs': build_windows.inputs(self.root)}))
        self.assertTrue(build_windows.is_current(self.root))
        (self.root / '.env').write_text('fixture-private')
        self.assertTrue(build_windows.is_current(self.root))
        (self.root / 'backend/bridge.py').write_text('changed implementation')
        self.assertFalse(build_windows.is_current(self.root))
        manifest.write_text(json.dumps({'root': str(self.root.resolve()), 'inputs': build_windows.inputs(self.root)}))
        (self.root / 'dist/Haru/_internal/python312.dll').unlink()
        self.assertFalse(build_windows.is_current(self.root))


if __name__ == '__main__':
    unittest.main()
