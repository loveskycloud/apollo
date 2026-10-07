"""Regression tests for packaging failure modes; no Apollo build required."""
import argparse
import importlib.util
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("binary_packager", Path(__file__).with_name("binary_packager.py"))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        args = argparse.Namespace(config="cpu", jobs=2, bazel_arg=[],
                                  include=[], output=self.work / "binary.tar.gz")
        self.builder = package.Builder(args, {}, self.work)

    def tearDown(self):
        self.tmp.cleanup()

    def test_materializes_absolute_build_symlinks(self):
        original = self.work / "original.so"
        original.write_bytes(b"library bytes")
        link = self.work / "external.so"
        link.symlink_to(original)
        dest = self.builder.copy(link, "lib/runtime/libexample.so")
        self.assertFalse(dest.is_symlink())
        original.unlink()
        self.assertEqual(dest.read_bytes(), b"library bytes")

    def test_different_linker_libraries_with_same_name_fail(self):
        first, second = self.work / "one.so", self.work / "two.so"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        self.builder.add_library(first, "libexample.so")
        with self.assertRaisesRegex(RuntimeError, "same runtime name"):
            self.builder.add_library(second, "libexample.so")

    def test_same_library_via_two_aliases_is_accepted(self):
        first, second = self.work / "one.so", self.work / "two.so"
        first.write_bytes(b"same bytes")
        second.write_bytes(b"same bytes")
        self.builder.add_library(first, "libexample.so")
        self.builder.add_library(second, "libexample.so")
        self.assertEqual(len(self.builder.libraries), 1)

    def test_declared_image_library_is_not_bundled(self):
        library = self.work / "image/libexample.so"
        library.parent.mkdir()
        library.write_bytes(b"image-owned bytes")
        self.builder.profile["image_libraries"] = ["libexample.so"]
        self.assertIsNone(self.builder.add_library(library))
        self.assertEqual(self.builder.image_libraries["libexample.so"], library)
        self.assertFalse((self.builder.stage / "lib/runtime/libexample.so").exists())
        self.assertIn(str(library.parent), self.builder.setup_script())

    def test_runtime_data_includes_external_viewer_and_layouts(self):
        from unittest.mock import patch
        viewer = "bazel-out/k8-opt/bin/modules/viewer/bin/rerun"
        layout = "modules/viewer/layouts/planning.rbl"
        for relative in (viewer, layout):
            path = self.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shutil.which("true"), self.work / viewer)
        (self.work / layout).write_bytes(b"layout bytes")
        with patch.object(package, "ROOT", self.work):
            self.builder.collect_output(viewer, runtime_data=True)
            self.builder.collect_output(layout, runtime_data=True)
        self.assertTrue(package.elf(self.builder.stage / "bin/rerun"))
        self.assertIn(self.builder.stage / "bin/rerun", self.builder.elf_files)
        self.assertEqual((self.builder.stage / layout).read_bytes(), b"layout bytes")
        self.assertFalse((self.builder.stage / "modules/viewer/bin").exists())

    def test_generated_schema_imports_survive_without_external_build_tree(self):
        from unittest.mock import patch
        outputs = {
            "bazel-out/k8-opt/bin/external/apollo_src/modules/fixture/proto/base_pb2.py": "VALUE = 42\n",
            "bazel-out/k8-opt/bin/modules/fixture/proto/result_pb2.py": "from modules.fixture.proto.base_pb2 import VALUE\nRESULT = VALUE + 1\n",
        }
        for relative, content in outputs.items():
            path = self.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        with patch.object(package, "ROOT", self.work):
            for relative in outputs:
                self.builder.collect_output(relative, runtime_data=True)
        shutil.rmtree(self.work / "bazel-out")
        result = subprocess.check_output([sys.executable, "-c",
            "from modules.fixture.proto.result_pb2 import RESULT; print(RESULT)"], cwd="/tmp",
            env={"PATH": os.environ["PATH"], "PYTHONPATH": str(self.builder.stage / "python")}, text=True)
        self.assertEqual(result.strip(), "43")
        self.assertFalse((self.builder.stage / "external").exists())

    def test_identical_generated_copy_uses_image_provider(self):
        library, generated = self.work / "image/libexample.so", self.work / "generated/libexample.so"
        for path in (library, generated):
            path.parent.mkdir()
            path.write_bytes(b"same bytes")
        self.builder.profile["system_library_roots"] = [str(library.parent)]
        self.builder.profile["system_library_paths"] = {"libexample.so": str(library)}
        bundled = self.builder.add_library(generated)
        self.assertTrue(bundled.is_file())
        self.assertIsNone(self.builder.add_library(library))
        self.assertFalse(bundled.exists())
        self.assertEqual(self.builder.image_libraries["libexample.so"], library)

    def test_developer_installed_system_library_is_bundled(self):
        library = self.work / "system/libextra.so"
        library.parent.mkdir()
        library.write_bytes(b"installed only in developer container")
        self.builder.profile["system_library_roots"] = [str(library.parent)]
        self.builder.profile["system_library_paths"] = {"libother.so": str(library.parent / "libother.so")}
        bundled = self.builder.add_library(library)
        self.assertEqual(bundled.read_bytes(), library.read_bytes())
        self.assertNotIn("libextra.so", self.builder.image_libraries)

    def test_missing_or_changed_image_library_is_rejected(self):
        spec = importlib.util.spec_from_file_location("verify_binary", Path(__file__).with_name("verify_binary.py"))
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        library = self.work / "libexample.so"
        manifest = {"image_libraries": {"libexample.so": {"path": str(library), "sha256": "unused"}}}
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            verifier.verify_image_libraries(manifest)
        library.write_bytes(b"one version")
        manifest["image_libraries"]["libexample.so"]["sha256"] = package.digest(library)
        verifier.verify_image_libraries(manifest)
        library.write_bytes(b"different version")
        with self.assertRaisesRegex(RuntimeError, "different libexample"):
            verifier.verify_image_libraries(manifest)

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("readelf"), "needs gcc and readelf")
    def test_system_library_updates_require_used_symbol_versions(self):
        spec = importlib.util.spec_from_file_location("verify_binary", Path(__file__).with_name("verify_binary.py"))
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        library = self.work / "libexample.so"
        source, versions = self.work / "example.c", self.work / "versions.map"
        def build(version, extra=""):
            source.write_text("int answer(void) { return 42; }\n" + extra)
            versions.write_text(version + " { global: answer; };\n")
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libexample.so",
                            "-Wl,--version-script=" + str(versions), source, "-o", library], check=True)
        build("EXAMPLE_1")
        requirement = {"path": str(library), "sha256": package.digest(library),
                       "required_symbols": ["answer@EXAMPLE_1"], "require_exact": False}
        manifest = {"image_libraries": {"libexample.so": requirement}}
        self.assertEqual(verifier.verify_image_libraries(manifest)["sha256_matches"], ["libexample.so"])
        build("EXAMPLE_1", "int additional(void) { return 7; }")
        self.assertEqual(verifier.verify_image_libraries(manifest)["abi_compatible"], ["libexample.so"])
        requirement["require_exact"] = True
        with self.assertRaisesRegex(RuntimeError, "different libexample"):
            verifier.verify_image_libraries(manifest)
        requirement["require_exact"] = False
        build("EXAMPLE_2")
        with self.assertRaisesRegex(RuntimeError, "lacks required ELF symbols/versions"):
            verifier.verify_image_libraries(manifest)

    def test_different_module_libraries_get_unique_runtime_names(self):
        one = self.work / "bazel-out/k8-opt/bin/modules/one/libexample.so"
        two = self.work / "bazel-out/k8-opt/bin/modules/two/libexample.so"
        one.parent.mkdir(parents=True)
        two.parent.mkdir(parents=True)
        one.write_bytes(b"version one")
        two.write_bytes(b"version two")
        self.builder.classify_private_libraries({"libexample.so": {one, two}})
        a = self.builder.add_library(one, "libexample.so")
        b = self.builder.add_library(two, "libexample.so")
        self.assertNotEqual(a, b)
        self.assertEqual(a.read_bytes(), b"version one")
        self.assertEqual(b.read_bytes(), b"version two")
        self.assertEqual(a.parent, b.parent)
        self.assertEqual(a.parent, self.builder.stage / "lib/runtime")

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("patchelf"), "needs gcc and patchelf")
    def test_explicit_single_library_selection_runs_after_originals_are_removed(self):
        sources = set()
        original = self.work / "build"
        original.mkdir()
        unique_source = original / "unique.c"
        unique_source.write_text("int unique(void) { return 40; }")
        subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libunique.so", unique_source,
                        "-o", original / "libunique.so"], check=True)
        middle_one = None
        for number in (1, 2):
            folder = original / str(number)
            folder.mkdir(parents=True)
            library = folder / "libanswer.so"
            source = folder / "answer.c"
            source.write_text(f"int answer(void) {{ return {number}; }}")
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libanswer.so", source, "-o", library], check=True)
            middle = folder / "libmiddle.so"
            if middle_one is None:
                source.write_text("int answer(void); int unique(void); int middle(void) { return answer() + unique(); }")
                subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libmiddle.so", source,
                                "-L" + str(folder), "-lanswer", "-L" + str(original), "-lunique",
                                "-Wl,-rpath,$ORIGIN:$ORIGIN/..", "-o", middle], check=True)
                middle_one = middle
            else:
                # Identical ELF bytes can resolve different dependencies through
                # their original $ORIGIN directories. Preserve that distinction.
                shutil.copy2(middle_one, middle)
            source.write_text('#include <stdio.h>\nint middle(void); int main(void) { printf("%d", middle()); return 0; }')
            program = folder / f"program{number}"
            subprocess.run(["gcc", source, "-L" + str(folder), "-lmiddle", "-Wl,-rpath," + str(folder),
                            "-Wl,-rpath-link," + str(folder), "-o", program], check=True)
            copied = self.builder.copy(program, "bin/" + program.name)
            self.builder.elf_files.add(copied)
            sources.add(program)
        self.builder.profile["library_overrides"] = {"libanswer.so": str(original / "1/libanswer.so")}
        self.builder.dependencies(sources)
        self.builder.check_library_compatibility()
        self.builder.relocate()
        shutil.rmtree(original)
        moved = self.work / "moved package"
        self.builder.stage.rename(moved)
        self.assertFalse((moved / "bazel-bin").exists())
        self.assertFalse((moved / "lib/runtime/.staging").exists())
        self.assertTrue((moved / "lib/runtime/libunique.so").is_file())
        self.assertTrue((moved / "lib/runtime/libanswer.so").is_file())
        self.assertFalse(any(path.is_dir() for path in (moved / "lib/runtime").iterdir()))
        self.assertFalse(any(path.is_dir() and not any(path.iterdir())
                             for path in (moved / "lib/runtime").rglob("*")))
        env = {"PATH": os.environ["PATH"], "LD_LIBRARY_PATH": str(moved / "lib/runtime")}
        for number in (1, 2):
            result = subprocess.check_output([moved / f"bin/program{number}"], text=True, env=env)
            self.assertEqual(result, "41")

    @unittest.skipUnless(shutil.which("gcc"), "needs gcc")
    def test_identical_library_bytes_with_different_origin_dependencies_fail(self):
        first = None
        for number in (1, 2):
            folder = self.work / str(number)
            folder.mkdir()
            source = folder / "answer.c"
            source.write_text(f"int answer(void) {{ return {number}; }}")
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libanswer.so", source,
                            "-o", folder / "libanswer.so"], check=True)
            middle = folder / "libmiddle.so"
            if first is None:
                source.write_text("int answer(void); int middle(void) { return answer(); }")
                subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libmiddle.so", source,
                                "-L" + str(folder), "-lanswer", "-Wl,-rpath,$ORIGIN", "-o", middle], check=True)
                first = middle
                self.builder.add_library(middle)
            else:
                shutil.copy2(first, middle)
                with self.assertRaisesRegex(RuntimeError, "resolve different dependencies"):
                    self.builder.add_library(middle)

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("patchelf"), "needs gcc and patchelf")
    def test_private_libraries_with_same_basename_work_in_one_process(self):
        import ctypes
        roots = set()
        original = self.work / "bazel-out/k8-opt/bin/modules"
        for number in (1, 2):
            folder = original / f"module{number}"
            folder.mkdir(parents=True)
            source = folder / "helper.c"
            source.write_text(f"int answer{number}(void) {{ return {number}; }}")
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libanswer.so", source,
                            "-o", folder / "libanswer.so"], check=True)
            source.write_text(f"int answer{number}(void); int client{number}(void) {{ return answer{number}(); }}")
            client = folder / "libclient.so"
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libclient.so", source,
                            "-L" + str(folder), "-lanswer", "-Wl,-rpath,$ORIGIN", "-o", client], check=True)
            self.builder.copy(client, f"lib/modules/module{number}/libclient.so")
            roots.add(client)
        self.builder.dependencies(roots)
        self.builder.relocate()
        shutil.rmtree(original)
        moved = self.work / "moved package"
        self.builder.stage.rename(moved)
        # Cyber dlopen uses RTLD_GLOBAL. Distinct private modules export their
        # own API names (e.g. lidar/radar4d C++ namespaces), unlike ABI versions
        # that reuse the same public symbols.
        handles = [ctypes.CDLL(str(moved / f"lib/modules/module{number}/libclient.so"),
                               mode=ctypes.RTLD_GLOBAL) for number in (1, 2)]
        self.assertEqual([getattr(handle, f"client{number}")()
                          for number, handle in enumerate(handles, 1)], [1, 2])

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("patchelf"), "needs gcc and patchelf")
    def test_many_private_dependencies_remain_directly_executable(self):
        roots = set()
        original = self.work / "bazel-out/k8-opt/bin/modules"
        for number in (1, 2):
            folder = original / f"module{number}"
            folder.mkdir(parents=True)
            source = folder / "helper.c"
            for index in range(70):
                name = f"libhelper{index}.so"
                source.write_text(f"int answer{index}(void) {{ return {number}; }}")
                subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname," + name,
                                source, "-o", folder / name], check=True)
            source.write_text('#include <stdio.h>\n' +
                "\n".join(f"int answer{index}(void);" for index in range(70)) +
                '\nint main(void) { printf("%d", ' +
                "+".join(f"answer{index}()" for index in range(70)) + "); return 0; }")
            program = folder / f"program{number}"
            subprocess.run(["gcc", source, "-L" + str(folder),
                *[f"-lhelper{index}" for index in range(70)],
                "-Wl,-rpath,$ORIGIN", "-o", program], check=True)
            self.builder.copy(program, f"bin/program{number}")
            roots.add(program)
        self.builder.dependencies(roots)
        self.builder.relocate()
        shutil.rmtree(original)
        for number in (1, 2):
            self.assertEqual(subprocess.check_output(
                [self.builder.stage / f"bin/program{number}"], text=True), str(number * 70))

    @unittest.skipUnless(shutil.which("gcc"), "needs gcc")
    def test_incompatible_explicit_library_selection_is_rejected(self):
        roots = set()
        for number in (1, 2):
            folder = self.work / str(number)
            folder.mkdir()
            source = folder / "answer.c"
            source.write_text(f"int answer{number}(void) {{ return {number}; }}")
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libanswer.so", source,
                            "-o", folder / "libanswer.so"], check=True)
            source.write_text(f"int answer{number}(void); int main(void) {{ return answer{number}(); }}")
            program = folder / f"program{number}"
            subprocess.run(["gcc", source, "-L" + str(folder), "-lanswer", "-Wl,-rpath," + str(folder),
                            "-o", program], check=True)
            self.builder.copy(program, f"bin/program{number}")
            roots.add(program)
        self.builder.profile["library_overrides"] = {"libanswer.so": str(self.work / "1/libanswer.so")}
        self.builder.dependencies(roots)
        with self.assertRaisesRegex(RuntimeError, "lacks required ELF symbols"):
            self.builder.check_library_compatibility()

    def test_native_entry_has_only_bin_and_no_build_tree_alias(self):
        from unittest.mock import patch
        original = "bazel-out/k8-opt/bin/modules/foo/program"
        source = self.work / original
        source.parent.mkdir(parents=True)
        source.write_bytes(b"\x7fELFprogram")
        with patch.object(package, "ROOT", self.work), patch.object(package.subprocess, "check_output", return_value=""):
            self.builder.collect_output(original)
        self.assertTrue((self.builder.stage / "bin/program").is_file())
        self.assertFalse((self.builder.stage / "bazel-bin").exists())
        self.assertEqual(self.builder.output_map[original], "bin/program")

    def test_python_entry_has_adjacent_runfiles_without_build_tree_alias(self):
        from unittest.mock import patch
        original = "bazel-out/k8-opt/bin/modules/foo/program"
        source = self.work / original
        source.parent.mkdir(parents=True)
        source.write_text("#!/usr/bin/env python3\nprint('program')\n")
        source.chmod(0o755)
        runfile = Path(str(source) + ".runfiles") / "__main__/modules/foo/data.txt"
        runfile.parent.mkdir(parents=True)
        runfile.write_text("data")
        with patch.object(package, "ROOT", self.work):
            self.builder.python_binary(original)
        self.assertTrue((self.builder.stage / "bin/program").is_file())
        self.assertEqual((self.builder.stage / "bin/program.runfiles/__main__/modules/foo/data.txt").read_text(), "data")
        self.assertFalse((self.builder.stage / "bazel-bin").exists())
        self.assertEqual(self.builder.output_map[original], "bin/program")

    def test_launch_build_path_is_rewritten_to_packaged_binary(self):
        from unittest.mock import patch
        source = self.work / "source"
        launch = source / "modules/foo/launch/foo.launch"
        launch.parent.mkdir(parents=True)
        launch.write_text('<module name="/apollo/bazel-bin/modules/foo/program --flagfile=modules/foo/conf/foo.conf"/>')
        self.builder.profile = {"resources": ["modules/foo"]}
        self.builder.output_map["bazel-out/k8-opt/bin/modules/foo/program"] = "bin/program"
        with patch.object(package, "ROOT", source):
            self.builder.resources()
            self.builder.relocate_resources()
        text = (self.builder.stage / "modules/foo/launch/foo.launch").read_text()
        self.assertIn('name="bin/program --flagfile=', text)
        self.assertNotIn("bazel-bin", text)

    def test_resource_collision_fails(self):
        a, b = self.work / "a", self.work / "b"
        a.write_text("first")
        b.write_text("second")
        self.builder.copy(a, "conf/example.txt")
        with self.assertRaisesRegex(RuntimeError, "Conflicting files"):
            self.builder.copy(b, "conf/example.txt")

    def test_filesystem_path_rewrite_preserves_cyber_channel_names(self):
        from unittest.mock import patch
        source = self.work / "source"
        conf = source / "modules/foo/conf/example.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text('--path=/apollo/modules/foo/conf/input.txt\n'
                        'topic: "/apollo/planning"\noriginal: "/bag/apollo/control"\n')
        self.builder.profile = {"resources": ["modules/foo"]}
        with patch.object(package, "ROOT", source):
            self.builder.resources()
            self.builder.relocate_resources()
        text = (self.builder.stage / "modules/foo/conf/example.conf").read_text()
        self.assertIn("--path=modules/foo/conf/input.txt", text)
        self.assertIn('"/apollo/planning"', text)
        self.assertIn('"/bag/apollo/control"', text)

    def test_archive_paths_cannot_escape(self):
        for path in ("/tmp/file", "../file", "lib/../../file"):
            with self.assertRaises(ValueError):
                package.valid_relative(path)

    def test_parallel_archive_is_readable_by_python_and_gnu_tar(self):
        archive_path = self.work / "binary.tar.gz"
        payload = bytes(range(256)) * 10000
        with package.ParallelGzip(archive_path, workers=2, block_size=512 * 1024) as stream:
            with tarfile.open(fileobj=stream, mode="w|") as archive:
                entry = tarfile.TarInfo("binary/payload.bin")
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
        with tarfile.open(archive_path, "r:gz") as archive:
            self.assertEqual(archive.extractfile("binary/payload.bin").read(), payload)
        extracted = subprocess.check_output(["tar", "-xOzf", archive_path, "binary/payload.bin"])
        self.assertEqual(extracted, payload)

    def test_profile_extension_needs_no_code_change(self):
        path = self.work / "profiles.json"
        path.write_text(json.dumps({"custom": {"patterns": ["//modules/routing:librouting_component.so"],
                                               "resources": ["modules/routing"]}}))
        self.assertIn("custom", package.load_profiles(path))

    def test_profile_path_escape_is_rejected(self):
        path = self.work / "profiles.json"
        path.write_text(json.dumps({"invalid": {"patterns": ["//cyber/..."], "resources": ["../secret"]}}))
        with self.assertRaises(ValueError):
            package.load_profiles(path)

    def test_setup_tracks_extraction_directory_with_spaces(self):
        destination = self.work / "moved package"
        destination.mkdir()
        (destination / "setup.bash").write_text(package.SETUP)
        result = subprocess.check_output(
            ["bash", "--noprofile", "--norc", "-c", 'source "$1/setup.bash"; printf "%s\\n" "$APOLLO_ROOT_DIR" "$APOLLO_PLUGIN_INDEX_PATH" "$APOLLO_WORKSPACE" "$WEB_MONITOR_SIM_SERVICE" "$SIMULATOR_BINARY" "$WEB_MONITOR_RECORD_TOOLS" "$WEB_MONITOR_RECORD_TOOL" "$WEB_MONITOR_CONVERT_CACHE"',
             "verify", str(destination)], text=True, env={"PATH": os.environ["PATH"]})
        self.assertEqual(result.splitlines(), [str(destination), str(destination / "share/cyber_plugin_index"),
            str(destination), str(destination / "modules/simulation/simulator/task_service.py"),
            str(destination / "bin/simulator_main"), str(destination / "modules/simulation/tools/apollo_record_tools"),
            str(destination / "bin/apollo_record_tool"), str(destination / "data/bag/.wm_mcap_cache")])

    def test_interrupted_build_reaps_its_child(self):
        from unittest.mock import patch
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        original_wait = child.wait
        first_wait = True
        def interrupt_once(*args, **kwargs):
            nonlocal first_wait
            if first_wait:
                first_wait = False
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)
        try:
            with patch.object(package.subprocess, 'Popen', return_value=child), patch.object(child, 'wait', side_effect=interrupt_once):
                with self.assertRaises(KeyboardInterrupt):
                    self.builder.run(['owned-build', 'compile'])
            self.assertIsNotNone(child.poll(), 'Interrupted packager left its build running')
        finally:
            if child.poll() is None:
                child.kill()
            original_wait()

    def test_links_remain_relative_after_move(self):
        target = self.builder.stage / "lib/runtime/libexample.so"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"library")
        link = self.builder.stage / "modules/example/libexample.so"
        package.relative_link(link, target)
        moved = self.work / "moved"
        self.builder.stage.rename(moved)
        self.assertEqual((moved / "modules/example/libexample.so").read_bytes(), b"library")

    def test_missing_library_is_not_ignored(self):
        from unittest.mock import patch
        result = subprocess.CompletedProcess([], 0, stdout="libmissing.so => not found\n")
        with patch.object(package.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "Unresolved dynamic dependency"):
                self.builder.ldd(self.work / "program")


if __name__ == "__main__":
    unittest.main()
