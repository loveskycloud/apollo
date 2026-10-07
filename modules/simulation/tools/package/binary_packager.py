#!/usr/bin/env python3
"""Build a relocatable Apollo runtime archive. Python standard library only.

Implementation for modules/simulation/apps/gen_binary.py.
No SDK installation occurs.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PROFILES = ROOT / "modules/simulation/tools/package/binary_profiles.json"
# Keep glibc and the ELF loader together in the destination base image.
SYSTEM_LIBS = re.compile(r"^(ld-linux.*|lib(c|m|pthread|dl|rt|resolv|util|anl)\.so\..*)$")
DRIVER_LIBS = re.compile(r"^lib(cuda|nvidia-[\w-]+)\.so(?:\..*)?$")
SKIP_DIRS = {".git", ".cache", ".venv", "venv", "node_modules", "__pycache__",
             "testdata", "tests", "test", "test-artifacts", "dumps", "log",
             "records", "bags", "checkpoints", "rerun", "output", "dist"}
SOURCE_SUFFIXES = {".cc", ".cpp", ".c", ".h", ".hpp", ".a", ".o", ".bzl",
                   ".bazel", ".pyc", ".md", ".sh", ".bash", ".snapshot"}


def log(message):
    print(f"[gen_binary] {message}", flush=True)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def elf(path):
    try:
        with Path(path).open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except (OSError, IsADirectoryError):
        return False


def relative_link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    value = os.path.relpath(target, path.parent)
    if path.is_symlink():
        if os.readlink(path) != value:
            raise RuntimeError(f"Conflicting link: {path}")
    elif path.exists():
        raise RuntimeError(f"Link would overwrite a file: {path}")
    else:
        path.symlink_to(value)


def valid_relative(value):
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"Expected a relative package path: {value}")
    return p


def target_expression(patterns, exclude_regex=None):
    # Limit query to production runtime rules; library wrappers are dependencies.
    scope = " union ".join(patterns)
    expression = (f'let scope = {scope} in '
            '((kind("(cc_binary|py_binary|cyber_plugin_description) rule", $scope) '
            'except attr(tags, "export_library", $scope)) '
            'except attr(testonly, 1, $scope))')
    if exclude_regex:
        expression += f' except filter({json.dumps(exclude_regex)}, $scope)'
    return expression


def load_profiles(path):
    profiles = json.loads(Path(path).read_text())
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Profile file must be a nonempty JSON object")
    for name, item in profiles.items():
        if not item.get("patterns"):
            raise ValueError(f"Profile {name} has no patterns")
        for pattern in item["patterns"]:
            if not re.fullmatch(r"//[\w/.:+*-]+", pattern):
                raise ValueError(f"Invalid target pattern: {pattern}")
        for resource in item.get("resources", []):
            valid_relative(resource)
        for name, source in item.get("library_overrides", {}).items():
            if Path(name).name != name or not Path(source).is_absolute():
                raise ValueError(f"Invalid library override: {name}={source}")
        for library in item.get("image_libraries", []):
            if Path(library).name != library:
                raise ValueError(f"Invalid image library: {library}")
        for root in item.get("system_library_roots", []):
            if not Path(root).is_absolute():
                raise ValueError(f"System library root must be absolute: {root}")
        for target, dependencies in item.get("runtime_dependencies", {}).items():
            for label in [target, *dependencies]:
                if not re.fullmatch(r"//[\w/.:+*-]+", label):
                    raise ValueError(f"Invalid runtime dependency label: {label}")
        for target, runtime in item.get("python_runtimes", {}).items():
            if not re.fullmatch(r"//[\w/.:+*-]+", target):
                raise ValueError(f"Invalid Python runtime target: {target}")
            valid_relative(runtime['requirements'])
            valid_relative(runtime['path'])
        if item.get("system_library_baseline"):
            baseline_path = Path(item["system_library_baseline"])
            if not baseline_path.is_absolute():
                baseline_path = Path(path).parent / baseline_path
            baseline = json.loads(baseline_path.read_text())
            if baseline["architecture"] != platform.machine():
                raise ValueError(f"Image library baseline architecture differs: {baseline_path}")
            for library, source in baseline["libraries"].items():
                if Path(library).name != library or not Path(source).is_absolute():
                    raise ValueError(f"Invalid image library baseline: {library}={source}")
            item["system_library_paths"] = baseline["libraries"]
            item["image_library_baseline"] = {k: v for k, v in baseline.items() if k != "libraries"}
    return profiles


class ParallelGzip:
    """Bounded parallel compression using standard concatenated gzip members."""
    def __init__(self, path, workers=6, level=6, block_size=16 * 1024 * 1024):
        self.stream = Path(path).open("wb")
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.pending = deque()
        self.buffer = bytearray()
        self.limit = max(1, workers * 2)
        self.level, self.block_size = level, block_size

    def write(self, data):
        size = len(data)
        self.buffer.extend(data)
        while len(self.buffer) >= self.block_size:
            block = bytes(self.buffer[:self.block_size])
            del self.buffer[:self.block_size]
            self.submit(block)
        return size

    def submit(self, block):
        self.pending.append(self.pool.submit(gzip.compress, block, compresslevel=self.level, mtime=0))
        if len(self.pending) >= self.limit:
            self.stream.write(self.pending.popleft().result())

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            if error_type is None:
                if self.buffer:
                    self.submit(bytes(self.buffer))
                    self.buffer.clear()
                while self.pending:
                    self.stream.write(self.pending.popleft().result())
        finally:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.stream.close()


class Builder:
    def __init__(self, args, profile, work):
        self.args, self.profile, self.work = args, profile, work
        self.stage = work / "binary"
        self.stage.mkdir()
        self.env = dict(os.environ, PYTHON_BIN_PATH="/usr/bin/python3",
                        TF_NEED_CUDA="1" if args.config == "gpu" else "0")
        library_dirs = [Path("/usr/local/lib")]
        library_dirs += sorted(Path("/opt/apollo/neo/lib").glob("3rd-*"))
        library_dirs += [Path("/usr/local/cuda/lib64"), Path("/usr/local/cuda-11.8/lib64")]
        library_dirs = [p for p in library_dirs if p.is_dir()]
        self.env["LD_LIBRARY_PATH"] = ":".join(map(str, library_dirs)) + ":" + self.env.get("LD_LIBRARY_PATH", "")
        self.options = [f"--config={args.config}", "--compilation_mode=opt",
                        f"--jobs={args.jobs}", "--color=no", "--curses=no"]
        self.options += [f"--{kind}=-L{p}" for kind in ("linkopt", "host_linkopt")
                         for p in library_dirs]
        if platform.machine() == "x86_64":
            self.options += profile.get("x86_bazel_args", [])
        self.options += args.bazel_arg
        self.libraries = {}
        self.library_destinations = {}
        self.link_maps = {}
        self.plugin_bindings = {}
        self.origins = {}
        self.elf_files = set()
        self.system = set()
        self.drivers = set()
        self.hashes = {}
        self.output_map = {}
        self.current_outputs = {}
        self.candidates = {}
        self.affected_libraries = set()
        self.dynamic_cache = {}
        self.symbol_cache = {}
        self.ldd_cache = {}
        self.compatibility = {}
        self.image_libraries = {}
        self.image_requirements = {}
        self.private_library_names = set()
        # Apollo's sysroot can shadow a newer distro patchelf.
        candidates = {shutil.which("patchelf"), "/usr/bin/patchelf"}
        versions = []
        for program in candidates - {None}:
            if os.access(program, os.X_OK):
                version = subprocess.check_output([program, "--version"], text=True)
                versions.append((tuple(map(int, re.findall(r"\d+", version))), program))
        self.patchelf = max(versions)[1] if versions else "patchelf"
        self.logfile = args.output.with_suffix(args.output.suffix + ".build.log")

    def run(self, command, capture=False):
        log(f"{command[0]} {command[1]} (full command in {self.logfile})")
        with self.logfile.open("a") as stream:
            stream.write("\n$ " + shlex.join(map(str, command)) + "\n")
            stream.flush()
            if capture:
                result = subprocess.run(command, cwd=ROOT, env=self.env, text=True,
                                        stdout=subprocess.PIPE, stderr=stream)
                stream.write(result.stdout)
                if result.returncode:
                    raise RuntimeError(f"Command failed ({result.returncode}); see {self.logfile}")
                return result.stdout
            process = subprocess.Popen(command, cwd=ROOT, env=self.env, stdout=stream,
                                       stderr=subprocess.STDOUT)
            try:
                while True:
                    try:
                        code = process.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        log(f"Build running; progress log: {self.logfile}")
            except KeyboardInterrupt:
                # Cancel Bazel's client too, so an interrupted packager does
                # not leave the server compiling against changing sources.
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
            if code:
                raise RuntimeError(f"Build failed ({code}); see {self.logfile}")

    def bazel(self, verb, *extra, capture=False):
        opts = [] if verb == "query" else self.options
        return self.run(["bazel", verb, *opts, *extra], capture=capture)

    def copy(self, source, destination):
        source = Path(source).resolve(strict=True)
        destination = self.stage / valid_relative(destination)
        if destination.exists():
            if digest(source) != digest(destination):
                raise RuntimeError(f"Conflicting files for {destination}: {source}")
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(destination.stat().st_mode | 0o200)
        self.origins[str(destination.relative_to(self.stage))] = str(source)
        if elf(destination):
            self.elf_files.add(destination)
        return destination

    def dynamic(self, source):
        source = Path(source).resolve()
        if source not in self.dynamic_cache:
            self.dynamic_cache[source] = subprocess.check_output(["readelf", "-d", source], text=True)
        return self.dynamic_cache[source]

    @staticmethod
    def build_relative(source):
        match = re.search(r"/bazel-out/[^/]+/bin/(.*)", str(source))
        return match.group(1).removeprefix("external/apollo_src/") if match else None

    def runtime_name(self, name, source):
        relative = self.build_relative(source)
        if name in self.private_library_names and relative and relative.startswith("modules/"):
            qualified = "lib" + "__".join(Path(relative).parts[:-1]) + "__" + name
            if len(qualified.encode()) > 240:
                qualified = "libmodules__" + hashlib.sha256(relative.encode()).hexdigest()[:16] + "__" + name
            return qualified
        return name

    def canonical_source(self, name, source):
        source = Path(source).resolve(strict=True)
        override = self.profile.get("library_overrides", {}).get(name)
        if override:
            chosen = Path(override.replace("{multiarch}", sysconfig.get_config_var("MULTIARCH") or f"{platform.machine()}-linux-gnu")).resolve(strict=True)
            soname = re.search(r"\(SONAME\).*\[(.+)\]", self.dynamic(chosen))
            if not soname or soname.group(1) != name:
                raise RuntimeError(f"Library override for {name} has an incompatible SONAME: {chosen}")
            return chosen
        # Only outputs in this configured build may replace SDK duplicates.
        relative = self.build_relative(source)
        return self.current_outputs.get(relative, source)

    def symbols(self, source):
        source = Path(source).resolve()
        if source not in self.symbol_cache:
            output = subprocess.check_output(["readelf", "--wide", "--dyn-syms", source], text=True)
            defined, imported = set(), set()
            for line in output.splitlines():
                fields = line.split()
                if len(fields) < 8 or not fields[0].endswith(":") or fields[4] not in {"GLOBAL", "WEAK"}:
                    continue
                name = fields[7]
                if fields[6] == "UND":
                    if fields[4] == "GLOBAL":
                        imported.add(name)
                else:
                    defined.add(name.replace("@@", "@"))
                    if "@@" in name or "@" not in name:
                        defined.add(name.split("@")[0])
            self.symbol_cache[source] = defined, imported
        return self.symbol_cache[source]

    def dependency_signature(self, source):
        result = []
        for name, actual in self.ldd(source):
            if SYSTEM_LIBS.fullmatch(name) or DRIVER_LIBS.fullmatch(name):
                continue
            selected = self.canonical_source(name, actual)
            if selected not in self.hashes:
                self.hashes[selected] = digest(selected)
            result.append((self.runtime_name(name, actual), self.hashes[selected]))
        return sorted(result)

    def add_library(self, source, name=None):
        source = Path(source)
        name = Path(name).name if name else source.name
        if SYSTEM_LIBS.fullmatch(name):
            self.system.add(name)
            return None
        if DRIVER_LIBS.fullmatch(name):
            self.drivers.add(name)
            return None
        observed = source.resolve(strict=True)
        runtime_name = self.runtime_name(name, observed)
        actual = self.canonical_source(name, observed)
        self.candidates.setdefault(runtime_name, set()).add(observed)
        if observed != actual:
            self.affected_libraries.add(runtime_name)
        key = (runtime_name, actual)
        if key in self.library_destinations:
            return self.library_destinations[key]
        if runtime_name in self.libraries:
            old = self.libraries[runtime_name]
            if old != actual:
                for item in (old, actual):
                    if item not in self.hashes:
                        self.hashes[item] = digest(item)
                if self.hashes[old] != self.hashes[actual]:
                    raise RuntimeError(f"Different libraries have the same runtime name {runtime_name}: {old}, {actual}; select one compatible library with library_overrides or fix the build graph")
                if self.dependency_signature(old) != self.dependency_signature(actual):
                    raise RuntimeError(f"Identical library bytes resolve different dependencies: {old}, {actual}; fix the build graph")
                destination = self.library_destinations[(runtime_name, old)]
                if runtime_name not in self.image_libraries and self.image_provides(name, actual):
                    # Generated repositories can copy an image library. Prefer
                    # the image only after byte/dependency equality is proven.
                    destination.unlink()
                    self.elf_files.discard(destination)
                    self.origins.pop(str(destination.relative_to(self.stage)))
                    self.libraries[runtime_name] = actual
                    self.image_libraries[runtime_name] = actual
                    for existing in self.library_destinations:
                        if existing[0] == runtime_name:
                            self.library_destinations[existing] = None
                    destination = None
                self.library_destinations[key] = destination
                return destination
        else:
            self.libraries[runtime_name] = actual
        if self.image_provides(name, actual):
            self.image_libraries[runtime_name] = actual
            self.library_destinations[key] = None
            return None
        destination = self.copy(actual, Path("lib/runtime") / runtime_name)
        self.library_destinations[key] = destination
        self.elf_files.add(destination)
        return destination

    def image_provides(self, name, source):
        if name in self.profile.get("image_libraries", []):
            return True
        expected = self.profile.get("system_library_paths", {}).get(name)
        if not expected or Path(source).resolve() != Path(expected):
            # A developer may install more libraries under /usr/lib. Only the
            # recorded clean-image inventory establishes image availability.
            return False
        source = Path(source).resolve()
        return any(source.is_relative_to(Path(root).resolve())
                   for root in self.profile.get("system_library_roots", []))

    def image_library_dirs(self):
        # Declared providers take precedence over distro copies with the same
        # SONAME. Do not inherit a caller's SDK search paths.
        names = self.profile.get("image_libraries", [])
        priority = [str(self.image_libraries[name].parent) for name in names if name in self.image_libraries]
        others = sorted({str(source.parent) for source in self.image_libraries.values()})
        return list(dict.fromkeys(priority + others))

    def setup_script(self):
        paths = self.image_library_dirs()
        return SETUP.replace(":/usr/local/nvidia/lib:",
                             (":" + ":".join(paths) if paths else "") + ":/usr/local/nvidia/lib:")

    def collect_output(self, value, runtime_data=False):
        source = ROOT / value
        if not source.is_file():
            return
        match = re.match(r"bazel-out/[^/]+/bin/(.*)", value)
        if not match:
            if runtime_data:
                relative = valid_relative(value)
                self.copy(source, relative)
                self.output_map[value] = str(relative)
            return
        rel = Path(match.group(1))
        schema_path = str(rel).removeprefix("external/apollo_src/")
        if runtime_data and schema_path.endswith("_pb2.py") and schema_path.startswith(("modules/", "cyber/")):
            destination = Path("python") / schema_path
            self.copy(source, destination)
            self.output_map[value] = str(destination)
        elif elf(source):
            dynamic = subprocess.check_output(["readelf", "-d", source], text=True)
            if ".so" in source.name or "(SONAME)" in dynamic:
                # dlopen plugins can share a basename (e.g. prediction libmodel.so).
                # Keep their qualified paths; only linker dependencies are pooled.
                library = self.copy(source, Path("lib") / rel)
                self.elf_files.add(library)
                self.output_map[value] = str(Path("lib") / rel)
            else:
                destination = self.stage / "bin" / source.name
                if destination.exists() and digest(destination) != digest(source):
                    destination = self.stage / "bin" / ("__".join(rel.parts))
                destination = self.copy(source, destination.relative_to(self.stage))
                self.elf_files.add(destination)
                self.output_map[value] = str(destination.relative_to(self.stage))
        elif "cyber_plugin_index" in rel.parts:
            index = self.copy(source, Path("share/cyber_plugin_index") / source.name)
            description = source.read_text().strip()
            self.copy(ROOT / valid_relative(description), description)
            package = Path(*rel.parts[:rel.parts.index("cyber_plugin_index")])
            prefix = str(package).replace("/", "__") + "__"
            if not source.name.startswith(prefix):
                raise RuntimeError(f"Unexpected generated plugin index name: {source.name}")
            self.plugin_bindings[description] = str(package / source.name[len(prefix):])
            # The simulator sets DESCRIPTION_PATH to DISTRIBUTION_HOME directly.
            index.write_text(description)
        elif runtime_data:
            self.copy(source, rel)
            self.output_map[value] = str(rel)

    def ldd(self, path):
        path = Path(path).resolve()
        if path in self.ldd_cache:
            return self.ldd_cache[path]
        result = subprocess.run(["ldd", str(path)], env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in result.stdout.splitlines():
            if "not found" in line:
                name = line.split()[0]
                if DRIVER_LIBS.fullmatch(name):
                    self.drivers.add(name)
                else:
                    raise RuntimeError(f"Unresolved dynamic dependency of {path}:\n{result.stdout}")
        if result.returncode and not any(x in result.stdout for x in ("statically linked", "not a dynamic executable")):
            raise RuntimeError(f"ldd failed for {path}: {result.stdout}")
        dependencies = []
        for line in result.stdout.splitlines():
            match = re.match(r"\s*(\S+)\s+=>\s+(/.+?)\s+\(0x", line)
            if match:
                dependencies.append((match.group(1), Path(match.group(2))))
        self.ldd_cache[path] = dependencies
        return dependencies

    def dependencies(self, sources):
        log(f"Resolving dynamic dependencies of {len(sources)} ELF outputs")
        # Inspect original search paths, then select one provider per public
        # SONAME and qualify distinct module-private libraries.
        resolved_sources = {}
        providers = {}
        sources = set(sources) | set(self.libraries.values())
        for source in sources:
            soname = re.search(r"\(SONAME\).*\[(.+)\]", self.dynamic(source))
            if soname:
                providers.setdefault(soname.group(1), set()).add(source.resolve())
        with ThreadPoolExecutor(max_workers=min(self.args.jobs, 8)) as pool:
            pending = {p.resolve() for p in sources}
            while pending:
                current = sorted(pending)
                pending = set()
                for original, entries in zip(current, pool.map(self.ldd, current)):
                    resolved_sources[original] = entries
                    for name, source in entries:
                        if SYSTEM_LIBS.fullmatch(name) or DRIVER_LIBS.fullmatch(name):
                            continue
                        selected = self.canonical_source(name, source)
                        providers.setdefault(name, set()).add(selected)
                        if selected not in resolved_sources:
                            pending.add(selected)
                pending -= resolved_sources.keys()
        self.classify_private_libraries(providers)
        # Collected runfile libraries were canonicalized before classification.
        candidates, affected = self.candidates, self.affected_libraries
        self.candidates, self.affected_libraries = {}, set()
        for name, observed in candidates.items():
            for source in observed:
                runtime_name = self.runtime_name(name, source)
                self.candidates.setdefault(runtime_name, set()).add(source)
                if name in affected:
                    self.affected_libraries.add(runtime_name)
        for original, entries in sorted(resolved_sources.items()):
            resolved = {}
            for name, source in entries:
                destination = self.add_library(source.resolve(), name)
                if destination is not None:
                    resolved[Path(name).name] = destination.name
                elif Path(name).name in self.image_libraries:
                    resolved[Path(name).name] = Path(name).name
            self.link_maps[original] = resolved

    def classify_private_libraries(self, providers):
        for name, sources in providers.items():
            paths = {self.build_relative(source) for source in sources}
            if len(paths) > 1 and all(path and path.startswith("modules/") for path in paths):
                self.private_library_names.add(name)

    def unify_collected_libraries(self):
        for path in sorted(self.elf_files):
            relative = str(path.relative_to(self.stage))
            original = Path(self.origins[relative])
            soname = re.search(r"\(SONAME\).*\[(.+)\]", self.dynamic(original))
            if not soname:
                continue
            name = soname.group(1)
            runtime_name = self.runtime_name(name, original)
            chosen = self.canonical_source(name, original)
            self.candidates.setdefault(runtime_name, set()).add(original)
            if self.image_provides(name, chosen):
                # Runfiles can contain copies of image-owned libraries. Omit
                # those copies too; DT_NEEDED resolves the declared provider.
                self.add_library(original, name)
                path.unlink()
                self.elf_files.discard(path)
                del self.origins[relative]
                continue
            if chosen != original:
                self.affected_libraries.add(runtime_name)
                shutil.copy2(chosen, path)
                path.chmod(path.stat().st_mode | 0o200)
                self.origins[relative] = str(chosen)

    def check_library_compatibility(self):
        # Check actual consumers, not every unused symbol exported by a library.
        consumers = {Path(self.origins[str(path.relative_to(self.stage))]) for path in self.elf_files}
        consumers.update(self.libraries.values())
        for name in sorted((self.affected_libraries & self.libraries.keys()) | self.image_libraries.keys()):
            chosen = self.libraries[name]
            exports = self.symbols(chosen)[0]
            possible = set().union(*(self.symbols(source)[0] for source in self.candidates[name])) | exports
            required = set()
            for source in consumers:
                direct = re.findall(r"\(NEEDED\).*\[(.+)\]", self.dynamic(source))
                bindings = self.link_maps[source]
                if any(bindings.get(Path(dependency).name) == name for dependency in direct):
                    required |= self.symbols(source)[1] & possible
            missing = required - exports
            if missing:
                raise RuntimeError(f"Selected {name} ({chosen}) lacks required ELF symbols: {sorted(missing)[:20]}")
            self.compatibility[name] = {"selected": str(chosen), "required_symbols_checked": len(required)}
            if name in self.image_libraries:
                self.image_requirements[name] = sorted(required)

    def remove_image_copies(self):
        for path in sorted(self.elf_files):
            relative = str(path.relative_to(self.stage))
            original = Path(self.origins[relative])
            soname = re.search(r"\(SONAME\).*\[(.+)\]", self.dynamic(original))
            if soname and self.runtime_name(soname.group(1), original) in self.image_libraries:
                path.unlink()
                self.elf_files.discard(path)
                del self.origins[relative]

    def resources(self):
        log("Collecting runtime resources")
        for root_name in self.profile.get("resources", []):
            root = ROOT / valid_relative(root_name)
            for parent, dirs, names in os.walk(root):
                # Map data is large and intentionally opt-in.
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                                 and not (Path(parent).relative_to(ROOT) == Path("modules/map") and d == "data"))
                rel_parent = Path(parent).relative_to(ROOT)
                runtime_dir = bool(set(rel_parent.parts) & {"conf", "dag", "launch", "data", "params", "models"})
                for name in sorted(names):
                    source = Path(parent) / name
                    if not source.is_file() or source.suffix in SOURCE_SUFFIXES or name.startswith("test_"):
                        continue
                    if name in {"BUILD", "BUILD.bazel", "cyberfile.xml"}:
                        continue
                    if runtime_dir or name.endswith(".xml"):
                        self.copy(source, rel_parent / name)
        for item in self.args.include:
            source_name, sep, destination = item.partition("=")
            source = Path(source_name)
            if not source.is_absolute():
                source = ROOT / source
            source = source.resolve(strict=True)
            if not sep:
                try:
                    destination = str(source.relative_to(ROOT))
                except ValueError:
                    raise ValueError("External --include requires SOURCE=PACKAGE_PATH")
            dest = valid_relative(destination)
            if source.is_dir():
                for parent, dirs, names in os.walk(source):
                    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
                    for name in sorted(names):
                        path = Path(parent) / name
                        self.copy(path, dest / path.relative_to(source))
            else:
                self.copy(source, dest)

    def python_runtimes(self, targets):
        for target, runtime in self.profile.get('python_runtimes', {}).items():
            if target not in targets:
                continue
            destination = valid_relative(runtime['path'])
            source = self.work / destination
            requirements = ROOT / valid_relative(runtime['requirements'])
            log(f"Collecting pinned Python runtime for {target}")
            self.run([sys.executable, '-m', 'pip', 'install',
                      '--disable-pip-version-check', '--only-binary=:all:', '--no-compile',
                      '--target', str(source), '--requirement', str(requirements)])
            (source / 'runtime.json').write_text(json.dumps({
                'requirements': runtime['requirements'], 'requirements_sha256': digest(requirements),
                'packages': {item.metadata['Name']: item.version
                             for item in importlib.metadata.distributions(path=[str(source)])},
            }, indent=2) + '\n')
            for path in sorted(source.rglob('*')):
                if path.is_file() and 'bin' not in path.relative_to(source).parts:
                    self.copy(path, destination / path.relative_to(source))

    def relocate_resources(self):
        # Rewrite only text runtime paths; model and protobuf binaries stay intact.
        for path in list(self.stage.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in {".txt", ".conf", ".dag", ".launch", ".xml", ".json", ".yaml", ".yml"}:
                try:
                    text = path.read_text()
                except UnicodeDecodeError:
                    continue
                text = text.replace("/opt/apollo/neo/share/", "")
                for prefix in (str(ROOT) + "/", "/apollo_workspace/"):
                    text = text.replace(prefix, "")
                # /apollo/... also names Cyber channels. Rewrite filesystem
                # roots only; never change /apollo/planning or /bag/apollo/...
                for directory in ("modules", "cyber", "data", "profiles"):
                    text = text.replace(f"/apollo/{directory}/", f"{directory}/")
                text = text.replace("/apollo/bazel-bin/", "bazel-bin/")
                for original, destination in sorted(self.output_map.items(), key=lambda item: len(item[0]), reverse=True):
                    match = re.match(r"bazel-out/[^/]+/bin/(.*)", original)
                    if match:
                        text = text.replace("bazel-bin/" + match.group(1), destination)
                if "bazel-bin/" in text:
                    raise RuntimeError(f"Runtime resource references an unpackaged Bazel output: {path}")
                path.write_text(text)

    def python_binary(self, value):
        source = ROOT / value
        if not source.is_file() or elf(source):
            return
        if not os.access(source, os.X_OK):
            return
        rel = re.sub(r"^bazel-out/[^/]+/bin/", "", value)
        destination = self.stage / "bin" / source.name
        if destination.exists() and digest(destination) != digest(source):
            destination = self.stage / "bin" / ("__".join(Path(rel).parts))
        destination = self.copy(source, destination.relative_to(self.stage))
        self.output_map[value] = str(destination.relative_to(self.stage))
        # Bazel Python launchers require runfiles, including non-code data.
        runfiles = Path(str(source) + ".runfiles")
        if runfiles.is_dir():
            for parent, dirs, names in os.walk(runfiles, followlinks=True):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for name in names:
                    path = Path(parent) / name
                    if path.is_file():
                        target = Path("bin") / (destination.name + ".runfiles") / path.relative_to(runfiles)
                        copied = self.copy(path, target)
                        if elf(copied):
                            self.elf_files.add(copied)

    def relocate(self):
        log(f"Relocating {len(self.elf_files)} ELF files")
        for index, path in enumerate(sorted(self.elf_files), 1):
            original = Path(self.origins[str(path.relative_to(self.stage))])
            dynamic = self.dynamic(original)
            if "There is no dynamic section" in dynamic:
                continue
            needed_patch = [self.patchelf]
            for needed in re.findall(r"\(NEEDED\).*\[(.+)\]", dynamic):
                name = Path(needed).name
                replacement = self.link_maps[original].get(name, name)
                if "/" in needed and replacement not in self.libraries and not (
                        SYSTEM_LIBS.fullmatch(name) or DRIVER_LIBS.fullmatch(name)):
                    raise RuntimeError(f"Absolute ELF dependency was not bundled: {needed}")
                if replacement != needed:
                    needed_patch.extend(["--replace-needed", needed, replacement])
            # One rewrite prevents old patchelf from adding one PT_LOAD for
            # every renamed dependency. Keep SONAME/RPATH changes separate.
            if len(needed_patch) > 1:
                subprocess.run([*needed_patch, path], check=True)
            soname = re.search(r"\(SONAME\).*\[(.+)\]", dynamic)
            if path.parent == self.stage / "lib/runtime":
                replacement = path.name
            elif soname:
                replacement = self.runtime_name(soname.group(1), original)
            else:
                replacement = None
            if replacement and (not soname or replacement != soname.group(1)):
                subprocess.run([self.patchelf, "--set-soname", replacement, path], check=True)
            origins = [path.parent]
            if path.is_relative_to(self.stage / "lib/modules"):
                origins.append((self.stage / path.relative_to(self.stage / "lib")).parent)
            rpath = ":".join(dict.fromkeys(["$ORIGIN"] + [
                "$ORIGIN/" + os.path.relpath(self.stage / "lib/runtime", origin) for origin in origins]))
            subprocess.run([self.patchelf, "--set-rpath", rpath, path], check=True,
                           stdout=subprocess.DEVNULL)
            if index % 250 == 0:
                log(f"Relocated {index}/{len(self.elf_files)} ELF files")
        # Source-style component paths and installed-style lib paths both work.
        for path in sorted((self.stage / "lib/modules").rglob("*.so")):
            rel = path.relative_to(self.stage / "lib")
            relative_link(self.stage / rel, path)
        (self.stage / "share/cyber_plugin_index").mkdir(parents=True, exist_ok=True)
        for index in (self.stage / "share/cyber_plugin_index").iterdir():
            description = self.stage / valid_relative(index.read_text().strip())
            tree = ET.parse(description)
            element = tree.getroot()
            # The generated index binds the BUILD's plugin label. A few legacy
            # XML files contain a stale directory; use that authoritative label.
            expected = self.plugin_bindings[index.read_text().strip()]
            if element.attrib["path"] != expected:
                log(f"Correcting packaged plugin path: {element.attrib['path']} -> {expected}")
                element.set("path", expected)
                tree.write(description, encoding="unicode")
            library = self.stage / "lib" / valid_relative(element.attrib["path"])
            if not library.is_file():
                raise RuntimeError(f"Plugin index {index.name} references missing library {library}")
        for path in self.stage.rglob("*"):
            if path.is_symlink() and (not path.exists() or not path.resolve().is_relative_to(self.stage)):
                raise RuntimeError(f"Nonportable or broken symlink: {path}")

    def finish(self, targets, outputs):
        if (self.stage / "bazel-bin").exists() or (self.stage / "bazel-bin").is_symlink():
            raise RuntimeError("Runtime packages must not contain bazel-bin")
        for folder in ("data/log", "data/core", "data/bag", "data/simulation"):
            (self.stage / folder).mkdir(parents=True, exist_ok=True)
        (self.stage / "lib/runtime").mkdir(parents=True, exist_ok=True)
        (self.stage / "setup.bash").write_text(self.setup_script())
        manifest = {
            "format": 4, "profile": self.args.profile, "config": self.args.config,
            "layout": {"executables": "bin", "module_libraries": "lib/modules",
                       "runtime_libraries": "lib/runtime"},
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "architecture": platform.machine(), "os_release": Path("/etc/os-release").read_text(),
            "cpu_requirements": self.profile.get("x86_cpu_requirements", []) if platform.machine() == "x86_64" else [],
            "build_image": os.environ.get("GEN_BINARY_IMAGE", "unspecified"),
            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "targets": targets, "bazel_options": self.options, "outputs": self.output_map,
            "runtime_dependencies": self.profile.get("runtime_dependencies", {}),
            "python_runtimes": self.profile.get("python_runtimes", {}),
            "system_libraries": sorted(self.system), "host_driver_libraries": sorted(self.drivers),
            "library_sources": {name: {"selected": str(source),
                "observed": sorted(map(str, self.candidates.get(name, {source})))}
                for name, source in self.libraries.items() if name not in self.image_libraries},
            "image_libraries": {name: {"path": str(source), "sha256": digest(source),
                "required_symbols": self.image_requirements.get(name, []),
                "require_exact": name in self.profile.get("image_libraries", []),
                "observed": sorted(map(str, self.candidates.get(name, {source})))}
                for name, source in self.image_libraries.items()},
            "image_library_dirs": self.image_library_dirs(),
            "image_library_baseline": self.profile.get("image_library_baseline"),
            "library_compatibility": self.compatibility,
            "private_library_names": sorted(self.private_library_names),
            "plugin_bindings": self.plugin_bindings,
            "files": self.origins,
        }
        (self.stage / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        (self.stage / "README.txt").write_text(
            "Apollo binary runtime\n\ncd <extracted binary directory>\nsource setup.bash\n"
            "cyber_recorder --help\nsimulator_main --task_dir=<task directory>\n\n"
            "Use the same architecture and Ubuntu ABI as manifest.json. Map/record inputs are opt-in.\n"
            "For GPU modules install the host NVIDIA driver and start Docker with --gpus all.\n"
            "Verify files: sha256sum -c SHA256SUMS\n")
        lines = []
        identical_elf = {}
        saved_bytes = 0
        for path in sorted(self.stage.rglob("*")):
            if path.is_file() and not path.is_symlink():
                checksum = digest(path)
                if path in self.elf_files:
                    key = (checksum, path.stat().st_mode)
                    if key in identical_elf:
                        saved_bytes += path.stat().st_size
                        path.unlink()
                        os.link(identical_elf[key], path)
                    else:
                        identical_elf[key] = path
                lines.append(f"{checksum}  {path.relative_to(self.stage)}\n")
        log(f"Deduplicated identical ELF files: saved {saved_bytes / 1024**2:.1f} MiB")
        (self.stage / "SHA256SUMS").write_text("".join(lines))
        log("Checking relocated dependencies")
        clean_env = dict(os.environ, LD_LIBRARY_PATH=":".join(
            [str(self.stage / "lib/runtime")] + self.image_library_dirs()))
        with ThreadPoolExecutor(max_workers=min(self.args.jobs, 8)) as pool:
            def check(path):
                result = subprocess.run(["ldd", path], env=clean_env, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for line in result.stdout.splitlines():
                    if "not found" in line and not DRIVER_LIBS.fullmatch(line.split()[0]):
                        raise RuntimeError(f"Relocated dependency missing: {path}: {line}")
                    match = re.match(r"\s*(\S+)\s+=>\s+(/.+?)\s+\(0x", line)
                    if match and not Path(match.group(2)).resolve().is_relative_to(self.stage):
                        expected = self.image_libraries.get(match.group(1))
                        if expected and Path(match.group(2)).resolve() == expected:
                            continue
                        if not SYSTEM_LIBS.fullmatch(match.group(1)) and not DRIVER_LIBS.fullmatch(match.group(1)):
                            raise RuntimeError(f"Relocated dependency escaped package: {path}: {line}")
            list(pool.map(check, sorted(self.elf_files)))
        log(f"Writing {self.args.output}")
        temporary = self.work / "binary.tar.gz"
        with ParallelGzip(temporary, workers=min(self.args.jobs, 8), level=self.args.compress_level) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                archive.add(self.stage, arcname="binary")
        os.replace(temporary, self.args.output)
        self.args.output.with_suffix(self.args.output.suffix + ".sha256").write_text(
            f"{digest(self.args.output)}  {self.args.output.name}\n")
        log(f"Ready: {self.args.output} ({self.args.output.stat().st_size / 1024**2:.1f} MiB)")

    def build(self):
        expression = target_expression(self.profile["patterns"], self.profile.get("exclude_regex"))
        targets = self.args.target or sorted(set(self.bazel("query", expression, "--output=label", capture=True).splitlines()))
        if not targets:
            raise RuntimeError("Profile selected no build targets")
        self.args.output.with_suffix(self.args.output.suffix + ".targets.txt").write_text("\n".join(targets) + "\n")
        log(f"Profile {self.args.profile}: {len(targets)} targets")
        runtime_targets = sorted({dependency for target in targets
            for dependency in self.profile.get("runtime_dependencies", {}).get(target, [])})
        self.bazel("build", *targets, *runtime_targets)
        target_set = "set(" + " ".join(targets) + ")"
        outputs = sorted(set(self.bazel("cquery", target_set, "--output=files", capture=True).splitlines()))
        for value in outputs:
            if not (ROOT / value).is_file():
                raise RuntimeError(f"Selected build output is missing: {value}")
        # Use the configured dependency graph, excluding build-time tools.
        closure = sorted(set(self.bazel("cquery", f"deps({target_set})", "--notool_deps", "--output=files", capture=True).splitlines()))
        for value in sorted(set(outputs + closure)):
            match = re.match(r"bazel-out/[^/]+/bin/((?:cyber|modules)/.*)", value)
            if match and elf(ROOT / value):
                self.current_outputs[match.group(1)] = (ROOT / value).resolve()
        # The selected roots include dlopen plugins. Linker dependencies are
        # resolved from those roots, rather than shipping unused DefaultInfo
        # outputs of wrapper rules that can live in a different repository ABI.
        for value in outputs:
            self.collect_output(value)
        if runtime_targets:
            runtime_set = "set(" + " ".join(runtime_targets) + ")"
            # --output=files omits source artifacts in Bazel 5; data filegroups
            # can contain checked-in layouts as well as generated executables.
            runtime_outputs = self.bazel("cquery", runtime_set, "--output=starlark",
                '--starlark:expr="\\n".join([file.path for file in target.files.to_list()])',
                capture=True).splitlines()
            for value in sorted(set(runtime_outputs)):
                if not (ROOT / value).is_file():
                    raise RuntimeError(f"Runtime dependency output is missing: {value}")
                self.collect_output(value, runtime_data=True)
            # A schema's generated Python imports other generated schemas.
            # filegroup DefaultInfo includes its own files, not that closure.
            schema_outputs = self.bazel("cquery", f"deps({runtime_set})", "--notool_deps",
                "--output=files", capture=True).splitlines()
            for value in sorted(set(schema_outputs)):
                if value.endswith("_pb2.py") and re.match(r"bazel-out/[^/]+/bin/(?:external/apollo_src/)?(?:cyber|modules)/", value):
                    self.collect_output(value, runtime_data=True)
        for value in closure:
            if re.match(r"bazel-out/[^/]+/bin/cyber/python/internal/_[^/]+\.so$", value):
                self.collect_output(value)
        for value in outputs:
            self.python_binary(value)
        self.resources()
        self.python_runtimes(targets)
        self.relocate_resources()
        self.unify_collected_libraries()
        sources = {Path(self.origins[str(path.relative_to(self.stage))]) for path in self.elf_files}
        self.dependencies(sources)
        self.check_library_compatibility()
        self.remove_image_copies()
        self.relocate()
        self.finish(targets, outputs)


SETUP = '''# Source this file from Bash. The package may be extracted anywhere.
export APOLLO_ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export APOLLO_DISTRIBUTION_HOME="$APOLLO_ROOT_DIR"
export APOLLO_PATH="$APOLLO_ROOT_DIR"
export APOLLO_ENV_WORKROOT="$APOLLO_ROOT_DIR"
export APOLLO_WORKSPACE="$APOLLO_ROOT_DIR"
export APOLLO_RUNTIME_PATH="$APOLLO_ROOT_DIR"
export CYBER_PATH="$APOLLO_ROOT_DIR/cyber"
export CYBER_DOMAIN_ID="${CYBER_DOMAIN_ID:-80}"
export CYBER_IP="${CYBER_IP:-127.0.0.1}"
export APOLLO_LIB_PATH="$APOLLO_ROOT_DIR/lib"
export APOLLO_PLUGIN_LIB_PATH="$APOLLO_LIB_PATH"
export APOLLO_PLUGIN_DESCRIPTION_PATH="$APOLLO_ROOT_DIR"
export APOLLO_PLUGIN_INDEX_PATH="$APOLLO_ROOT_DIR/share/cyber_plugin_index"
export APOLLO_PLUGIN_SEARCH_IN_BAZEL_OUTPUT=0
export APOLLO_MODEL_PATH="$APOLLO_ROOT_DIR/modules/perception/data/models"
export APOLLO_CONF_PATH="$APOLLO_ROOT_DIR"
export APOLLO_FLAG_PATH="$APOLLO_ROOT_DIR"
export APOLLO_DAG_PATH="$APOLLO_ROOT_DIR"
export APOLLO_LAUNCH_PATH="$APOLLO_ROOT_DIR"
export PATH="$APOLLO_ROOT_DIR/modules/simulation/apps:$APOLLO_ROOT_DIR/bin${PATH:+:$PATH}"
export LD_LIBRARY_PATH="$APOLLO_ROOT_DIR/lib/runtime:/usr/local/nvidia/lib:/usr/local/nvidia/lib64"
export PYTHONPATH="$APOLLO_ROOT_DIR/lib/cyber/python/internal:$APOLLO_ROOT_DIR/python:$APOLLO_ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export WEB_MONITOR_SIM_SERVICE="$APOLLO_ROOT_DIR/modules/simulation/simulator/task_service.py"
export WEB_MONITOR_RECORD_TOOLS="$APOLLO_ROOT_DIR/modules/simulation/tools/apollo_record_tools"
export WEB_MONITOR_RECORD_TOOL="$APOLLO_ROOT_DIR/bin/apollo_record_tool"
export WEB_MONITOR_CONVERT_CACHE="$APOLLO_ROOT_DIR/data/bag/.wm_mcap_cache"
export SIMULATOR_BINARY="$APOLLO_ROOT_DIR/bin/simulator_main"
export GLOG_log_dir="$APOLLO_ROOT_DIR/data/log"
export GLOG_alsologtostderr="${GLOG_alsologtostderr:-0}"
mkdir -p "$GLOG_log_dir" "$APOLLO_ROOT_DIR/data/core" "$APOLLO_ROOT_DIR/data/bag"
cd -- "$APOLLO_ROOT_DIR"
'''
