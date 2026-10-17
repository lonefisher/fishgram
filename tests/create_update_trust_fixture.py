"""Generate disposable public trust inputs; private test keys are deleted."""
import importlib.util
from pathlib import Path
import sys
import tempfile

spec = importlib.util.spec_from_file_location("keys", Path(__file__).resolve().parents[1] / "tools/key_management.py")
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)

output = Path(sys.argv[1])
output.mkdir(parents=True)
with tempfile.TemporaryDirectory(prefix="fishgram-embedded-test-") as temporary:
    private = Path(temporary)
    password = "disposable-test-only-password"
    keys.generate_root(private / "root.pem", output / "root-public.pem", password)
    keys.generate_issuer(private / "issuer.pem", output / "issuer-public.pem", password)
    manifest, signature = keys.init_manifest(private / "root.pem", output / "root-public.pem",
        output / "issuer-public.pem", "test-only-issuer", password)
    (output / "manifest.min.json").write_bytes(manifest)
    (output / "manifest.sig").write_bytes(signature)
