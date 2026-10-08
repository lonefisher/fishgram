#!/usr/bin/env python3
"""Offline Ed25519 root and FishGram update signing-key maintenance.

Private-key cryptography is delegated to the system OpenSSL CLI. This module
never prints key material or accepts passwords in command-line arguments.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import getpass
import json
import os
import re
import stat
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

YEAR_SECONDS = 365 * 24 * 60 * 60
MAX_SAFE_JSON_INT = 2**53 - 1
KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CHANNELS = {"stable", "beta", "canary-public", "canary-private"}


class KeyManagementError(Exception):
    """Expected, safe-to-display operational error."""


def openssl_path() -> str:
    configured = os.environ.get("FISHGRAM_OPENSSL")
    candidate = configured or shutil.which("openssl")
    if not candidate or not Path(candidate).is_file():
        raise KeyManagementError(
            "OpenSSL CLI not found. Add it to PATH or set FISHGRAM_OPENSSL."
        )
    return str(candidate)


def _openssl(*args: str, password: str | None = None) -> bytes:
    if password is not None and any(character in password for character in "\r\n\0"):
        raise KeyManagementError("Passphrases cannot contain CR, LF, or NUL.")
    command = [openssl_path(), *map(str, args)]
    secret_input = None if password is None else (password + "\n").encode("utf-8")
    try:
        result = subprocess.run(
            command,
            input=secret_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as error:
        raise KeyManagementError("Could not start OpenSSL.") from error
    if result.returncode:
        raise KeyManagementError("OpenSSL operation failed; check the key and passphrase.")
    return result.stdout


def _password(confirm: bool = False) -> str:
    value = getpass.getpass("Passphrase (not echoed): ")
    if any(character in value for character in "\r\n\0"):
        raise KeyManagementError("Passphrases cannot contain CR, LF, or NUL.")
    if len(value) < 12:
        raise KeyManagementError("Use a passphrase of at least 12 characters.")
    if confirm and getpass.getpass("Repeat passphrase: ") != value:
        raise KeyManagementError("Passphrases do not match.")
    return value


def _private_mode(path: Path) -> None:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                check=True,
            )
            sid = next(row[-1] for row in csv.reader([result.stdout.strip()]))
            acl = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if acl.returncode:
                raise KeyManagementError(f"Could not restrict permissions on {path}.")
        except (OSError, subprocess.SubprocessError, StopIteration) as error:
            raise KeyManagementError(f"Could not restrict permissions on {path}.") from error
    else:
        path.chmod(0o600)


def _require_new_path(path: Path) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    if not path.parent.is_dir():
        raise KeyManagementError(f"Parent directory does not exist: {path.parent}")
    try:
        resolved_parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise KeyManagementError(f"Could not resolve parent directory: {path.parent}") from error
    if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(path.parent)):
        raise KeyManagementError(f"Refusing redirected or symlinked parent directory: {path.parent}")
    ancestor = path.parent
    while True:
        try:
            metadata = ancestor.lstat()
        except OSError as error:
            raise KeyManagementError(f"Could not inspect parent directory: {ancestor}") from error
        reparse = os.name == "nt" and bool(
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        if ancestor.is_symlink() or reparse:
            raise KeyManagementError(f"Refusing reparse-point parent directory: {ancestor}")
        if ancestor.parent == ancestor:
            break
        ancestor = ancestor.parent
    if os.path.lexists(path):
        raise KeyManagementError(f"Refusing to overwrite existing file: {path}")
    return path


def _write_new(path: Path, data: bytes, private: bool = False) -> None:
    path = _require_new_path(path)
    try:
        with path.open("xb") as stream:
            stream.write(data)
        if private:
            _private_mode(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _generate_private(path: Path, password: str) -> None:
    path = _require_new_path(path)
    try:
        _openssl(
            "genpkey", "-algorithm", "Ed25519", "-aes-256-cbc",
            "-pass", "stdin", "-out", str(path), password=password,
        )
        _private_mode(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _public_der(private_path: Path, password: str) -> bytes:
    der = _openssl(
        "pkey", "-in", str(private_path), "-passin", "stdin",
        "-pubout", "-outform", "DER", password=password,
    )
    if len(der) != 44 or not der.startswith(bytes.fromhex("302a300506032b6570032100")):
        raise KeyManagementError("OpenSSL returned an invalid Ed25519 public key.")
    return der


def _public_pem(private_path: Path, password: str) -> bytes:
    return _openssl(
        "pkey", "-in", str(private_path), "-passin", "stdin", "-pubout",
        password=password,
    )


def generate_root(private_path: Path, public_path: Path, password: str) -> None:
    private_path = _require_new_path(private_path)
    public_path = _require_new_path(public_path)
    if private_path == public_path:
        raise KeyManagementError("Private and public key paths must differ.")
    _generate_private(private_path, password)
    try:
        _write_new(public_path, _public_pem(private_path, password))
    except Exception:
        private_path.unlink(missing_ok=True)
        raise


def backup_encrypted_root(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve(strict=True)
    if not source.is_file():
        raise KeyManagementError("Root key backup source must be a file.")
    encrypted_pem = source.read_bytes()
    if not encrypted_pem.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise KeyManagementError("Refusing backup: root key is not encrypted PKCS#8 PEM.")
    _write_new(destination, encrypted_pem, private=True)


def generate_issuer(
    private_path: Path, public_path: Path, password: str
) -> tuple[bytes, int]:
    private_path = _require_new_path(private_path)
    public_path = _require_new_path(public_path)
    if private_path == public_path:
        raise KeyManagementError("Private and public key paths must differ.")
    _generate_private(private_path, password)
    try:
        public_pem = _public_pem(private_path, password)
        der = _public_der(private_path, password)
        _write_new(public_path, public_pem)
    except Exception:
        private_path.unlink(missing_ok=True)
        raise
    return der[-32:], int(dt.datetime.now(dt.timezone.utc).timestamp()) + YEAR_SECONDS


def init_manifest(
    root_private: Path,
    root_public: Path,
    issuer_public: Path,
    issuer_id: str,
    password: str,
    now: int | None = None,
) -> tuple[bytes, bytes]:
    """Build the first one-year stable/beta manifest under a self-checked root."""
    if not KEY_ID.fullmatch(issuer_id):
        raise KeyManagementError("Issuer id must be 1-64 ASCII letters, digits, dot, underscore or dash.")
    root_der = _public_der(root_private, password)
    trusted_root_der = _openssl(
        "pkey", "-pubin", "-in", str(root_public), "-outform", "DER"
    )
    if root_der != trusted_root_der:
        raise KeyManagementError("Root private key does not match the supplied root public key.")
    issuer_der = _openssl(
        "pkey", "-pubin", "-in", str(issuer_public), "-outform", "DER"
    )
    prefix = bytes.fromhex("302a300506032b6570032100")
    if len(issuer_der) != 44 or not issuer_der.startswith(prefix):
        raise KeyManagementError("Issuer public key must be Ed25519.")
    now = int(dt.datetime.now(dt.timezone.utc).timestamp()) if now is None else now
    if not 0 < now < MAX_SAFE_JSON_INT:
        raise KeyManagementError("Current time is outside the supported range.")
    expires = now + YEAR_SECONDS
    manifest = {
        "format": 1,
        "manifest_version": 1,
        "issued": now,
        "expires": expires,
        "keys": [{
            "id": issuer_id,
            "alg": "Ed25519",
            "x": _b64url(issuer_der[-32:]),
            "expires": expires,
        }],
        "channels": {"stable": [[issuer_id]], "beta": [[issuer_id]]},
        "revoked": [],
    }
    encoded = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = _sign(root_private, password, encoded)
    verify_manifest(encoded, signature, root_public)
    return encoded, signature


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_b64url(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise KeyManagementError(f"Manifest has invalid {label} encoding.")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, base64.binascii.Error) as error:
        raise KeyManagementError(f"Manifest has invalid {label} encoding.") from error


def _strict_json(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise KeyManagementError("Manifest JSON is invalid or contains duplicate fields.") from error
    if not isinstance(value, dict):
        raise KeyManagementError("Manifest JSON must be an object.")
    return value


def verify_manifest(data: bytes, signature: bytes, root_public: Path) -> dict:
    if not data or len(data) > 256 * 1024:
        raise KeyManagementError("Manifest size is outside the parser limit.")
    with tempfile.TemporaryDirectory(prefix="fishgram-verify-") as temp:
        signature_path = Path(temp) / "manifest.sig"
        signature_path.write_bytes(signature)
        # The manifest is public; a temporary file avoids OpenSSL-version
        # differences in stdin handling and contains no secret material.
        message_path = Path(temp) / "manifest.json"
        message_path.write_bytes(data)
        _openssl(
            "pkeyutl", "-verify", "-pubin", "-inkey", str(root_public),
            "-rawin", "-in", str(message_path), "-sigfile", str(signature_path),
        )
    manifest = _strict_json(data)
    if type(manifest.get("format")) is not int or manifest["format"] != 1:
        raise KeyManagementError("Manifest format must be 1.")
    version = manifest.get("manifest_version")
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 0xFFFFFFFF:
        raise KeyManagementError("Manifest version must be a positive uint32.")
    for name in ("issued", "expires"):
        value = manifest.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) >= MAX_SAFE_JSON_INT:
            raise KeyManagementError(f"Manifest {name} must be an exact JSON integer.")
    keys = manifest.get("keys")
    channels = manifest.get("channels")
    revoked = manifest.get("revoked")
    if not isinstance(keys, list) or len(keys) > 64:
        raise KeyManagementError("Manifest keys must be an array of at most 64 entries.")
    ids = set()
    for key in keys:
        if not isinstance(key, dict) or not isinstance(key.get("id"), str) or not KEY_ID.fullmatch(key["id"]):
            raise KeyManagementError("Manifest contains an invalid key id.")
        if key["id"] in ids or key.get("alg") not in ("Ed25519", "ES256"):
            raise KeyManagementError("Manifest contains a duplicate id or unsupported algorithm.")
        ids.add(key["id"])
        if key["alg"] == "Ed25519":
            raw = _decode_b64url(key.get("x"), "Ed25519 public key")
            if len(raw) != 32:
                raise KeyManagementError("Manifest Ed25519 public key must be 32 bytes.")
        elif key.get("crv") != "P-256":
            raise KeyManagementError("Manifest ES256 curve must be P-256.")
        elif len(_decode_b64url(key.get("x"), "ES256 x coordinate")) != 32 or len(
            _decode_b64url(key.get("y"), "ES256 y coordinate")
        ) != 32:
            raise KeyManagementError("Manifest ES256 coordinates must be 32 bytes.")
        if "expires" in key and (
            not isinstance(key["expires"], int)
            or isinstance(key["expires"], bool)
            or key["expires"] <= 0
            or key["expires"] >= MAX_SAFE_JSON_INT
        ):
            raise KeyManagementError("Manifest contains an invalid key expiry.")
    if not isinstance(channels, dict) or len(channels) > 16:
        raise KeyManagementError("Manifest channels must be an object of at most 16 entries.")
    for channel, groups in channels.items():
        if not isinstance(channel, str) or not isinstance(groups, list) or not 1 <= len(groups) <= 8:
            raise KeyManagementError("Manifest has an invalid channel or group list.")
        for group in groups:
            if not isinstance(group, list) or not 1 <= len(group) <= 16 or any(
                not isinstance(item, str) or len(item) > 64 for item in group
            ):
                raise KeyManagementError("Manifest has an invalid channel key group.")
    if not isinstance(revoked, list) or len(revoked) > 64 or any(
        not isinstance(item, str) or not 1 <= len(item) <= 64 for item in revoked
    ):
        raise KeyManagementError("Manifest revoked list is invalid.")
    return manifest


def _parse_channel_group(value: str) -> tuple[str, list[str]]:
    if "=" not in value:
        raise KeyManagementError("Channel group must use CHANNEL=KEY_ID[,KEY_ID].")
    channel, raw_ids = value.split("=", 1)
    ids = raw_ids.split(",")
    if channel not in CHANNELS or not ids or any(not KEY_ID.fullmatch(item) for item in ids):
        raise KeyManagementError("Invalid channel name or key id in --channel-group.")
    if len(set(ids)) != len(ids):
        raise KeyManagementError("A channel group cannot repeat a key id.")
    return channel, ids


def _sign(private_path: Path, password: str, message: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="fishgram-sign-") as temp:
        message_path = Path(temp) / "manifest.json"
        message_path.write_bytes(message)
        return _openssl(
            "pkeyutl", "-sign", "-inkey", str(private_path), "-passin", "stdin",
            "-rawin", "-in", str(message_path), password=password,
        )


def update_manifest(
    previous_data: bytes,
    previous_signature: bytes,
    root_public: Path,
    root_private: Path,
    password: str,
    version: int,
    add_keys: list[tuple[str, Path]],
    renew_ids: list[str],
    revoke_ids: list[str],
    channel_groups: list[str],
    now: int | None = None,
) -> tuple[bytes, bytes]:
    previous = verify_manifest(previous_data, previous_signature, root_public)
    if version <= previous["manifest_version"]:
        raise KeyManagementError("Manifest version must be greater than the signed previous version.")
    if not 1 <= version <= 0xFFFFFFFF:
        raise KeyManagementError("Manifest version must be a positive uint32.")
    # Prove the encrypted signing key is the same root whose public key
    # validated the prior manifest before it is allowed to authorize changes.
    if _public_der(root_private, password) != _openssl(
        "pkey", "-pubin", "-in", str(root_public), "-outform", "DER"
    ):
        raise KeyManagementError("Root private key does not match the trusted public key.")
    now = int(dt.datetime.now(dt.timezone.utc).timestamp()) if now is None else now
    if not 0 < now < MAX_SAFE_JSON_INT:
        raise KeyManagementError("Current time is outside the supported range.")
    updated = json.loads(json.dumps(previous))
    updated["manifest_version"] = version
    updated["issued"] = now
    updated["expires"] = now + YEAR_SECONDS
    by_id = {key["id"]: key for key in updated["keys"]}
    for key_id, public_path in add_keys:
        if not KEY_ID.fullmatch(key_id) or key_id in by_id:
            raise KeyManagementError(f"New key id is invalid or already exists: {key_id}")
        der = _openssl("pkey", "-pubin", "-in", str(public_path), "-outform", "DER")
        if len(der) != 44 or not der.startswith(bytes.fromhex("302a300506032b6570032100")):
            raise KeyManagementError(f"Not an Ed25519 public key: {public_path}")
        raw = der[-32:]
        key = {"id": key_id, "alg": "Ed25519", "x": _b64url(raw), "expires": now + YEAR_SECONDS}
        updated["keys"].append(key)
        by_id[key_id] = key
    for key_id in renew_ids:
        if key_id not in by_id or key_id in updated["revoked"]:
            raise KeyManagementError(f"Cannot renew unknown or revoked key: {key_id}")
        by_id[key_id]["expires"] = now + YEAR_SECONDS
    revoked = set(updated["revoked"])
    for key_id in revoke_ids:
        if key_id not in by_id:
            raise KeyManagementError(f"Cannot revoke unknown key: {key_id}")
        revoked.add(key_id)
    updated["revoked"] = sorted(revoked)
    grouped: dict[str, list[list[str]]] = {}
    for value in channel_groups:
        channel, ids = _parse_channel_group(value)
        grouped.setdefault(channel, []).append(ids)
    for channel, groups in grouped.items():
        updated["channels"][channel] = groups
    for channel, groups in list(updated["channels"].items()):
        filtered = [[item for item in group if item not in revoked] for group in groups]
        if all(not group for group in filtered):
            updated["channels"].pop(channel, None)
            continue
        if any(not group for group in filtered):
            raise KeyManagementError(
                f"Revocation leaves channel {channel!r} without an authorized key group."
            )
        updated["channels"][channel] = filtered
        for group in filtered:
            if any(item not in by_id for item in group):
                raise KeyManagementError(f"Channel {channel!r} references an unknown key.")
    if len(updated["keys"]) > 64 or len(updated["revoked"]) > 64:
        raise KeyManagementError("Manifest key or revoked entry limit reached.")
    encoded = json.dumps(updated, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # Re-parse all structural limits before signing.
    if len(encoded) > 256 * 1024:
        raise KeyManagementError("Manifest exceeds the client parser size limit.")
    signature = _sign(root_private, password, encoded)
    verify_manifest(encoded, signature, root_public)
    return encoded, signature


def maintenance_report(data: bytes, now: int | None = None, warn_days: int = 45) -> list[tuple[str, int]]:
    manifest = _strict_json(data)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp()) if now is None else now
    due = []
    for key in manifest.get("keys", []):
        expiry = key.get("expires")
        if isinstance(expiry, int) and not isinstance(expiry, bool):
            days = (expiry - now) // 86400
            if days <= warn_days:
                due.append((key.get("id", "<unknown>"), days))
    return sorted(due, key=lambda item: item[1])


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise KeyManagementError(f"Could not read {path}.") from error


def _cmd_init_root(args) -> None:
    private_path = _require_new_path(Path(args.private))
    public_path = _require_new_path(Path(args.public))
    if private_path == public_path:
        raise KeyManagementError("Private and public key paths must differ.")
    backup_path = _require_new_path(Path(args.backup)) if args.backup else None
    if backup_path in (private_path, public_path):
        raise KeyManagementError("Root backup path must differ from root key paths.")
    password = _password(confirm=True)
    generate_root(private_path, public_path, password)
    if backup_path:
        try:
            backup_encrypted_root(private_path, backup_path)
        except Exception:
            private_path.unlink(missing_ok=True)
            public_path.unlink(missing_ok=True)
            raise
    print("Encrypted root key created; public key exported.")


def _cmd_issuer(args) -> None:
    if not KEY_ID.fullmatch(args.key_id):
        raise KeyManagementError("Issuer id must be 1-64 ASCII letters, digits, dot, underscore or dash.")
    private_path = _require_new_path(Path(args.private))
    public_path = _require_new_path(Path(args.public))
    if private_path == public_path:
        raise KeyManagementError("Private and public key paths must differ.")
    password = _password(confirm=True)
    _, expiry = generate_issuer(private_path, public_path, password)
    print(f"Encrypted issuer key created: id={args.key_id}, expires={expiry} (UTC epoch).")


def _cmd_backup(args) -> None:
    backup_encrypted_root(Path(args.source), Path(args.destination))
    print("Encrypted root backup copied; store one copy offline.")


def _cmd_init_manifest(args) -> None:
    manifest_path = _require_new_path(Path(args.output))
    signature_path = _require_new_path(Path(args.signature_output))
    if manifest_path == signature_path:
        raise KeyManagementError("Manifest and signature output paths must differ.")
    fixture_files: dict[Path, bytes] = {}
    if args.public_fixture_dir:
        fixture_dir = _require_new_path(
            Path(args.public_fixture_dir) / "root-public.pem"
        ).parent
        fixture_files = {
            fixture_dir / "root-public.pem": _read(Path(args.root_public)),
            _require_new_path(fixture_dir / "issuer-public.pem"): _read(Path(args.issuer_public)),
            _require_new_path(fixture_dir / "manifest.min.json"): b"",
            _require_new_path(fixture_dir / "manifest.sig"): b"",
        }
        if len(fixture_files) != 4 or {manifest_path, signature_path}.intersection(fixture_files):
            raise KeyManagementError("Public fixture files must not overlap manifest output paths.")
    password = _password()
    data, signature = init_manifest(
        Path(args.root_private), Path(args.root_public), Path(args.issuer_public),
        args.issuer_id, password,
    )
    written = []
    try:
        _write_new(manifest_path, data)
        written.append(manifest_path)
        _write_new(signature_path, signature)
        written.append(signature_path)
        if fixture_files:
            fixture_files[Path(args.public_fixture_dir) / "manifest.min.json"] = data
            fixture_files[Path(args.public_fixture_dir) / "manifest.sig"] = signature
            for path, contents in fixture_files.items():
                _write_new(path, contents)
                written.append(path)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    print("Root-signed bootstrap manifest version 1 written.")


def _cmd_manifest(args) -> None:
    additions = []
    for item in args.add_key:
        if "=" not in item:
            raise KeyManagementError("--add-key uses KEY_ID=PUBLIC_KEY_PEM.")
        key_id, path = item.split("=", 1)
        additions.append((key_id, Path(path)))
    output_path = _require_new_path(Path(args.output))
    signature_path = _require_new_path(Path(args.signature_output))
    if output_path == signature_path:
        raise KeyManagementError("Manifest and signature output paths must differ.")
    previous = _read(Path(args.previous))
    previous_signature = _read(Path(args.previous_signature))
    password = _password()
    data, signature = update_manifest(
        previous, previous_signature,
        Path(args.root_public), Path(args.root_private), password,
        args.version, additions, args.renew_key, args.revoke_key,
        args.channel_group,
    )
    _write_new(output_path, data)
    try:
        _write_new(signature_path, signature)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    print(f"Root-signed manifest version {args.version} written.")


def _cmd_check(args) -> None:
    data = _read(Path(args.manifest))
    verify_manifest(data, _read(Path(args.signature)), Path(args.root_public))
    due = maintenance_report(data, warn_days=args.warn_days)
    for key_id, days in due:
        state = "expired" if days < 0 else "expires within maintenance window"
        print(f"{key_id}: {state} ({days} days)")
    if not due:
        print(f"No issuer keys expire within {args.warn_days} days.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    root = commands.add_parser("init-root", help="Create encrypted offline Ed25519 root key")
    root.add_argument("--private", required=True, help="New encrypted PKCS8 PEM path")
    root.add_argument("--public", required=True, help="New root public PEM path")
    root.add_argument("--backup", help="Optional encrypted backup path in an existing directory")
    root.set_defaults(run=_cmd_init_root)
    issuer = commands.add_parser("new-issuer", help="Create a one-year encrypted Ed25519 issuer key")
    issuer.add_argument("--id", dest="key_id", required=True)
    issuer.add_argument("--private", required=True)
    issuer.add_argument("--public", required=True)
    issuer.set_defaults(run=_cmd_issuer)
    backup = commands.add_parser("backup-root", help="Copy the encrypted root key to an existing directory")
    backup.add_argument("--source", required=True)
    backup.add_argument("--destination", required=True)
    backup.set_defaults(run=_cmd_backup)
    bootstrap = commands.add_parser("init-manifest", help="Create the first root-signed stable/beta manifest")
    bootstrap.add_argument("--root-private", required=True)
    bootstrap.add_argument("--root-public", required=True)
    bootstrap.add_argument("--issuer-public", required=True)
    bootstrap.add_argument("--issuer-id", required=True)
    bootstrap.add_argument("--output", required=True)
    bootstrap.add_argument("--signature-output", required=True)
    bootstrap.add_argument("--public-fixture-dir", help="Optional existing directory for a public-only parser fixture")
    bootstrap.set_defaults(run=_cmd_init_manifest)
    manifest = commands.add_parser("update-manifest", help="Apply issuer changes and sign a higher manifest version")
    manifest.add_argument("--previous", required=True)
    manifest.add_argument("--previous-signature", required=True)
    manifest.add_argument("--root-public", required=True)
    manifest.add_argument("--root-private", required=True)
    manifest.add_argument("--version", required=True, type=int)
    manifest.add_argument("--add-key", action="append", default=[], metavar="ID=PUBLIC_PEM")
    manifest.add_argument("--renew-key", action="append", default=[])
    manifest.add_argument("--revoke-key", action="append", default=[])
    manifest.add_argument("--channel-group", action="append", default=[], metavar="CHANNEL=ID[,ID]")
    manifest.add_argument("--output", required=True)
    manifest.add_argument("--signature-output", required=True)
    manifest.set_defaults(run=_cmd_manifest)
    check = commands.add_parser("check", help="Report issuer keys expiring within 45 days")
    check.add_argument("--manifest", required=True)
    check.add_argument("--signature", required=True)
    check.add_argument("--root-public", required=True)
    check.add_argument("--warn-days", type=int, default=45)
    check.set_defaults(run=_cmd_check)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.run(args)
    except KeyManagementError as error:
        print(f"key-management: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
