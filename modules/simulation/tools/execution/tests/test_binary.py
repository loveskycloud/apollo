import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from modules.simulation.tools.execution.binary import (
    Binary, DownloadedBinary, LocalBinary, binary_from_dict, extract_package, get_binary,
)
from modules.simulation.tools.execution.mock_binary_service import make_server
from fixtures import archive, package


class BinaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='binary paths ')
        self.path = Path(self.tmp.name)
        self.local = package(self.path / 'local binary')
        self.remote = package(self.path / 'remote binary', 'remote')
        self.archive = archive(self.remote, self.path / 'binary.tar.gz')
        self.cache = self.path / 'cache'
        self.server = make_server('123143', self.archive, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def get(self, identity='123143'):
        return get_binary(identity, service_url=self.url, cache_dir=self.cache)

    def stats(self):
        with urllib.request.urlopen(self.url + '/stats') as response:
            return json.load(response)

    def test_download_and_cache_select_requested_binary(self):
        result = self.get()
        self.assertIsInstance(result, Binary)
        self.assertIsInstance(result, DownloadedBinary)
        self.assertEqual(result.binary_id, '123143')
        self.assertEqual(json.loads((result.path / 'manifest.json').read_text())['profile'], 'remote')
        again = self.get()
        self.assertEqual(result, again)
        self.assertEqual(self.stats()['/archives/123143/binary.tar.gz'], 1)
        self.assertEqual(binary_from_dict(result.to_dict()), result)

    def test_missing_remote_id_never_uses_local_binary(self):
        with patch.dict(os.environ, {'APOLLO_DISTRIBUTION_HOME': str(self.local)}):
            with self.assertRaises(urllib.error.HTTPError):
                self.get('404')
        self.assertFalse((self.cache / '404').exists())

    def test_bad_download_checksum_does_not_publish_cache(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server = make_server('123143', self.archive, port=0, sha256='0' * 64)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
            self.get()
        self.assertFalse((self.cache / '123143').exists())
        self.assertEqual(list(self.cache.glob('.binary-download-*')), [])

    def test_corrupted_cache_is_an_error_without_replacing_it(self):
        result = self.get()
        (result.path / 'lib/runtime/libtest.so').write_text('corruption')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            self.get()
        self.assertEqual((result.path / 'lib/runtime/libtest.so').read_text(), 'corruption')
        self.assertEqual(self.stats()['/archives/123143/binary.tar.gz'], 1)

    def test_local_directory_and_local_archive_return_local_objects(self):
        direct = get_binary(-1, local_path=self.local, cache_dir=self.cache)
        packed = get_binary(-1, local_path=self.archive, cache_dir=self.cache)
        self.assertIsInstance(direct, LocalBinary)
        self.assertIsInstance(packed, LocalBinary)
        self.assertEqual(direct.path, self.local)
        self.assertEqual(packed.binary_id, '-1')
        self.assertEqual(packed.archive_sha256, hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_runtime_environment_does_not_inherit_another_distribution(self):
        binary = get_binary(-1, local_path=self.local, cache_dir=self.cache)
        with patch.dict(os.environ, {'APOLLO_WORKSPACE': '/wrong', 'LD_LIBRARY_PATH': '/wrong/lib',
                                    'PYTHONPATH': '/wrong/python', 'SIMULATOR_BINARY': '/wrong/bin'}):
            env = binary.environment()
        self.assertEqual(env['APOLLO_WORKSPACE'], str(self.local))
        self.assertEqual(env['SIMULATOR_BINARY'], str(self.local / 'bin/simulator_main'))
        self.assertNotIn('/wrong', env['LD_LIBRARY_PATH'] + env['PYTHONPATH'])

    def test_changed_binary_identity_is_rejected_after_generation(self):
        binary = get_binary(-1, local_path=self.local, cache_dir=self.cache)
        (self.local / 'bin/simulator_main').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'changed after task generation'):
            binary_from_dict(binary.to_dict())

    def test_invalid_id_and_missing_service_are_explicit(self):
        for identity in ('../123', '0', '-2', '1x'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.get(identity)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'BINARY_SERVICE_URL'):
                get_binary(1, cache_dir=self.cache)

    def test_archive_rejects_escape_and_symlink_parent(self):
        outside = self.path / 'outside'
        outside.write_text('unchanged')
        cases = [('binary/../outside', tarfile.REGTYPE, ''),
                 (str(outside), tarfile.REGTYPE, ''),
                 ('binary/link', tarfile.SYMTYPE, str(outside)),
                 ('binary/link', tarfile.LNKTYPE, '../outside')]
        for index, (name, kind, link) in enumerate(cases):
            with self.subTest(name=name, kind=kind):
                path = self.path / f'unsafe-{index}.tar.gz'
                with tarfile.open(path, 'w:gz') as tf:
                    info = tarfile.TarInfo(name)
                    info.type, info.linkname = kind, link
                    if kind == tarfile.REGTYPE:
                        info.size = 4
                        tf.addfile(info, io.BytesIO(b'evil'))
                    else:
                        tf.addfile(info)
                with self.assertRaises(ValueError):
                    extract_package(path, self.path / f'unpack-{index}')
        self.assertEqual(outside.read_text(), 'unchanged')
        path = self.path / 'symlink-parent.tar.gz'
        with tarfile.open(path, 'w:gz') as tf:
            link = tarfile.TarInfo('binary/link'); link.type = tarfile.SYMTYPE; link.linkname = 'internal'
            tf.addfile(link)
            child = tarfile.TarInfo('binary/link/child'); child.size = 1
            tf.addfile(child, io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError, 'non-directory parent'):
            extract_package(path, self.path / 'unpack-link-parent')

    def test_real_hardlinks_survive_even_if_the_target_comes_later(self):
        path = self.path / 'hardlinks.tar.gz'
        with tarfile.open(path, 'w:gz') as tf:
            link = tarfile.TarInfo('binary/copy'); link.type = tarfile.LNKTYPE; link.linkname = 'binary/original'
            tf.addfile(link)
            original = tarfile.TarInfo('binary/original'); original.size = 3
            tf.addfile(original, io.BytesIO(b'abc'))
        destination = self.path / 'unpack-hardlinks'
        extract_package(path, destination)
        self.assertEqual((destination / 'binary/copy').read_bytes(), b'abc')
        self.assertEqual((destination / 'binary/copy').stat().st_ino,
                         (destination / 'binary/original').stat().st_ino)


if __name__ == '__main__':
    unittest.main()
