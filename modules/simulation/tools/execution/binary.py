"""Resolve portable Apollo binaries without sharing their runtime environments."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def binary_id(value):
    value = str(value)
    if value != '-1' and not re.fullmatch(r'[1-9][0-9]{0,63}', value):
        raise ValueError('Binary ID must be -1 (local) or a positive decimal ID')
    return value


@dataclass(frozen=True)
class Binary(ABC):
    binary_id: str
    path: Path
    manifest_sha256: str
    simulator_sha256: str
    archive_sha256: str | None = None

    @property
    @abstractmethod
    def kind(self):
        """Local or downloaded; serialized across the CLI pipe."""

    @property
    def executable(self):
        return self.path / 'bin/simulator_main'

    def to_dict(self):
        return dict(kind=self.kind, id=self.binary_id, path=str(self.path),
                    manifest_sha256=self.manifest_sha256,
                    simulator_sha256=self.simulator_sha256,
                    archive_sha256=self.archive_sha256)

    def verify_identity(self):
        validate_layout(self.path)
        if (sha256(self.path / 'manifest.json') != self.manifest_sha256 or
                sha256(self.executable) != self.simulator_sha256):
            raise RuntimeError('Selected binary changed after task generation: ' + str(self.path))

    def environment(self):
        self.verify_identity()
        # Start with image Python and system tools, rather than the caller's
        # previously sourced Apollo, virtualenv, LD_PRELOAD or PYTHONPATH.
        env = {'PATH': '/usr/bin:/bin'}
        for key in ('HOME', 'USER', 'LANG', 'LC_ALL', 'TMPDIR', 'CUDA_VISIBLE_DEVICES',
                    'NVIDIA_VISIBLE_DEVICES', 'NVIDIA_DRIVER_CAPABILITIES',
                    'CYBER_DOMAIN_ID', 'CYBER_IP'):
            if key in os.environ:
                env[key] = os.environ[key]
        output = subprocess.check_output(['bash', '--noprofile', '--norc', '-c',
            'set -e; source "$1/setup.bash"; env -0', 'binary', str(self.path)], env=env)
        result = dict(item.decode().split('=', 1) for item in output.split(b'\0') if b'=' in item)
        if Path(result.get('APOLLO_DISTRIBUTION_HOME', '')).resolve() != self.path:
            raise RuntimeError('Binary setup selected a different distribution')
        result['SIMULATOR_BINARY'] = str(self.executable)
        result['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
        return result


class LocalBinary(Binary):
    @property
    def kind(self):
        return 'local'


class DownloadedBinary(Binary):
    @property
    def kind(self):
        return 'downloaded'


def validate_layout(root):
    root = Path(root)
    for relative in ('manifest.json', 'setup.bash', 'bin/simulator_main',
                     'modules/simulation/simulator/task_service.py', 'SHA256SUMS'):
        path = root / relative
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Missing or package-external binary runtime file: ' + str(path))
    if not os.access(root / 'bin/simulator_main', os.X_OK):
        raise ValueError('simulator_main is not executable')
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('format') != 4:
        raise ValueError('Binary requires portable package manifest format 4')
    return manifest


def capture_binary(cls, identity, root, archive_sha256=None):
    root = Path(root).resolve(strict=True)
    validate_layout(root)
    return cls(identity, root, sha256(root / 'manifest.json'),
               sha256(root / 'bin/simulator_main'), archive_sha256)


def binary_from_dict(value):
    identity = binary_id(value['id'])
    kind = value['kind']
    if (identity == '-1' and kind != 'local') or (identity != '-1' and kind != 'downloaded'):
        raise ValueError('Binary kind and ID disagree')
    cls = LocalBinary if kind == 'local' else DownloadedBinary
    result = cls(identity, Path(value['path']).resolve(strict=True),
                 value['manifest_sha256'], value['simulator_sha256'], value.get('archive_sha256'))
    result.verify_identity()
    return result


@dataclass(frozen=True)
class BinaryArtifact:
    binary_id: str
    archive_url: str
    sha256: str
    size_bytes: int


class BinaryProvider(ABC):
    @abstractmethod
    def resolve(self, identity) -> BinaryArtifact:
        """Translate an ID to an immutable downloadable archive."""


class HTTPBinaryProvider(BinaryProvider):
    def __init__(self, service_url):
        if urllib.parse.urlsplit(service_url).scheme not in ('http', 'https'):
            raise ValueError('Binary service URL must use HTTP or HTTPS')
        self.service_url = service_url.rstrip('/')

    def resolve(self, identity):
        url = self.service_url + '/binaries/' + identity
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Binary service metadata exceeds 1 MiB')
        value = json.loads(raw)
        if str(value.get('id')) != identity:
            raise ValueError('Binary service returned a different ID')
        digest = value.get('sha256', '')
        size = value.get('size_bytes')
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or type(size) is not int or size <= 0:
            raise ValueError('Binary service must return SHA-256 and positive size_bytes')
        if not isinstance(value.get('archive_url'), str) or not value['archive_url']:
            raise ValueError('Binary service must return archive_url')
        download = urllib.parse.urljoin(url, value['archive_url'])
        if urllib.parse.urlsplit(download).scheme not in ('http', 'https'):
            raise ValueError('Binary archive URL must use HTTP or HTTPS')
        return BinaryArtifact(identity, download, digest, size)


def verify_checksums(root):
    entries = 0
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = root / name
        if (not re.fullmatch(r'[0-9a-f]{64}', expected) or not path.is_file() or
                not path.resolve().is_relative_to(root) or sha256(path) != expected):
            raise RuntimeError('Binary package checksum mismatch: ' + name)
        entries += 1
    if not entries:
        raise ValueError('Binary package has no checksum entries')


def _archive_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or not path.parts or path.parts[0] != 'binary':
        raise ValueError('Unsafe or unexpected binary archive path: ' + name)
    return path


def extract_package(archive_path, destination):
    """Extract real package hardlinks safely on Apollo's Python 3.10."""
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        indexed = {}
        for member in members:
            path = _archive_path(member.name)
            if str(path) in indexed:
                raise ValueError('Duplicate archive path: ' + member.name)
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError('Unsupported archive entry: ' + member.name)
            indexed[str(path)] = member
        for name, member in indexed.items():
            for parent in PurePosixPath(name).parents:
                ancestor = indexed.get(str(parent))
                if ancestor is not None and not ancestor.isdir():
                    raise ValueError('Archive entry has a non-directory parent: ' + name)
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
        pending = [(name, member) for name, member in indexed.items() if member.islnk()]
        while pending:
            deferred = []
            for name, member in pending:
                link = str(_archive_path(member.linkname))
                if link not in indexed or indexed[link].issym() or indexed[link].isdir():
                    raise ValueError('Invalid archive hardlink: ' + name)
                source, target = destination / link, destination / name
                if not source.is_file():
                    deferred.append((name, member))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target)
            if len(deferred) == len(pending):
                raise ValueError('Archive hardlink cycle')
            pending = deferred
        root = (destination / 'binary').resolve()
        for name, member in indexed.items():
            if not member.issym():
                continue
            target = destination / name
            if PurePosixPath(member.linkname).is_absolute():
                raise ValueError('Absolute archive symlink: ' + name)
            if not (target.parent / member.linkname).resolve().is_relative_to(root):
                raise ValueError('Package-external archive symlink: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(member.linkname)
        for name, member in indexed.items():
            if member.issym():
                target = destination / name
                if not target.exists() or not target.resolve().is_relative_to(root):
                    raise ValueError('Broken or package-external archive symlink: ' + name)


def _materialize(identity, digest, cache_dir, source_path=None, artifact=None):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Invalid binary archive SHA-256')
    cache_dir.mkdir(parents=True, exist_ok=True)
    locks = cache_dir / '.locks'
    locks.mkdir(exist_ok=True)
    key = 'local' if identity == '-1' else identity
    destination = cache_dir / key / digest
    with (locks / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            metadata = json.loads((destination / 'source.json').read_text())
            if metadata != {'id': identity, 'archive_sha256': digest}:
                raise RuntimeError('Binary cache metadata mismatch: ' + str(destination))
            validate_layout(destination / 'binary')
            verify_checksums(destination / 'binary')
            return destination / 'binary'
        with tempfile.TemporaryDirectory(prefix='.binary-download-', dir=cache_dir) as temporary:
            staging = Path(temporary)
            if artifact is not None:
                source_path = staging / 'archive.tar.gz'
                actual = hashlib.sha256()
                count = 0
                with urllib.request.urlopen(artifact.archive_url, timeout=60) as response, source_path.open('wb') as output:
                    for chunk in iter(lambda: response.read(1024 * 1024), b''):
                        count += len(chunk)
                        if count > artifact.size_bytes:
                            raise RuntimeError('Binary download exceeds declared size')
                        output.write(chunk)
                        actual.update(chunk)
                if count != artifact.size_bytes or actual.hexdigest() != digest:
                    raise RuntimeError('Binary download size / SHA-256 mismatch')
            extract_package(source_path, staging)
            root = staging / 'binary'
            validate_layout(root)
            verify_checksums(root)
            if artifact is not None:
                source_path.unlink()
            elif sha256(source_path) != digest:
                raise RuntimeError('Local binary archive changed while extracting')
            (staging / 'source.json').write_text(json.dumps({'id': identity, 'archive_sha256': digest}) + '\n')
            destination.parent.mkdir(exist_ok=True)
            staging.rename(destination)
        return destination / 'binary'


def get_binary(identity=-1, *, local_path=None, service_url=None, cache_dir=None,
               workspace=None, provider=None) -> Binary:
    identity = binary_id(identity)
    cache = Path(cache_dir or os.environ.get('SIMULATION_BINARY_CACHE',
                 str(Path.home() / '.cache/apollo_simulation/binaries'))).expanduser().resolve()
    if identity == '-1':
        selected = local_path or os.environ.get('SIMULATION_LOCAL_BINARY') or os.environ.get('APOLLO_DISTRIBUTION_HOME')
        if selected is None and workspace is not None:
            workspace = Path(workspace)
            selected = workspace / 'binary' if (workspace / 'binary').is_dir() else workspace / 'binary.tar.gz'
        if selected is None:
            raise ValueError('Local binary not selected; source its setup.bash or use --local-binary')
        selected = Path(selected).expanduser().resolve(strict=True)
        if selected.is_file():
            digest = sha256(selected)
            root = _materialize(identity, digest, cache, source_path=selected)
            return capture_binary(LocalBinary, identity, root, digest)
        return capture_binary(LocalBinary, identity, selected)
    if local_path is not None:
        raise ValueError('--local-binary is only valid with -B -1')
    if provider is None:
        url = service_url or os.environ.get('BINARY_SERVICE_URL')
        if not url:
            raise ValueError('Set BINARY_SERVICE_URL or --binary-service-url for a binary ID')
        provider = HTTPBinaryProvider(url)
    artifact = provider.resolve(identity)
    if artifact.binary_id != identity:
        raise ValueError('Binary provider returned a different ID')
    root = _materialize(identity, artifact.sha256, cache, artifact=artifact)
    return capture_binary(DownloadedBinary, identity, root, artifact.sha256)
