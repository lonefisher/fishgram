import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ApiSetupTests(unittest.TestCase):
    def run_setup(self, api_hash):
        with tempfile.TemporaryDirectory(prefix="fishgram-api-fixture-") as temp:
            root = Path(temp)
            (root / "tools").mkdir()
            shutil.copyfile(Path(__file__).parents[1] / "tools/set-fishgram-api.ps1",
                            root / "tools/set-fishgram-api.ps1")
            harness = root / "test.ps1"
            harness.write_text("""
$ErrorActionPreference='Stop'
$global:exports=0
function Read-Host { param($Prompt,[switch]$AsSecureString)
    if ($AsSecureString) { return ConvertTo-SecureString $env:FISHGRAM_FIXTURE_HASH -AsPlainText -Force }
    return '12345'
}
function Export-Clixml { param([Parameter(ValueFromPipeline)]$InputObject,$LiteralPath)
    process { $global:exports++ }
}
$accepted=$true
try { & (Join-Path $PSScriptRoot 'tools/set-fishgram-api.ps1') -ConfirmOwnApplication | Out-Null }
catch { $accepted=$false }
@{accepted=$accepted;exports=$global:exports} | ConvertTo-Json -Compress
""", encoding="utf-8")
            import os
            result = subprocess.run(["pwsh", "-NoProfile", "-File", str(harness)],
                env={**os.environ, "FISHGRAM_FIXTURE_HASH": api_hash},
                capture_output=True, text=True, check=True)
            return json.loads(result.stdout)

    def test_invalid_hash_never_reaches_credential_export(self):
        for value in ("", "a" * 31, "a" * 33, "g" * 32, "a" * 31 + " "):
            with self.subTest(length=len(value)):
                self.assertEqual(self.run_setup(value), {"accepted": False, "exports": 0})

    def test_valid_lower_and_upper_hex_hashes_reach_export(self):
        for value in ("a1" * 16, "A1" * 16):
            self.assertEqual(self.run_setup(value), {"accepted": True, "exports": 1})


if __name__ == "__main__":
    unittest.main()

