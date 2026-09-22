import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'native'), str(ROOT / 'scripts')]
import app_paths
from llm import AppError
import maintenance
from service import Service


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def old_data(self):
        source = self.root / 'old'
        app = Service(source / 'runtime')
        try: app.route('card_seed', {})
        finally: app.close()
        (source / '.env').write_text('LLM_MODEL_ID=test-model\n', encoding='utf-8')
        (source / 'runtime/study-assets').mkdir()
        (source / 'runtime/study-assets/example.wav').write_bytes(b'local audio')
        return source

    def test_import_preserves_source_and_copies_database_and_media(self):
        source = self.old_data()
        target = self.root / 'target'; target.mkdir()
        before = (source / 'runtime/haru.sqlite3').read_bytes()
        result = maintenance.import_legacy(source, target)
        self.assertTrue(result['restart_required'])
        self.assertEqual(before, (source / 'runtime/haru.sqlite3').read_bytes())
        app = Service(target / 'runtime')
        try: self.assertEqual(len(app.route('cards', {})), 5)
        finally: app.close()
        self.assertEqual((target / 'runtime/study-assets/example.wav').read_bytes(), b'local audio')
        with self.assertRaises(AppError): maintenance.import_legacy(source, target)

    def test_import_into_bootstrapped_empty_install(self):
        source = self.old_data()
        target = self.root / 'target'; target.mkdir()
        app = Service(target / 'runtime')
        try: app.route('bootstrap', {})
        finally: app.close()
        self.assertTrue(maintenance.import_legacy(source, target)['imported'])

    def test_refuse_symlink_and_future_database(self):
        source = self.old_data()
        target = self.root / 'target'; target.mkdir()
        link = source / 'runtime/study-assets/link'
        try: link.symlink_to(source / '.env')
        except OSError: self.skipTest('Symlinks unavailable on this runner')
        with self.assertRaises(AppError): maintenance.import_legacy(source, target)
        link.unlink()
        with closing(sqlite3.connect(source / 'runtime/haru.sqlite3')) as db: db.execute('PRAGMA user_version=99')
        with self.assertRaises(AppError): maintenance.import_legacy(source, target)
        with self.assertRaises(AppError): Service(source / 'runtime')
        with closing(sqlite3.connect(source / 'runtime/haru.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 99)

    def test_failure_rolls_back_and_retry_succeeds(self):
        source = self.old_data()
        target = self.root / 'target'; target.mkdir()
        app = Service(target / 'runtime'); app.close()
        original = (target / 'runtime/haru.sqlite3').read_bytes()
        replace = Path.replace
        def fail_once(path, destination):
            if '/new/runtime/study-assets' in path.as_posix():
                raise OSError('simulated interrupted move')
            return replace(path, destination)
        with patch.object(Path, 'replace', fail_once):
            with self.assertRaises(OSError): maintenance.import_legacy(source, target)
        self.assertFalse((target / '.env').exists())
        self.assertEqual((target / 'runtime/haru.sqlite3').read_bytes(), original)
        self.assertFalse((target / '.migration.json').exists())
        self.assertTrue(maintenance.import_legacy(source, target)['imported'])

    def test_recovery_after_process_dies_between_moves(self):
        root = self.root / 'target'; root.mkdir()
        identity = 'a' * 32
        stage = root / 'migration-backups' / identity
        (stage / 'old/runtime').mkdir(parents=True)
        (stage / 'old/runtime/haru.sqlite3').write_bytes(b'old snapshot')
        (root / 'runtime').mkdir()
        (root / 'runtime/haru.sqlite3').write_bytes(b'new snapshot')
        maintenance.atomic_json(root / '.migration.json', {'id': identity, 'items':[
            {'name':'runtime/haru.sqlite3','existed':True}]})
        maintenance.recover(root)
        self.assertEqual((root / 'runtime/haru.sqlite3').read_bytes(), b'old snapshot')

    def test_backup_includes_wal_and_configuration(self):
        root = self.old_data()
        db = sqlite3.connect(root / 'runtime/haru.sqlite3')
        self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO kv VALUES ('backup-test','true')"); db.commit()
        folder = maintenance.backup(root)
        with closing(sqlite3.connect(folder / 'haru.sqlite3')) as copy:
            self.assertEqual(copy.execute("SELECT value FROM kv WHERE key='backup-test'").fetchone()[0], 'true')
        self.assertTrue((folder / '.env').is_file())

    def test_frozen_macos_uses_application_support(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', str(self.root), create=True), patch.object(sys, 'platform', 'darwin'), patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(app_paths.storage_root(), self.root / 'Library/Application Support/Haru')

    def test_preview_does_not_initialize_updater(self):
        from windows_updater import WindowsUpdater
        (self.root / 'app-release.json').write_text(json.dumps({'updates_enabled':False}))
        with patch('windows_updater.resource_root', return_value=self.root), patch('windows_updater.ctypes.CDLL') as dll:
            updater = WindowsUpdater(lambda: 1, lambda: None)
            self.assertFalse(updater.automatic())
            dll.assert_not_called()

    def test_signed_feed_rejects_tampering_and_mixed_channels(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.exceptions import InvalidSignature
        from release_feed import generate, PLATFORMS
        key=Ed25519PrivateKey.generate()
        reports=[]
        for platform in PLATFORMS:
            name='Haru-1.1.0-'+platform+'.bin'
            payload=('fixture-'+platform).encode()
            (self.root/name).write_bytes(payload)
            report=dict(platform=platform,version='1.1.0',distribution='preview',updates_enabled=True,
                channel='preview',release_tag='v1.1.0-preview.1',artifact=name,length=len(payload),
                signature=base64.b64encode(key.sign(payload)).decode(),
                public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode())
            path=self.root/(platform+'.json');path.write_text(json.dumps(report));reports.append((path,report))
        generate(self.root,self.root/'feeds','1.1.0','Test notes')
        self.assertEqual(len(list((self.root/'feeds/preview').glob('*.xml'))),3)
        for path, report in reports:
            report.update(channel='stable', distribution='release', release_tag='v1.1.0')
            path.write_text(json.dumps(report))
        generate(self.root,self.root/'feeds','1.1.0','Stable notes')
        self.assertEqual(len(list((self.root/'feeds/stable').glob('*.xml'))),3)
        for path, report in reports:
            report.update(channel='preview', distribution='preview', release_tag='v1.1.0-preview.1')
            path.write_text(json.dumps(report))
        path,report=reports[0]
        package=self.root/report['artifact'];original=package.read_bytes()
        package.write_bytes(b'x'*len(original))
        with self.assertRaises(InvalidSignature):generate(self.root,self.root/'feeds','1.1.0','Test')
        package.write_bytes(original)
        report['channel']='stable';path.write_text(json.dumps(report))
        with self.assertRaises(ValueError):generate(self.root,self.root/'feeds','1.1.0','Test')


if __name__ == '__main__': unittest.main()
