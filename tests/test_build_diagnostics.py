import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("diagnostics", Path(__file__).resolve().parents[1] / "tools/build_diagnostics.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class BuildDiagnosticsTests(unittest.TestCase):
    def test_only_compiler_locations_and_codes_survive(self):
        secret = "never-publish-this-identity"
        log = (f'cl /DAPI_HASH={secret} C:\\private\\account\\source.cpp\n'
               f'C:\\private\\account\\source.cpp(42,3): error C2065: {secret}\n'
               f'client.exe : fatal error LNK1120: {secret}\n'
               f'C:\\private\\account\\source.cpp(42,3): error C2065: {secret}\n'
               'ninja: build stopped: subcommand failed.\n')
        value = diagnostics.extract(log)
        self.assertEqual(value["compilerErrors"], [
            {"file": "source.cpp", "line": 42, "code": "C2065"}, {"code": "LNK1120"}])
        self.assertTrue(value["ninjaStopped"])
        for text in (secret, "private", "account", "cl /D"):
            self.assertNotIn(text, str(value))

    def test_unknown_failure_is_not_invented(self):
        value = diagnostics.extract("Process ended with -1; no compiler output")
        self.assertFalse(value["hasDiagnostic"])
        self.assertEqual(value["compilerErrors"], [])

    def test_cmake_error_location_does_not_publish_arguments(self):
        value = diagnostics.extract('CMake Error at D:/private/user/Qt5CoreConfig.cmake:181 (message):\nsecret-data')
        self.assertEqual(value["cmakeErrors"], [{"file": "Qt5CoreConfig.cmake", "line": 181}])
        self.assertTrue(value["hasDiagnostic"])
        self.assertNotIn("private", str(value))
        self.assertNotIn("secret-data", str(value))

    def test_diagnostics_are_bounded(self):
        value = diagnostics.extract("\n".join(f"p{n}.cpp({n}): error C2065: x" for n in range(100)))
        self.assertEqual(len(value["compilerErrors"]), 32)
