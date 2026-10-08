#!/usr/bin/env python3
"""Fail-closed FishGram candidate signing; private-key operations use OpenSSL."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/product-candidate.yml"
ARTIFACT_PREFIX = "fishgram-product-candidate-"


class SigningError(Exception):
    """Expected, safe-to-display signing refusal."""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise SigningError("Required reviewed signing component is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load("fishgram_release_gate", ROOT / "tools/release_gate.py")
keys = _load("fishgram_key_management", ROOT / "tools/key_management.py")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_run_provenance(run: dict, jobs: dict, artifacts: dict, repository: str,
                          run_id: int, expected_sha: str | None = None) -> str:
    """Bind the artifact to a successful workflow_dispatch on protected main."""
    repo = run.get("repository")
    if (not isinstance(repo, dict) or repo.get("full_name") != repository
            or type(run.get("id")) is not int or run["id"] != run_id
            or run.get("path") != WORKFLOW
            or run.get("event") != "workflow_dispatch"
            or run.get("conclusion") != "success"
            or run.get("head_branch") != "main"
            or not isinstance(run.get("head_sha"), str)
            or len(run["head_sha"]) != 40
            or any(c not in "0123456789abcdef" for c in run["head_sha"])):
        raise SigningError("Candidate workflow provenance is not approved.")
    if expected_sha is not None and run["head_sha"] != expected_sha:
        raise SigningError("Candidate run does not match the external candidate record.")
    job_list = jobs.get("jobs") if isinstance(jobs, dict) else None
    candidates = [job for job in job_list or []
                  if isinstance(job, dict) and job.get("name") in ("candidate", "candidateapproved")]
    if len(candidates) != 1 or candidates[0].get("conclusion") != "success":
        raise SigningError("Protected product-candidate job did not succeed.")
    artifact_list = artifacts.get("artifacts") if isinstance(artifacts, dict) else None
    expected_name = ARTIFACT_PREFIX + str(run_id)
    matches = [item for item in artifact_list or []
               if isinstance(item, dict) and item.get("name") == expected_name]
    if (len(matches) != 1 or matches[0].get("expired") is not False
            or (matches[0].get("workflow_run") or {}).get("id") != run_id):
        raise SigningError("Candidate artifact is not uniquely bound to the approved workflow run.")
    return run["head_sha"]


def check_issuer_authorization(manifest: dict, issuer_id: str, channel: str,
                               expected_public_raw: bytes, now: int | None = None) -> dict:
    """Require one Ed25519 issuer to satisfy every required channel group."""
    now = int(time.time()) if now is None else now
    if (not isinstance(manifest, dict) or type(manifest.get("issued")) is not int
            or type(manifest.get("expires")) is not int
            or manifest["issued"] > now or manifest["expires"] <= now):
        raise SigningError("Issuer trust manifest is not currently valid.")
    if issuer_id in manifest.get("revoked", []):
        raise SigningError("Issuer is revoked.")
    entries = [item for item in manifest.get("keys", [])
               if isinstance(item, dict) and item.get("id") == issuer_id]
    if len(entries) != 1 or entries[0].get("alg") != "Ed25519":
        raise SigningError("Issuer is not a unique active Ed25519 key.")
    entry = entries[0]
    key_expiry = entry.get("expires", manifest["expires"])
    if type(key_expiry) is not int or key_expiry <= now:
        raise SigningError("Issuer key is expired.")
    try:
        encoded = entry["x"]
        expected = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise SigningError("Issuer public key record is invalid.") from error
    if expected != expected_public_raw:
        raise SigningError("Issuer private and trusted public keys do not match.")
    groups = manifest.get("channels", {}).get(channel)
    if (not isinstance(groups, list) or not groups
            or any(not isinstance(group, list) or issuer_id not in group for group in groups)):
        raise SigningError("One issuer cannot satisfy every required channel signature group.")
    return entry


class Envelope:
    def __init__(self, signed_region: bytes, manifest: bytes, manifest_signature: bytes,
                 signatures: list[tuple[str, bytes]], payload: bytes, channel: int,
                 target: tuple[int, int], version: int, created: int):
        self.signed_region = signed_region
        self.manifest = manifest
        self.manifest_signature = manifest_signature
        self.signatures = signatures
        self.payload = payload
        self.channel = channel
        self.target = target
        self.version = version
        self.created = created


def parse_envelope(data: bytes) -> Envelope:
    """Parse the production TDUP v2 envelope with bounded exact reads."""
    if len(data) > 256 * 1024 * 1024 + 1024 * 1024 or not data.startswith(b"TDUP"):
        raise SigningError("Packer output is not a bounded FishGram v2 package.")
    offset = 4

    def take(size: int) -> bytes:
        nonlocal offset
        if size < 0 or offset + size > len(data):
            raise SigningError("Packer output has a truncated v2 package field.")
        value = data[offset:offset + size]
        offset += size
        return value

    def u32() -> int:
        return struct.unpack("<I", take(4))[0]

    def u64() -> int:
        return struct.unpack("<Q", take(8))[0]

    # Keep this synchronized with core/update_verify.h's kEnvelopeFormat.
    if u32() != 2:
        raise SigningError("Packer output has an unsupported v2 package format.")
    channel = take(1)[0]
    os_id, arch = take(1)[0], take(1)[0]
    version, created = u64(), u64()
    manifest_len = u32()
    if not 1 <= manifest_len <= 256 * 1024:
        raise SigningError("Packer output has an invalid manifest size.")
    manifest = take(manifest_len)
    signed_region = data[:offset]
    manifest_sig_len = u32()
    if not 1 <= manifest_sig_len <= 4096:
        raise SigningError("Packer output has an invalid root signature size.")
    manifest_sig = take(manifest_sig_len)
    count = u32()
    if count > 64:
        raise SigningError("Packer output has too many issuer signatures.")
    signatures = []
    for _ in range(count):
        id_len = u32()
        if not 1 <= id_len <= 64:
            raise SigningError("Packer output has an invalid issuer id.")
        try:
            key_id = take(id_len).decode("ascii")
        except UnicodeDecodeError as error:
            raise SigningError("Packer output has an invalid issuer id.") from error
        sig_len = u32()
        if not 1 <= sig_len <= 128:
            raise SigningError("Packer output has an invalid issuer signature size.")
        signatures.append((key_id, take(sig_len)))
    payload_len = u32()
    if not 1 <= payload_len <= 256 * 1024 * 1024:
        raise SigningError("Packer output has an invalid payload size.")
    payload = take(payload_len)
    if offset != len(data):
        raise SigningError("Packer output has trailing bytes.")
    return Envelope(signed_region, manifest, manifest_sig, signatures, payload,
                    channel, (os_id, arch), version, created)


def verify_envelope(envelope: Envelope, trust_manifest: dict, issuer_id: str,
                    issuer_public_raw: bytes, root_public_pem: bytes,
                    channel_name: str, expected_unsigned: Envelope | None = None) -> None:
    """Verify root trust, exact Packer transformation and detached Ed25519 signature."""
    with tempfile.TemporaryDirectory(prefix="fishgram-public-verify-") as temp:
        folder = Path(temp)
        root = folder / "root-public.pem"
        root.write_bytes(root_public_pem)
        manifest_path, manifest_sig_path = folder / "manifest.json", folder / "manifest.sig"
        manifest_path.write_bytes(envelope.manifest)
        manifest_sig_path.write_bytes(envelope.manifest_signature)
        parsed = keys.verify_manifest(envelope.manifest, envelope.manifest_signature, root)
        if parsed != trust_manifest:
            raise SigningError("Package trust manifest differs from the reviewed trust bundle.")
        if expected_unsigned is not None and (
            envelope.signed_region != expected_unsigned.signed_region
            or envelope.manifest_signature != expected_unsigned.manifest_signature
            or envelope.manifest != expected_unsigned.manifest
            or envelope.payload != expected_unsigned.payload
            or envelope.channel != expected_unsigned.channel
            or envelope.target != expected_unsigned.target
            or envelope.version != expected_unsigned.version
            or envelope.created != expected_unsigned.created
        ):
            raise SigningError("Packer changed the reviewed unsigned package while embedding signatures.")
        matches = [signature for key_id, signature in envelope.signatures if key_id == issuer_id]
        if len(matches) != 1 or len(envelope.signatures) != 1 or len(matches[0]) != 64:
            raise SigningError("Signed package does not contain exactly the expected issuer signature.")
        check_issuer_authorization(parsed, issuer_id, channel_name, issuer_public_raw)
        public_pem = keys._openssl("pkey", "-pubin", "-inform", "DER",
                                  "-in", _write_public_der(folder, issuer_public_raw), "-outform", "PEM")
        public_path = folder / "issuer-public.pem"
        public_path.write_bytes(public_pem)
        message_path, signature_path = folder / "signing-input.bin", folder / "issuer.sig"
        message_path.write_bytes(envelope.signed_region + hashlib.sha256(envelope.payload).digest())
        signature_path.write_bytes(matches[0])
        try:
            keys._openssl("pkeyutl", "-verify", "-pubin", "-inkey", str(public_path),
                          "-rawin", "-in", str(message_path), "-sigfile", str(signature_path))
        except keys.KeyManagementError as error:
            raise SigningError("Signed package Ed25519 verification failed.") from error


def _write_public_der(folder: Path, raw: bytes) -> str:
    path = folder / "issuer-public.der"
    # RFC 8410 SubjectPublicKeyInfo prefix for Ed25519.
    path.write_bytes(bytes.fromhex("302a300506032b6570032100") + raw)
    return str(path)


def _read_json(path: Path) -> dict:
    try:
        value = keys._strict_json(Path(path).read_bytes())
    except (OSError, keys.KeyManagementError) as error:
        raise SigningError("A required candidate verification record is invalid.") from error
    if not isinstance(value, dict):
        raise SigningError("A required candidate verification record is invalid.")
    return value


def _verify_trust(root: Path, trust: Path) -> tuple[dict, bytes, bytes, bytes]:
    expected = (root / "config/update-trust").resolve(strict=True)
    actual = Path(trust).resolve(strict=True)
    def reparse(path: Path) -> bool:
        try:
            info = path.lstat()
            return path.is_symlink() or (os.name == "nt" and bool(
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))
        except OSError:
            return True
    if actual != expected or any(reparse(path) for path in (actual, *actual.parents)):
        raise SigningError("Production trust must come from the reviewed config/update-trust directory.")
    allowed_names = {"root-public.pem", "issuer-public.pem", "manifest.min.json", "manifest.sig"}
    try:
        if {item.name for item in actual.iterdir()} != allowed_names:
            raise SigningError("Production trust directory must contain only the reviewed public trust files.")
    except OSError as error:
        raise SigningError("Reviewed production trust directory could not be inspected.") from error
    root_path, issuer_path = actual / "root-public.pem", actual / "issuer-public.pem"
    data_path, sig_path = actual / "manifest.min.json", actual / "manifest.sig"
    if not all(path.is_file() and not path.is_symlink() for path in (root_path, issuer_path, data_path, sig_path)):
        raise SigningError("Reviewed production public trust bundle is incomplete.")
    root_pem, issuer_pem = root_path.read_bytes(), issuer_path.read_bytes()
    data, signature = data_path.read_bytes(), sig_path.read_bytes()
    try:
        manifest = keys.verify_manifest(data, signature, root_path)
        issuer_der = keys._openssl("pkey", "-pubin", "-in", str(issuer_path), "-outform", "DER")
    except Exception as error:
        raise SigningError("Reviewed production public trust could not be verified.") from error
    prefix = bytes.fromhex("302a300506032b6570032100")
    if len(issuer_der) != 44 or not issuer_der.startswith(prefix):
        raise SigningError("Configured production issuer key must be Ed25519.")
    return manifest, issuer_der[-32:], root_pem, data


def _record_and_gates(root: Path, manifest_path: Path, package: Path, source: Path,
                      qa_path: Path, packer: Path, trust: Path, commit: str,
                      qa_name: str) -> tuple[dict, dict, dict, bytes, bytes, bytes]:
    if Path(manifest_path).name != "candidate-record.json":
        raise SigningError("Signing requires the external candidate-record.json.")
    manifest = _read_json(manifest_path)
    source_record = manifest.get("sourceArchive")
    if not isinstance(source_record, dict) or set(source_record) != {"name", "size", "sha256"}:
        raise SigningError("External candidate record has no bound corresponding-source archive.")
    recipe = gate._recipe_at(root, manifest["parentCommit"])
    gate.validate_manifest(manifest, recipe)
    tracked = ("tools/release_gate.py", "tools/key_management.py", "tools/export_source.py",
               "tools/license_inventory.py", "config/license-sources.json",
               "tools/sign_candidate.py", "tests/test_sign_candidate.py", WORKFLOW,
               ".github/workflows/product-candidate.yml", "docs/RELEASING.md", qa_name)
    gate.check_pins(root, commit, manifest, tracked=tracked)
    gate.verify_package(manifest, package)
    gate.verify_tool(manifest, "Packer.exe", packer)
    gate.verify_source_archive(manifest, source)
    qa = _read_json(qa_path)
    gate.verify_qa(manifest, qa)
    auth_path = Path(tempfile.gettempdir()) / ("fishgram-authorization-" + os.urandom(16).hex() + ".json")
    qa_bytes = Path(qa_path).read_bytes()
    try:
        authorization = gate.authorize(manifest, qa, Path(qa_path).name, qa_bytes, auth_path)
    finally:
        auth_path.unlink(missing_ok=True)
    trust_manifest, issuer_raw, root_pem, trust_data = _verify_trust(root, trust)
    channel = authorization["packer"]["channel"]
    return manifest, authorization, trust_manifest, issuer_raw, root_pem, trust_data


def _check_record_trust(manifest: dict, trust_manifest: dict, issuer_id: str,
                        channel: str, issuer_raw: bytes) -> None:
    check_issuer_authorization(trust_manifest, issuer_id, channel, issuer_raw)


def _protected_tempdir(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        if os.name == "nt":
            keys._private_mode(path)
        else:
            path.chmod(0o700)
    except Exception as error:
        shutil.rmtree(path, ignore_errors=True)
        raise SigningError("Could not restrict temporary signing-file permissions.") from error
    return path


@contextmanager
def secret_key_file(key_env: str, passphrase_env: str):
    """Consume CI secret variables and write encrypted PKCS#8 only under a private ACL."""
    pem = os.environ.pop(key_env, None)
    password = os.environ.pop(passphrase_env, None)
    if not pem or not password or "ENCRYPTED PRIVATE KEY" not in pem[:256]:
        raise SigningError("Protected encrypted issuer-key inputs are unavailable.")
    folder = _protected_tempdir("fishgram-issuer-")
    path = folder / "issuer-encrypted.pem"
    try:
        with path.open("xb"):
            pass
        if os.name == "nt":
            keys._private_mode(path)
        else:
            path.chmod(0o600)
        path.write_text(pem, encoding="ascii", newline="")
        yield path, password
    except SigningError:
        raise
    except Exception as error:
        raise SigningError("Protected issuer-key processing failed.") from error
    finally:
        password = ""
        pem = ""
        shutil.rmtree(folder, ignore_errors=True)


