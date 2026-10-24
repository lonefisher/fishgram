import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PrepareCompatTests(unittest.TestCase):
    def run_wrapper(self, package, qt5_option=False, qt='5.15.19'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tools').mkdir()
            wrapper = root / 'tools/prepare-compat.py'
            shutil.copyfile(Path(__file__).parents[1] / 'tools/prepare-compat.py', wrapper)
            upstream = root / 'tdesktop/Telegram/build/prepare/prepare.py'
            upstream.parent.mkdir(parents=True)
            upstream.write_text('commands = """' + package + '\n'
                                'git clone https://chromium.googlesource.com/breakpad/breakpad\n'
                                '"""\nprint(commands)\n' +
                                ("optionsList = ['qt5',]\nimport sys\nprint('QT5=' + str('qt5' in sys.argv))\n" if qt5_option else ''), encoding='utf-8')
            (root/'fishgram.json').write_text('{"qt":"' + qt + '"}', encoding='utf-8')
            return subprocess.run([sys.executable, str(wrapper)], capture_output=True, text=True)

    def test_explicit_qt5_for_new_upstream_default(self):
        result = self.run_wrapper('mingw-w64-ucrt-x86_64-diffutils', qt5_option=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('QT5=True', result.stdout)

    def test_qt6_keeps_upstream_default(self):
        result = self.run_wrapper('mingw-w64-ucrt-x86_64-diffutils', qt5_option=True, qt='6.11.2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('QT5=False', result.stdout)

    def test_old_package_is_replaced(self):
        result = self.run_wrapper('mingw-w64-x86_64-diffutils')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('diffutils\n'))
        self.assertIn('git -c http.sslBackend=openssl clone', result.stdout)

    def test_ucrt_package_is_preserved(self):
        result = self.run_wrapper('mingw-w64-ucrt-x86_64-diffutils')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('mingw-w64-ucrt-x86_64-diffutils\n'))
        self.assertIn('git -c http.sslBackend=openssl clone', result.stdout)

    def test_unknown_package_stops_before_execution(self):
        result = self.run_wrapper('unknown-diff-package')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
