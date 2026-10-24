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

    def test_cmake_missing_dependency_uses_fixed_vocabulary(self):
        log = ('CMake Error at D:/private/FindPackageHandleStandardArgs.cmake:233 (message):\n'
               '  Could NOT find OpenSSL (missing: OPENSSL_CRYPTO_LIBRARY\n'
               '  OPENSSL_INCLUDE_DIR) (found version "private-secret")\n'
               'Call Stack (most recent call first):\n'
               '  D:/private/FindOpenSSL.cmake:691 (_FPHSA_FAILURE_MESSAGE)\n')
        value = diagnostics.extract(log)
        self.assertEqual(value["missingDependencies"], [{"package": "OpenSSL",
                          "variables": ["OPENSSL_CRYPTO_LIBRARY", "OPENSSL_INCLUDE_DIR"]}])
        for secret in ("private", "secret", "691"):
            self.assertNotIn(secret, str(value))

    def test_unknown_dependency_names_are_not_published(self):
        value = diagnostics.extract('Could NOT find private-secret (missing: private-secret)')
        self.assertEqual(value["missingDependencies"], [])

    def test_qt6_dependency_errors_discard_paths_and_unknown_variables(self):
        value = diagnostics.extract(
            'Could NOT find Qt6Core (missing: Qt6Core_DIR C:/private/secret)\n'
            'Could NOT find WrapZLIB (missing: WrapZLIB_FOUND secret)\n'
            'Could NOT find ZLIB (missing: ZLIB_LIBRARY_RELEASE ZLIB_INCLUDE_DIR secret)\n')
        self.assertEqual(value['missingDependencies'], [
            {'package': 'Qt6Core', 'variables': ['Qt6Core_DIR']},
            {'package': 'WrapZLIB', 'variables': ['WrapZLIB_FOUND']},
            {'package': 'ZLIB', 'variables': ['ZLIB_LIBRARY_RELEASE', 'ZLIB_INCLUDE_DIR']},
        ])
        self.assertNotIn('private', str(value))
        self.assertNotIn('secret', str(value))

    def test_qt6_config_package_failures_use_existing_dependency_fields(self):
        value = diagnostics.extract(
            'Could not find a package configuration file provided by "Qt6CoreTools"\n'
            '  (requested version 6.11.2) with any of the following names:\n'
            '    Qt6CoreToolsConfig.cmake\n  C:/private/secret\n'
            'Found package configuration file:\n  C:/private/Qt6Config.cmake\n'
            'but it set Qt6_FOUND to FALSE so package "Qt6" is considered to be NOT FOUND.\n'
            'but it set private_secret_FOUND to FALSE\n'
            'Could not find a package configuration file provided by "private_secret"\n')
        self.assertEqual(value['missingDependencies'], [
            {'package': 'Qt6CoreTools', 'variables': ['Qt6CoreTools_DIR']},
            {'package': 'Qt6', 'variables': []},
        ])
        self.assertTrue(value['hasDiagnostic'])
        self.assertNotIn('private', str(value))
        self.assertNotIn('secret', str(value))

    def test_root_cmake_filename_is_captured(self):
        value = diagnostics.extract('CMake Error at CMakeLists.txt:38 (message):\nprivate-secret')
        self.assertEqual(value["cmakeErrors"], [{"file": "CMakeLists.txt", "line": 38}])

    def test_link_failure_target_is_fixed_vocabulary_without_command_or_path(self):
        value = diagnostics.extract('FAILED: Release/Telegram.exe secret-identity\n'
                                    'link.exe /DAPI_HASH=secret-identity\n'
                                    'LINK : fatal error LNK1102: out of memory\n')
        self.assertEqual(value['buildTargets'], ['Telegram.exe'])
        self.assertEqual(value['compilerErrors'], [{'code': 'LNK1102'}])
        self.assertNotIn('secret-identity', str(value))
        self.assertNotIn('Release/', str(value))

    def test_unknown_failed_targets_and_private_names_are_not_exposed(self):
        value = diagnostics.extract('FAILED: C:/private/account.exe secret\n'
                                    'FAILED: C:/private/secretTelegram.exe\n')
        self.assertEqual(value['buildTargets'], [])
        self.assertNotIn('private', str(value))
        self.assertNotIn('secret', str(value))

    def test_failed_target_names_are_deduplicated(self):
        value = diagnostics.extract('FAILED: Release/Updater.exe\n'
                                    'FAILED: Release/Updater.exe\nFAILED: "Release/Packer.exe"\n')
        self.assertEqual(value['buildTargets'], ['Updater.exe', 'Packer.exe'])
