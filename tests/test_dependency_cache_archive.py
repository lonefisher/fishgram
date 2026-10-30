import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from dependency_cache_archive import pack, restore


class DependencyArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'prepared'
        self.destination = self.base / 'restored'
        self.archive = self.base / 'dependencies.zip'
        (self.root / 'Libraries/win64').mkdir(parents=True)
        (self.root / 'ThirdParty').mkdir()

    def write(self, name, data=b'dependency input'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_roundtrip_retains_build_inputs_and_omits_intermediates(self):
        retained = [
            'Libraries/win64/Qt-6.11.2/lib/Qt6Core.lib',
            'Libraries/win64/Qt-6.11.2/include/QtCore/qobject.h',
            'Libraries/win64/Qt-6.11.2/bin/moc.exe',
            'Libraries/win64/Qt-6.11.2/lib/cmake/Qt6Core/Qt6CoreConfig.cmake',
            'Libraries/win64/Qt-6.11.2/lib/objects-Release/plugin/init.obj',
            'Libraries/win64/tlottie/src/lib.rs',
            'Libraries/win64/tlottie/Cargo.toml',
            'Libraries/win64/wallet-engine/src/wallet.rs',
            'Libraries/win64/wallet-engine/bindgen/cpp/templates/impl.cpp',
            'Libraries/win64/tdesktop_rust/Cargo.lock',
            'Libraries/win64/tdesktop_rust/src/lib.rs',
            'Libraries/win64/tdesktop_rust/out/src/wallet_engine/wallet_engine.cpp',
            'Libraries/win64/tdesktop_rust/out/include/wallet_engine/wallet_engine.hpp',
            'Libraries/win64/tdesktop_rust/out/lib/tdesktop_rust.lib',
            'Libraries/win64/lzma/C/LzmaLib.h',
            'Libraries/win64/lzma/C/Util/LzmaLib/x64/Release/LzmaLib.lib',
            'Libraries/win64/openssl3/out/libcrypto.lib',
            'Libraries/win64/tg_angle/out/Release/tg_angle.lib',
            'Libraries/win64/cache_keys/qt_6.11.2',
            'Libraries/win64/patches/wallet-engine.patch',
            'ThirdParty/rust/rustup/toolchains/stable/lib/std.rlib',
            'ThirdParty/python/Lib/site-packages/six.py',
            'ThirdParty/msys64/usr/bin/sh.exe',
            'ThirdParty/msys64/usr/bin/msys-2.0.dll',
        ]
        omitted = [
            'Libraries/win64/qt_6.11.2/qtbase/CMakeFiles/core.obj',
            'Libraries/win64/qt_6.11.2/qtbase/core.pch',
            'Libraries/win64/tg_angle/out/Release/angle.pdb',
            'Libraries/win64/tde2e/build/native.o',
            'Libraries/win64/libjxl/out/jxl.ilk',
            'Libraries/win64/tlottie/.git/objects/pack/history.pack',
            'ThirdParty/gyp/.git/config',
            'ThirdParty/python/Lib/__pycache__/six.pyc',
        ]
        outside = ['build-modified/api-config.h', '.private/api-test.json',
                   'fishgram.json', 'tdesktop/Telegram/SourceFiles/api.cpp']
        originals = {name: self.write(name, name.encode()) for name in retained + omitted + outside}
        result = pack(self.root, self.archive)
        self.assertEqual(result['archive_bytes'], self.archive.stat().st_size)
        restore(self.destination, self.archive)
        for name in retained:
            self.assertEqual((self.destination / name).read_bytes(), name.encode())
        for name in omitted + outside:
            self.assertFalse((self.destination / name).exists(), name)
        for name, path in originals.items():
            self.assertEqual(path.read_bytes(), name.encode())

    def test_budget_measures_compressed_bytes_and_discards_oversize_zip(self):
        self.write('Libraries/win64/output.lib', b'compressible' * 10000)
        self.write('ThirdParty/tool.exe', b'executable')
        result = pack(self.root, self.archive, max_bytes=3000)
        self.assertGreater(result['retained_bytes'], 3000)
        self.assertLess(result['archive_bytes'], 3000)
        self.archive.unlink()
        self.write('Libraries/win64/output.lib', os.urandom(10000))
        with self.assertRaisesRegex(ValueError, 'exceeds budget'):
            pack(self.root, self.archive, max_bytes=3000)
        self.assertFalse(self.archive.exists())
        # Also cover a limit exceeded only after writing the ZIP directory.
        self.write('Libraries/win64/output.lib', b'a')
        result = pack(self.root, self.archive)
        self.archive.unlink()
        with self.assertRaisesRegex(ValueError, 'exceeds budget'):
            pack(self.root, self.archive, max_bytes=result['archive_bytes'] - 1)
        self.assertFalse(self.archive.exists())

    def test_dependency_timestamps_outside_zip_range_roundtrip(self):
        files = [('Libraries/win64/old.lib', 0),
                 ('ThirdParty/future-tool.exe', 5_000_000_000)]
        for name, stamp in files:
            path = self.write(name, name.encode())
            os.utime(path, (stamp, stamp))
        pack(self.root, self.archive)
        restore(self.destination, self.archive)
        for name, stamp in files:
            self.assertEqual((self.destination / name).read_bytes(), name.encode())
            self.assertEqual(int((self.root / name).stat().st_mtime), stamp)

    def corrupt_member(self, filename, symlink=False):
        # Start from an actual valid archive, then append the external input.
        self.write('Libraries/win64/native.lib')
        self.write('ThirdParty/tool.exe')
        pack(self.root, self.archive)
        with zipfile.ZipFile(self.archive, 'a') as bundle:
            info = zipfile.ZipInfo(filename)
            if symlink:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, 'outside')

    def test_restore_rejects_traversal_and_non_dependency_roots_before_extracting(self):
        for filename in ['../escaped', '/Libraries/evil', 'Libraries/../escaped',
                         'Libraries/win64/../../escaped', 'Libraries\\..\\escaped',
                         'Libraries/win64/file:secret', 'Libraries/win64/alias.',
                         'Libraries/win64/NUL.txt', 'Libraries/win64/CON',
                         'build-modified/config.h', '.private/api-config.h']:
            with self.subTest(filename=filename):
                self.corrupt_member(filename)
                with self.assertRaisesRegex(ValueError, 'Invalid dependency archive path'):
                    restore(self.destination, self.archive)
                self.assertFalse(self.destination.exists())
                self.archive.unlink()

    def test_restore_rejects_archive_links_and_case_aliases(self):
        self.corrupt_member('Libraries/win64/link', symlink=True)
        with self.assertRaisesRegex(ValueError, 'link or special file'):
            restore(self.destination, self.archive)
        self.assertFalse(self.destination.exists())
        self.archive.unlink()
        self.corrupt_member('Libraries/WIN64/NATIVE.LIB')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            restore(self.destination, self.archive)

    def test_restore_cannot_replace_live_dependencies(self):
        self.write('Libraries/win64/native.lib')
        self.write('ThirdParty/tool.exe')
        pack(self.root, self.archive)
        marker = self.destination / 'Libraries/keep'
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b'live dependency')
        with self.assertRaisesRegex(ValueError, 'absent dependency directories'):
            restore(self.destination, self.archive)
        self.assertEqual(marker.read_bytes(), b'live dependency')

    def test_pack_rejects_output_inside_dependencies_and_existing_archives(self):
        with self.assertRaisesRegex(ValueError, 'outside dependency directories'):
            pack(self.root, self.root / 'ThirdParty/cache.zip')
        self.archive.write_bytes(b'previous cache')
        with self.assertRaises(FileExistsError):
            pack(self.root, self.archive)
        self.assertEqual(self.archive.read_bytes(), b'previous cache')

    def file_link(self, path, target):
        try:
            path.symlink_to(target)
        except OSError as error:
            if os.name == 'nt' and error.winerror == 1314:
                self.skipTest('File symlink creation requires Windows Developer Mode or privilege.')
            raise

    def test_internal_file_links_restore_as_regular_files(self):
        self.write('Libraries/win64/native.lib')
        executable = self.write('ThirdParty/rust/cargo/bin/rustup.exe', b'rustup program bytes')
        proxy = executable.with_name('cargo.exe')
        self.file_link(proxy, 'rustup.exe')
        pack(self.root, self.archive)
        restore(self.destination, self.archive)
        restored = self.destination / 'ThirdParty/rust/cargo/bin/cargo.exe'
        self.assertFalse(restored.is_symlink())
        self.assertEqual(restored.read_bytes(), executable.read_bytes())
        self.assertTrue(proxy.is_symlink())

    def test_file_links_cannot_include_external_files(self):
        outside = self.base / 'outside.txt'
        outside.write_bytes(b'outside dependency roots')
        self.file_link(self.root / 'Libraries/win64/linked.h', outside)
        with self.assertRaisesRegex(ValueError, 'leaves dependency directories'):
            pack(self.root, self.archive)
        self.assertFalse(self.archive.exists())

    def test_pack_rejects_junctions_instead_of_reading_outside_dependencies(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'private.txt').write_bytes(b'outside')
        link = self.root / 'Libraries/win64/linked'
        if os.name == 'nt':
            import subprocess
            subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(outside)],
                           check=True, capture_output=True)
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.addCleanup(lambda: link.rmdir() if os.name == 'nt' else link.unlink())
        with self.assertRaisesRegex(ValueError, 'Linked dependency directory'):
            pack(self.root, self.archive)
        self.assertFalse(self.archive.exists())


if __name__ == '__main__':
    unittest.main()
