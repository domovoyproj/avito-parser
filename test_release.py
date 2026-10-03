"""Release archives must contain the complete runnable application without local state."""
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import build_release


class ReleaseTests(unittest.TestCase):
    def test_archives_import_application_and_exclude_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(build_release, 'DIST_DIR', root/'dist'):
                build_release.build_releases()
            archive = next((root/'dist').glob('*.zip'))
            with zipfile.ZipFile(archive) as stream:
                names = set(stream.namelist())
                stream.extractall(root/'unpacked')
            with tarfile.open(next((root/'dist').glob('*.tar.gz'))) as stream:
                linux_names = set(stream.getnames())
            for name in ('routers/parser.py','repositories/items.py','constraints.txt','setup_admin.py','export_jobs.py','manage_db.py','static/vendor/lucide.js'):
                self.assertIn('avito_parser/'+name,names)
                self.assertIn('avito_parser/'+name,linux_names)
            self.assertFalse(any(name.endswith(('.db','.env','.pyc','-wal','-shm')) for name in names))
            env = dict(os.environ, PYTHONPATH='', APP_ENV_FILE=str(root/'absent.env'), BOOTSTRAP_ADMIN_PASSWORD='')
            subprocess.run([sys.executable,'-c','import web_server, manage_db; assert web_server.app'],
                           cwd=root/'unpacked'/'avito_parser',env=env,check=True,capture_output=True)
