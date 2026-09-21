"""Incremental builds must not reuse stale binaries or relocated runtime paths."""
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

spec = importlib.util.spec_from_file_location(
    'app_config', Path(__file__).resolve().parents[1] / 'scripts/app_config.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class AppBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='haru-build-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source with spaces'
        self.contents = self.root / 'Haru.app/Contents'
        for name in (*config.INPUTS, 'ui/brand-icon.png',
                     'Haru.app/Contents/MacOS/Haru',
                     'Haru.app/Contents/Resources/Haru.icns'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        (self.contents / 'MacOS/Haru').chmod(0o755)
        (self.root / 'pyproject.toml').write_text('[project]\nversion = "1.0.0"\n')
        self.enterContext(patch.object(config, 'ROOT', self.root))
        self.enterContext(patch.object(config, 'CONTENTS', self.contents))
        config.write_config()

    def test_unchanged_build_is_reusable(self):
        self.assertTrue(config.is_current())

    def test_changed_native_source_requires_rebuild(self):
        source = self.root / 'native/Haru.swift'
        stat = source.stat()
        source.write_text('changed')  # Same byte count, distinct modification time.
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertFalse(config.is_current())

    def test_moving_project_requires_new_runtime_paths(self):
        moved = self.root.with_name('moved source')
        self.root.rename(moved)
        with patch.object(config, 'ROOT', moved), patch.object(
                config, 'CONTENTS', moved / 'Haru.app/Contents'):
            self.assertFalse(config.is_current())
            config.write_config()
            self.assertTrue(config.is_current())

    def test_missing_bundle_icon_requires_rebuild(self):
        (self.contents / 'Resources/Haru.icns').unlink()
        self.assertFalse(config.is_current())

    def test_swift_cache_is_reused_locally_and_reset_after_move(self):
        config.prepare_cache()
        cache = self.root / 'build/swift-cache'
        module = cache / 'old.pcm'
        module.write_text('compiled with absolute paths')
        config.prepare_cache()
        self.assertTrue(module.exists())
        (cache / '.project-root').write_text('/previous/project/location')
        config.prepare_cache()
        self.assertFalse(module.exists())


if __name__ == '__main__':
    unittest.main()