def _prepare(manifest: dict, auth: dict, package: Path, packer: Path, payload: Path,
             trust: Path, prepared: Path) -> tuple[Path, Path]:
    if prepared.exists():
        raise SigningError("Prepared signing directory already exists.")
    prepared.mkdir(parents=True)
    gate.extract_payload(manifest, package, payload)
    signing_input = prepared / "signing-input.bin"
    command = [str(packer)]
    for name in auth["packer"]["files"]:
        command.extend(("-path", name))
    command.extend(("-version", str(auth["packer"]["versionBase"]),
                    "-counter", str(auth["packer"]["counter"]),
                    "-channel", auth["packer"]["channel"], "-target", auth["packer"]["target"],
                    "-keys-loc", str(trust), "-emit-signing-input", str(signing_input)))
    try:
        result = subprocess.run(command, cwd=payload, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError as error:
        raise SigningError("Reviewed Packer could not be started.") from error
    unsigned_files = list(payload.glob("*.unsigned"))
    if result.returncode or not signing_input.is_file() or len(unsigned_files) != 1:
        raise SigningError("Reviewed Packer did not produce one unsigned package and signing input.")
    unsigned = unsigned_files[0]
    envelope = parse_envelope(unsigned.read_bytes())
    if envelope.signatures:
        raise SigningError("Packer signing input is not unsigned.")
    expected_channel = {"stable": 0, "beta": 1, "canary-public": 2, "canary-private": 3}[auth["packer"]["channel"]]
    if envelope.channel != expected_channel or envelope.version != (
            auth["packer"]["versionBase"] << 32 | auth["packer"]["counter"]):
        raise SigningError("Packer unsigned package differs from the authorized candidate version.")
    expected_target = {"win": (0, 0), "win64": (0, 1), "winarm": (0, 2),
                       "mac": (1, 1), "armac": (1, 2), "linux": (2, 1)}[auth["packer"]["target"]]
    if envelope.target != expected_target:
        raise SigningError("Packer unsigned package has the wrong authorized platform target.")
    expected_input = envelope.signed_region + hashlib.sha256(envelope.payload).digest()
    if signing_input.read_bytes() != expected_input:
        raise SigningError("Packer signing input does not match its unsigned package.")
    (prepared / "unsigned.path").write_text(str(unsigned), encoding="utf-8")
    (prepared / "prepared.json").write_text(json.dumps({
        "schema": 1, "kind": "fishgram-prepared-signing", "signingInputSha256": sha256(signing_input.read_bytes()),
        "unsignedSha256": sha256_file(unsigned), "manifestSha256": sha256(json.dumps(manifest, sort_keys=True).encode()),
        "sourceArchive": manifest["sourceArchive"], "package": manifest["archive"],
        "packerSha256": sha256_file(packer), "channel": auth["packer"]["channel"],
        "version": auth["updateVersion"],
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return unsigned, signing_input


def _validate_prepared(prepared: Path, manifest: dict, packer: Path,
                       payload_dir: Path, auth: dict) -> tuple[Path, bytes, Envelope]:
    record = _read_json(prepared / "prepared.json")
    if (record.get("kind") != "fishgram-prepared-signing"
            or record.get("sourceArchive") != manifest.get("sourceArchive")
            or record.get("package") != manifest.get("archive")
            or record.get("packerSha256") != sha256_file(packer)
            or record.get("manifestSha256") != sha256(json.dumps(manifest, sort_keys=True).encode())
            or record.get("version") != auth["updateVersion"]
            or record.get("channel") != auth["packer"]["channel"]):
        raise SigningError("Prepared signing inputs do not match the verified candidate.")
    unsigned = Path((prepared / "unsigned.path").read_text(encoding="utf-8"))
    signing_input = prepared / "signing-input.bin"
    try:
        unsigned_real = unsigned.resolve(strict=True)
        payload_real = payload_dir.resolve(strict=True)
    except OSError as error:
        raise SigningError("Prepared Packer output is unavailable.") from error
    if (unsigned.is_symlink() or unsigned_real.parent != payload_real
            or not unsigned.is_file() or not signing_input.is_file()
            or sha256_file(unsigned) != record.get("unsignedSha256")
            or sha256(signing_input.read_bytes()) != record.get("signingInputSha256")):
        raise SigningError("Prepared Packer outputs changed after verification.")
    envelope = parse_envelope(unsigned.read_bytes())
    if envelope.signatures:
        raise SigningError("Prepared package unexpectedly contains a signature.")
    if signing_input.read_bytes() != envelope.signed_region + hashlib.sha256(envelope.payload).digest():
        raise SigningError("Prepared signing input does not match the unsigned package.")
    return unsigned, signing_input.read_bytes(), envelope


def _embed_and_record(packer: Path, trust: Path, unsigned: Path, signature: bytes,
                      issuer_id: str, manifest: dict, auth: dict, trust_manifest: dict,
                      issuer_raw: bytes, root_pem: bytes, unsigned_envelope: Envelope,
                      output: Path, qa: dict) -> tuple[Path, Path]:
    if output.exists():
        raise SigningError("Signing output directory already exists.")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="fishgram-public-signature-") as temp:
        signature_path = Path(temp) / "issuer.sig"
        signature_path.write_bytes(signature)
        command = [str(packer), "-unsigned", str(unsigned), "-keys-loc", str(trust),
                   "-embed-signatures", f"{issuer_id}:{signature_path}"]
        try:
            result = subprocess.run(command, cwd=unsigned.parent, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError as error:
            raise SigningError("Reviewed Packer could not embed the detached signature.") from error
    package = Path(str(unsigned)[:-len(".unsigned")])
    if result.returncode or not package.is_file():
        raise SigningError("Reviewed Packer rejected the detached issuer signature.")
    signed = parse_envelope(package.read_bytes())
    verify_envelope(signed, trust_manifest, issuer_id, issuer_raw, root_pem,
                    auth["packer"]["channel"], expected_unsigned=unsigned_envelope)
    output_package = output / package.name
    shutil.copyfile(package, output_package)
    record_path = output / "signing-record.json"
    record = {
        "schema": 1, "kind": "fishgram-signed-candidate", "candidateVersion": manifest["version"],
        "updateVersion": auth["updateVersion"], "channel": auth["packer"]["channel"],
        "parentCommit": manifest["parentCommit"], "sourceCommit": manifest["sourceCommit"],
        "candidateArchive": manifest["archive"], "sourceArchive": manifest["sourceArchive"],
        "qa": {"file": Path(qa["_file"]).name, "sha256": qa["_sha256"],
               "approver": qa["approver"], "approvedAt": qa["approvedAt"]},
        "issuer": {"id": issuer_id, "alg": "Ed25519", "publicKeySha256": sha256(issuer_raw)},
        "package": {"name": output_package.name, "size": output_package.stat().st_size,
                    "sha256": sha256_file(output_package)},
        "signedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    record_path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return output_package, record_path


def _preflight(options) -> tuple[dict, dict, dict, bytes, bytes, bytes, dict]:
    root = Path(options.root).resolve(strict=True)
    run_sha = None
    if options.run_json:
        run = _read_json(Path(options.run_json))
        jobs, artifacts = _read_json(Path(options.jobs_json)), _read_json(Path(options.artifacts_json))
        run_sha = verify_run_provenance(run, jobs, artifacts, options.repository,
                                        int(options.run_id), expected_sha=None)
    commit = options.commit or run_sha
    if not commit:
        raise SigningError("A reviewed commit or verified candidate run is required.")
    manifest, auth, trust_manifest, issuer_raw, root_pem, trust_data = _record_and_gates(
        root, Path(options.manifest), Path(options.package), Path(options.source),
        Path(options.qa), Path(options.packer), Path(options.trust), commit,
        "release/qa/" + Path(options.qa).name)
    if run_sha is not None and manifest.get("parentCommit") != run_sha:
        raise SigningError("External candidate record does not match its successful candidate workflow SHA.")
    _check_record_trust(manifest, trust_manifest, options.issuer_id, auth["packer"]["channel"], issuer_raw)
    qa = _read_json(Path(options.qa))
    qa["_file"], qa["_sha256"] = Path(options.qa).name, sha256_file(Path(options.qa))
    return manifest, auth, trust_manifest, issuer_raw, root_pem, trust_data, qa


def _sign(options) -> None:
    (manifest, auth, trust_manifest, issuer_raw, root_pem, _trust_data, qa) = _preflight(options)
    packer, trust = Path(options.packer), Path(options.trust)
    if options.prepare_only:
        payload = Path(options.payload_dir)
        _prepare(manifest, auth, Path(options.package), packer, payload, trust, Path(options.prepared_dir))
        print("Candidate, QA, pins, public trust and Packer signing input verified.")
        return
    unsigned, signing_input, unsigned_envelope = _validate_prepared(
        Path(options.prepared_dir), manifest, packer, Path(options.payload_dir), auth)
    _check_record_trust(manifest, trust_manifest, options.issuer_id, auth["packer"]["channel"], issuer_raw)
    if options.key_env:
        if options.private_key or options.passphrase_env is None:
            raise SigningError("CI key input options are inconsistent.")
        with secret_key_file(options.key_env, options.passphrase_env) as (private_path, password):
            _finish_signature(options, private_path, password, unsigned, signing_input, unsigned_envelope,
                              manifest, auth, trust_manifest, issuer_raw, root_pem, trust, qa)
    else:
        if not options.private_key or options.passphrase_env:
            raise SigningError("Local signing requires an encrypted issuer-key file.")
        password = keys._password()
        try:
            _finish_signature(options, Path(options.private_key), password, unsigned, signing_input,
                              unsigned_envelope, manifest, auth, trust_manifest, issuer_raw,
                              root_pem, trust, qa)
        finally:
            password = ""


def _finish_signature(options, private_path: Path, password: str, unsigned: Path,
                      signing_input: bytes, unsigned_envelope: Envelope, manifest: dict,
                      auth: dict, trust_manifest: dict, issuer_raw: bytes, root_pem: bytes,
                      trust: Path, qa: dict) -> None:
    try:
        derived = keys._public_der(private_path, password)
        if derived[-32:] != issuer_raw:
            raise SigningError("Encrypted issuer key does not match the reviewed production public key.")
        check_issuer_authorization(trust_manifest, options.issuer_id,
                                   auth["packer"]["channel"], derived[-32:])
        signature = keys._sign(private_path, password, signing_input)
        if len(signature) != 64:
            raise SigningError("OpenSSL did not return an Ed25519 signature.")
        _embed_and_record(Path(options.packer), trust, unsigned, signature, options.issuer_id,
                          manifest, auth, trust_manifest, issuer_raw, root_pem,
                          unsigned_envelope, Path(options.output_dir), qa)
    except SigningError:
        raise
    except Exception as error:
        raise SigningError("Issuer signing or package verification failed.") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--manifest", required=True, help="External candidate-record.json only")
    parser.add_argument("--package", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--qa", required=True)
    parser.add_argument("--packer", required=True)
    parser.add_argument("--trust", required=True)
    parser.add_argument("--issuer-id", required=True)
    parser.add_argument("--commit")
    parser.add_argument("--run-json")
    parser.add_argument("--jobs-json")
    parser.add_argument("--artifacts-json")
    parser.add_argument("--repository")
    parser.add_argument("--run-id")
    parser.add_argument("--prepared-dir", required=True)
    parser.add_argument("--payload-dir", default=str(Path(tempfile.gettempdir()) / "fishgram-sign-payload"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--private-key", help="Local encrypted PKCS#8 PEM; password is hidden-interactive")
    parser.add_argument("--key-env", help=argparse.SUPPRESS)
    parser.add_argument("--passphrase-env", help=argparse.SUPPRESS)
    return parser


def _verify_run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify the GitHub candidate run provenance before artifact use.")
    parser.add_argument("--run-json", required=True)
    parser.add_argument("--jobs-json", required=True)
    parser.add_argument("--artifacts-json", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    provenance = parser.parse_args(argv)
    run_sha = verify_run_provenance(_read_json(Path(provenance.run_json)),
                                    _read_json(Path(provenance.jobs_json)),
                                    _read_json(Path(provenance.artifacts_json)),
                                    provenance.repository, provenance.run_id)
    print("Candidate workflow and artifact provenance verified: " + run_sha)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "verify-run":
            return _verify_run_cli(argv[1:])
        options = _parser().parse_args(argv)
        if bool(options.run_json) != bool(options.jobs_json) or bool(options.run_json) != bool(options.artifacts_json):
            raise SigningError("Candidate workflow provenance files must be supplied together.")
        if options.run_json and (not options.repository or not options.run_id):
            raise SigningError("Candidate repository and run ID are required with workflow provenance.")
        _sign(options)
        return 0
    except (SigningError, gate.GateError, keys.KeyManagementError, OSError, ValueError, KeyError) as error:
        # Never echo OpenSSL diagnostics, secret values, key paths, or passphrases.
        print("Signing refused: " + (str(error) if isinstance(error, SigningError) else "candidate validation failed."), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
