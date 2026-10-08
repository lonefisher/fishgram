import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'tools/native_memory.py'


class NativeMemoryTests(unittest.TestCase):
    def load(self):
        self.assertTrue(MODULE.is_file(), 'Missing actual Windows memory diagnostics')
        spec = importlib.util.spec_from_file_location('native_memory', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows GetPerformanceInfo required')
    def test_actual_windows_counters_are_bounded_and_numeric_only(self):
        value = self.load().collect()
        self.assertEqual(set(value), {'physicalTotalBytes', 'physicalAvailableBytes',
                         'commitTotalBytes', 'commitLimitBytes', 'peakCommitBytes', 'pageSizeBytes'})
        self.assertTrue(all(type(number) is int and number >= 0 for number in value.values()))
        self.assertGreater(value['physicalTotalBytes'], 0)
        self.assertGreater(value['commitLimitBytes'], 0)
        self.assertLessEqual(value['physicalAvailableBytes'], value['physicalTotalBytes'])
        self.assertLessEqual(value['commitTotalBytes'], value['commitLimitBytes'])
        self.assertGreaterEqual(value['peakCommitBytes'], value['commitTotalBytes'])
        self.assertEqual(value['pageSizeBytes'] & (value['pageSizeBytes'] - 1), 0)

    def test_host_architecture_uses_executable_header_not_directory_name(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'secret-account-Hostx64' / 'link.exe'
            path.parent.mkdir()
            for machine, expected in [(0x8664, 'x64'), (0x14c, 'x86'), (0xaa64, 'arm64')]:
                header = bytearray(128)
                header[:2] = b'MZ'
                struct.pack_into('<I', header, 60, 80)
                header[80:84] = b'PE\0\0'
                struct.pack_into('<H', header, 84, machine)
                path.write_bytes(header)
                self.assertEqual(module.host_architecture(path), expected)

    def test_invalid_executable_header_is_unknown(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'link.exe'
            for data in [b'', b'secret-value', b'MZ' + b'\xff' * 80,
                         b'MZ' + b'\0' * 126]:
                path.write_bytes(data)
                self.assertEqual(module.host_architecture(path), 'unknown')

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows CLI required')
    def test_cli_writes_only_approved_metadata(self):
        self.load()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'private-account-name' / 'result.json'
            process = subprocess.run([sys.executable, str(MODULE), '--label', 'build-failure',
                                      '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            value = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(value, json.loads(process.stdout))
            self.assertEqual(set(value), {'schema', 'label', 'memory', 'linkerHostArchitecture'})
            self.assertEqual(value['label'], 'build-failure')
            self.assertNotIn('private-account-name', process.stdout)
            self.assertIsNone(value['linkerHostArchitecture'])

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows CLI required')
    def test_cli_reports_tool_architecture_without_publishing_tool_path(self):
        self.load()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'result.json'
            process = subprocess.run([sys.executable, str(MODULE), '--label', 'build-context',
                                      '--linker', sys.executable, '--output', str(output)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            value = json.loads(process.stdout)
            self.assertIn(value['linkerHostArchitecture'], ['x64', 'x86', 'arm64'])
            self.assertNotIn(sys.executable, process.stdout)

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows CLI required')
    def test_missing_linker_error_does_not_expose_private_path(self):
        with tempfile.TemporaryDirectory() as temp:
            process = subprocess.run([sys.executable, str(MODULE), '--label', 'build-failure',
                                      '--linker', str(Path(temp) / 'private-path-marker' / 'missing.exe'),
                                      '--output', str(Path(temp) / 'result.json')], capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(process.stderr.strip(), 'FishGram memory diagnostics failed.')
            self.assertEqual(process.stdout, '')

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows CLI required')
    def test_report_write_error_does_not_expose_private_path(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / 'private-path-marker'
            parent.write_bytes(b'not a directory')
            process = subprocess.run([sys.executable, str(MODULE), '--label', 'build-failure',
                                      '--output', str(parent / 'result.json')], capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(process.stderr.strip(), 'FishGram memory diagnostics failed.')
            self.assertEqual(process.stdout, '')

    def powershell_measure(self, python, linker, output):
        helper = MODULE.parent / 'memory-diagnostics.ps1'
        self.assertTrue(helper.is_file(), 'Missing result-preserving diagnostic wrapper')
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        command = ("$ErrorActionPreference='Stop'; . " + quote(helper) + "; "
                   "$global:LASTEXITCODE=37; Invoke-FishGramMemoryDiagnostics -Python " + quote(python)
                   + " -Label build-failure -Linker " + quote(linker) + " -Output " + quote(output)
                   + "; if($global:LASTEXITCODE -ne 37){exit 99}; exit 0")
        return subprocess.run(['pwsh', '-NoProfile', '-Command', command], capture_output=True, text=True)

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows PowerShell required')
    def test_successful_diagnostic_preserves_prior_build_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            process = self.powershell_measure(sys.executable, sys.executable, Path(temp) / 'result.json')
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue((Path(temp) / 'result.json').is_file())

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows PowerShell required')
    def test_failed_diagnostic_preserves_prior_build_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            process = self.powershell_measure(sys.executable, Path(temp) / 'private-path-marker.exe',
                                              Path(temp) / 'result.json')
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertNotIn('private-path-marker', process.stdout + process.stderr)
            self.assertFalse((Path(temp) / 'result.json').exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Windows PowerShell required')
    def test_unstartable_diagnostic_preserves_prior_build_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            process = self.powershell_measure(Path(temp) / 'private-path-marker.exe', sys.executable,
                                              Path(temp) / 'result.json')
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertNotIn('private-path-marker', process.stdout + process.stderr)

