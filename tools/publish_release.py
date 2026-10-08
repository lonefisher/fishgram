"""Prepare and, when explicitly authorized, promote a verified FishGram release.

The CLI is deliberately offline: it validates reviewed inputs and emits a
promotion directory.  ``promote`` is the separately gated API orchestration
boundary and accepts an injected transport so its irreversible ordering can
be tested without contacting GitHub.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Protocol
from urllib.parse import urlsplit


PRODUCT_REPOSITORY = "lonefisher/fishgram"
SOURCE_REPOSITORY = "lonefisher/tdesktop"
HEX = re.compile(r"^[a-f0-9]{64}$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+-r\d+$")
MAX_UPDATE_SIZE = 256 * 1024 * 1024

# Publication has its own review-pin set in addition to the signer gate. Keep
# the publisher, its REST transport, and the source-license inventory inputs
# bound to the exact protected-main commit used for this promotion.
PUBLISH_TRACKED = (
    "tools/publish_release.py",
    "tools/github_release_transport.py",
    "tools/release_gate.py",
    "tools/license_inventory.py",
    "config/license-sources.json",
    "tests/test_publish_release.py",
    "tests/test_github_release_transport.py",
    "docs/PUBLISHING.md",
    ".github/workflows/publish-release.yml",
)


class PublishError(ValueError):
    """An input or promotion condition is unsafe or incomplete."""


def verify_signing_run_provenance(run: dict, jobs: dict, artifacts: dict,
                                 repository: str, run_id: int,
                                 candidate_run_id: int, expected_sha: str) -> None:
    """Require one successful manual signer run and its exact candidate-bound artifact."""
    expected_workflow = ".github/workflows/sign-release-candidate.yml"
    if (repository != PRODUCT_REPOSITORY or type(run_id) is not int or type(candidate_run_id) is not int
            or not isinstance(run, dict) or not isinstance(run.get("repository"), dict)
            or run["repository"].get("full_name") != repository or run.get("id") != run_id
            or run.get("path") != expected_workflow or run.get("event") != "workflow_dispatch"
            or run.get("conclusion") != "success" or run.get("head_branch") != "main"
            or run.get("head_sha") != expected_sha):
        raise PublishError("Signed artifact is not from the successful reviewed-main signing workflow run.")
    job_list = jobs.get("jobs") if isinstance(jobs, dict) else None
    successful = [job for job in job_list or []
                  if isinstance(job, dict) and job.get("name") in {"sign-candidate", "sign-candidate (sign-candidate)"}
                  and job.get("conclusion") == "success"]
    if len(successful) != 1:
        raise PublishError("Protected signing job did not succeed uniquely.")
    artifact_list = artifacts.get("artifacts") if isinstance(artifacts, dict) else None
    expected_name = f"fishgram-signed-candidate-{candidate_run_id}"
    matches = [item for item in artifact_list or []
               if isinstance(item, dict) and item.get("name") == expected_name]
    if (len(matches) != 1 or matches[0].get("expired") is not False
            or (matches[0].get("workflow_run") or {}).get("id") != run_id):
        raise PublishError("Signed artifact is not uniquely owned by the exact successful signing run.")


@dataclass(frozen=True)
class Asset:
    name: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class PageFile:
    path: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class PromotionPlan:
    tag: str
    target_commit: str
    index_channel: str
    package_channel: str
    version: str
    assets: tuple[Asset, ...]
    page_files: tuple[PageFile, ...]
    index_path: str
    index_bytes: bytes
    reuse_release: bool = False


@dataclass(frozen=True)
class PublishContext:
    event: str
    ref: str
    environment: str
    approval_gate_passed: bool


class ReleaseTransport(Protocol):
    def tag_exists(self, repository: str, tag: str) -> bool: ...
    def release_exists(self, tag: str) -> bool: ...
    def source_tag_exists(self, tag: str) -> bool: ...
    def asset_names(self, tag: str) -> set[str]: ...
    def get_index(self, path: str) -> dict | None: ...
    def create_release(self, tag: str, target: str, title: str, body: str,
                       *, prerelease: bool) -> None: ...
    def upload_asset(self, tag: str, name: str, data: bytes) -> None: ...
    def download_asset(self, tag: str, name: str) -> bytes: ...
    def get_page(self, path: str) -> bytes | None: ...
    def put_page(self, path: str, data: bytes) -> None: ...
    def pages_config(self) -> dict: ...
    def create_source_tag(self, tag: str, target: str) -> None: ...
    def publish_release(self, tag: str) -> None: ...


def supports_split_channel_feed(root: Path) -> bool:
    source = Path(root) / "tdesktop/Telegram/SourceFiles/core/fishgram_update_feed.cpp"
    try:
        code = source.read_text(encoding="utf-8")
    except OSError as error:
        raise PublishError("Could not inspect production ParseFeed for split-channel support.") from error
    return ("packageChannelValue" in code and "indexChannel" in code
            and 'indexChannel == "stable" && packageChannel != "stable"' in code
            and "Candidate{ *version, *size, packageChannel" in code)


def _canonical_decimal(value: str, label: str) -> int:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise PublishError(label + " must be a canonical decimal string.")
    return int(value)


def _feed_url(url: str, package_channel: str, version: str) -> None:
    number = _canonical_decimal(version, "Update version")
    if number > 0xFFFFFFFFFFFFFFFF or number >> 32 == 0 or number & 0xFFFFFFFF == 0:
        raise PublishError("Update version has an invalid base or revision.")
    base, revision = number >> 32, number & 0xFFFFFFFF
    expected = f"fishgram-update-win-x64-{base}-r{revision}" + ("-beta" if package_channel == "beta" else "")
    parsed = urlsplit(url)
    parts = parsed.path.split("/")
    if (package_channel not in {"stable", "beta"} or parsed.scheme != "https"
            or parsed.hostname != "github.com" or parsed.port is not None
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or len(parts) != 7
            or parts[:5] != ["", PRODUCT_REPOSITORY.split("/")[0], PRODUCT_REPOSITORY.split("/")[1], "releases", "download"]
            or not TAG.fullmatch(parts[5]) or parts[6] != expected):
        raise PublishError("Update URL or package name violates the production ParseFeed contract.")


def build_index(index_channel: str, package_channel: str, version: str,
                size: int, sha256: str, url: str) -> bytes:
    number = _canonical_decimal(version, "Update version")
    if index_channel not in {"stable", "beta"} or package_channel not in {"stable", "beta"}:
        raise PublishError("Only stable and beta channels can be published.")
    if index_channel == "stable" and package_channel != "stable":
        raise PublishError("Stable indexes may contain only stable-channel signed packages.")
    if (number > 0xFFFFFFFFFFFFFFFF or number >> 32 == 0 or number & 0xFFFFFFFF == 0
            or type(size) is not int or not 0 < size <= MAX_UPDATE_SIZE
            or not isinstance(sha256, str) or not HEX.fullmatch(sha256)):
        raise PublishError("Update index version, size or SHA256 is invalid.")
    _feed_url(url, package_channel, version)
    value = {"schema": 1, "channel": index_channel, "platform": "windows-x64",
             "update": {"version": version, "size": str(size), "sha256": sha256,
                        "url": url, "channel": package_channel}}
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _parse_current_index(value: dict | None, channel: str) -> int:
    if value is None:
        return 0
    if (not isinstance(value, dict) or value.get("schema") != 1
            or value.get("channel") != channel or value.get("platform") != "windows-x64"):
        raise PublishError("Current public channel index is malformed or belongs to another channel.")
    update = value.get("update")
    if update is None:
        return 0
    if (not isinstance(update, dict)
            or not {"version", "size", "sha256", "url"}.issubset(update)
            or set(update) - {"version", "size", "sha256", "url", "channel"}):
        raise PublishError("Current public channel update entry is malformed.")
    version = _canonical_decimal(update.get("version"), "Current update version")
    size = _canonical_decimal(update.get("size"), "Current indexed package size")
    package_channel = update.get("channel", channel)
    if (version > 0xFFFFFFFFFFFFFFFF or version >> 32 == 0 or version & 0xFFFFFFFF == 0
            or not 0 < size <= MAX_UPDATE_SIZE or package_channel not in {"stable", "beta"}
            or (channel == "stable" and package_channel != "stable")
            or not isinstance(update.get("sha256"), str) or not HEX.fullmatch(update["sha256"])):
        raise PublishError("Current public channel version is out of range.")
    _feed_url(update.get("url"), package_channel, update["version"])
    return version


def _validate_plan_index(plan: PromotionPlan) -> None:
    if len(plan.index_bytes) > 1024 * 1024:
        raise PublishError("Promotion index exceeds the production ParseFeed limit.")
    try:
        index = json.loads(plan.index_bytes)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublishError("Promotion channel index is invalid JSON.") from error
    if (not isinstance(index, dict) or set(index) != {"schema", "channel", "platform", "update"}
            or index.get("schema") != 1 or index.get("channel") != plan.index_channel
            or index.get("platform") != "windows-x64" or not isinstance(index.get("update"), dict)):
        raise PublishError("Promotion channel index does not match the FishGram feed schema.")
    if plan.index_channel == "stable" and plan.package_channel != "stable":
        raise PublishError("Stable indexes may contain only stable-channel signed packages.")
    entry = index["update"]
    if (set(entry) != {"version", "size", "sha256", "url", "channel"}
            or entry.get("version") != plan.version or entry.get("channel") != plan.package_channel):
        raise PublishError("Promotion channel index does not bind the complete planned version.")
    size = _canonical_decimal(entry.get("size"), "Indexed package size")
    _feed_url(entry.get("url"), plan.package_channel, plan.version)
    asset_name = urlsplit(entry["url"]).path.rsplit("/", 1)[-1]
    matching = [asset for asset in plan.assets if asset.name == asset_name]
    if (len(matching) != 1 or size != len(matching[0].data)
            or size <= 0 or size > MAX_UPDATE_SIZE
            or entry.get("sha256") != matching[0].sha256
            or urlsplit(entry["url"]).path.split("/")[-2] != plan.tag):
        raise PublishError("Promotion index size or hash does not match its signed package attachment.")


def promote(plan: PromotionPlan, api: ReleaseTransport, *, enabled: bool,
            context: PublishContext | None) -> None:
    """Publish attachments, read each public byte back, then promote the index."""
    if enabled is not True:
        raise PublishError("Publishing requires the explicit enable switch.")
    if (context is None or context.event != "workflow_dispatch"
            or context.ref != "refs/heads/main" or context.environment != "release"
            or context.approval_gate_passed is not True):
        raise PublishError("Publishing requires workflow_dispatch on reviewed main after release-environment approval.")
    if (not COMMIT.fullmatch(plan.target_commit) or plan.index_channel not in {"stable", "beta"}
            or plan.package_channel not in {"stable", "beta"}
            or not RELEASE_TAG.fullmatch(plan.tag) or not plan.assets
            or plan.index_path != f"{plan.index_channel}.json"):
        raise PublishError("Promotion plan is malformed.")
    number = _canonical_decimal(plan.version, "Update version")
    if number > 0xFFFFFFFFFFFFFFFF:
        raise PublishError("Update version is outside the production uint64 range.")
    _validate_plan_index(plan)
    tag_exists = api.tag_exists(PRODUCT_REPOSITORY, plan.tag)
    source_tag_exists = api.source_tag_exists(plan.tag)
    release_exists = api.release_exists(plan.tag)
    if plan.reuse_release:
        if not tag_exists or not source_tag_exists or not release_exists:
            raise PublishError("Index-only promotion requires both immutable repository tags and the release.")
    elif tag_exists or source_tag_exists or release_exists:
        raise PublishError("Product/source tag or release already exists; published objects are immutable.")
    old = _parse_current_index(api.get_index(plan.index_path), plan.index_channel)
    if int(plan.version) <= old:
        raise PublishError("The channel index must move to a strictly newer full update version.")
    names = [asset.name for asset in plan.assets]
    paths = [item.path for item in plan.page_files]
    if len(names) != len(set(names)) or len(paths) != len(set(paths)):
        raise PublishError("Promotion plan contains duplicate asset or Pages paths.")
    if any(not name or Path(name).name != name or "\\" in name for name in names):
        raise PublishError("Release asset names must be safe basenames.")
    if any(not path.startswith("keys/") or "\\" in path or str(PurePosixPath(path)) != path
           or ".." in PurePosixPath(path).parts for path in paths):
        raise PublishError("Only reviewed public trust files under Pages keys/ may be promoted.")
    if (f"FishGram-{plan.tag[1:]}-windows-x64-candidate.zip" not in names
            or not {"candidate-record.json", "signing-record.json"}.issubset(names)
            or sum(name.endswith(".zip") and name != f"FishGram-{plan.tag[1:]}-windows-x64-candidate.zip"
                   for name in names) != 1
            or set(paths) != {"keys/root-public.pem", "keys/issuer-public.pem",
                              "keys/manifest.min.json", "keys/manifest.sig"}):
        raise PublishError("Promotion is missing the exact candidate, source, signing records or public key trust bundle.")
    if not plan.reuse_release:
        pages_config = getattr(api, "pages_config", None)
        if not callable(pages_config):
            raise PublishError("Pages configuration cannot be verified by this transport.")
        try:
            pages_config()
        except Exception as error:
            raise PublishError("Pages is unavailable or unsafe; publication stopped before writes.") from error
        # A previous interrupted draft must be handled manually; never append to it.
        existing_assets = api.asset_names(plan.tag) if release_exists else set()
        if existing_assets:
            raise PublishError("Existing release contains assets; publication will not append or replace them.")
    candidate_record = next(item.data for item in plan.assets if item.name == "candidate-record.json")
    try:
        source_commit = json.loads(candidate_record)["sourceCommit"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PublishError("Candidate record does not bind the source repository commit.") from error
    if not COMMIT.fullmatch(source_commit):
        raise PublishError("Candidate record source repository pin is malformed.")
    if not plan.reuse_release:
        api.create_source_tag(plan.tag, source_commit)
    if not plan.reuse_release:
        api.create_release(plan.tag, plan.target_commit, plan.tag,
                           "FishGram signed Windows x64 release. See attached corresponding source and QA records.",
                           prerelease=plan.package_channel == "beta")
    for asset in plan.assets:
        if not plan.reuse_release:
            try:
                api.upload_asset(plan.tag, asset.name, asset.data)
            except Exception as error:
                raise PublishError("Draft release asset upload failed: " + asset.name) from error
    if not plan.reuse_release:
        try:
            api.publish_release(plan.tag)
        except Exception as error:
            raise PublishError("Draft release could not be published after uploads.") from error
    for asset in plan.assets:
        try:
            public_bytes = api.download_asset(plan.tag, asset.name)
        except Exception as error:
            raise PublishError("Public release asset download failed: " + asset.name) from error
        if hashlib.sha256(public_bytes).hexdigest() != asset.sha256:
            raise PublishError("Public release asset SHA256 mismatch: " + asset.name)
    for item in plan.page_files:
        try:
            current = api.get_page(item.path)
            if current != item.data:
                api.put_page(item.path, item.data)
            public_bytes = api.get_page(item.path)
        except Exception as error:
            raise PublishError("Pages trust-file promotion or read-back failed: " + item.path) from error
        if public_bytes is None or hashlib.sha256(public_bytes).hexdigest() != item.sha256:
            raise PublishError("Public Pages trust-file SHA256 mismatch: " + item.path)
    try:
        api.put_page(plan.index_path, plan.index_bytes)
    except Exception as error:
        raise PublishError("Channel index promotion failed after all public assets were verified.") from error
    try:
        public_index = api.get_page(plan.index_path)
    except Exception as error:
        raise PublishError("Channel index was written but public read-back failed.") from error
    if public_index is None or hashlib.sha256(public_index).hexdigest() != hashlib.sha256(plan.index_bytes).hexdigest():
        raise PublishError("Public channel index SHA256 mismatch after promotion.")


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublishError(label + " is missing or invalid JSON.") from error
    if not isinstance(value, dict):
        raise PublishError(label + " must be a JSON object.")
    return value


def _promotion_file(root: Path, relative: str) -> bytes:
    """Read only a canonical regular file beneath an offline promotion root."""
    pure = PurePosixPath(relative) if isinstance(relative, str) else PurePosixPath(".")
    if (not isinstance(relative, str) or not relative or relative.startswith("/")
            or "\\" in relative or str(pure) != relative or any(part in {".", ".."} for part in pure.parts)):
        raise PublishError("Promotion file path is not canonical and relative.")
    root = root.resolve(strict=True)
    path = root.joinpath(*pure.parts)
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PublishError("Promotion directory contains a symbolic link.")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise PublishError("Promotion file escaped its root or is not a regular file.")
    return resolved.read_bytes()


def validate_inputs(root: Path, commit: str, index_channel: str | None, reuse_release: bool,
                    record_path: Path, qa_path: Path,
                    signing_record_path: Path, candidate_zip: Path, source_zip: Path,
                    signed_package: Path, packer: Path, trust: Path, issuer_id: str) -> PromotionPlan:
    """Run the existing candidate/QA/pin gates and production v2 verification."""
    root = root.resolve(strict=True)
    try:
        from sign_candidate import _check_record_trust, _record_and_gates, _verify_trust, parse_envelope, verify_envelope
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sign_candidate import _check_record_trust, _record_and_gates, _verify_trust, parse_envelope, verify_envelope
    import release_gate as gate

    manifest, authorization, trust_manifest, issuer_raw, root_pem, _ = _record_and_gates(
        root, record_path, candidate_zip, source_zip, qa_path, packer, trust, commit,
        "release/qa/" + qa_path.name)
    try:
        gate.check_pins(root, commit, manifest,
                        tracked=(*PUBLISH_TRACKED, "release/qa/" + qa_path.name))
    except gate.GateError as error:
        raise PublishError("Publisher review pins rejected the protected-main checkout.") from error
    signing = _load_json(signing_record_path, "Signing record")
    _load_json(qa_path, "QA approval")
    signing_qa = signing.get("qa")
    signing_issuer = signing.get("issuer")
    signed_entry = signing.get("package")
    if (signing.get("schema") != 1 or signing.get("kind") != "fishgram-signed-candidate"
            or signing.get("candidateVersion") != manifest["version"]
            or signing.get("updateVersion") != authorization["updateVersion"]
            or signing.get("channel") != authorization["packer"]["channel"]
            or signing.get("parentCommit") != manifest["parentCommit"]
            or signing.get("sourceCommit") != manifest["sourceCommit"]
            or signing.get("candidateArchive") != manifest["archive"]
            or signing.get("sourceArchive") != manifest["sourceArchive"]
            or not isinstance(signing_qa, dict)
            or signing_qa.get("file") != qa_path.name
            or signing_qa.get("sha256") != gate.sha256_file(qa_path)
            or not isinstance(signing_issuer, dict)
            or signing_issuer.get("id") != issuer_id
            or signing_issuer.get("alg") != "Ed25519"
            or signing_issuer.get("publicKeySha256") != hashlib.sha256(issuer_raw).hexdigest()
            or not isinstance(signed_entry, dict)
            or set(signed_entry) != {"name", "size", "sha256"}
            or signed_entry.get("name") != signed_package.name
            or type(signed_entry.get("size")) is not int
            or signed_entry["size"] != signed_package.stat().st_size
            or signed_entry.get("sha256") != gate.sha256_file(signed_package)):
        raise PublishError("Signing record is not bound to this exact candidate, QA, trust key and signed package.")
    _check_record_trust(manifest, trust_manifest, issuer_id, manifest["channel"], issuer_raw)
    trust_manifest, issuer_raw, root_pem, _ = _verify_trust(root, trust)
    envelope = parse_envelope(signed_package.read_bytes())
    verify_envelope(envelope, trust_manifest, issuer_id, issuer_raw, root_pem, manifest["channel"])
    expected_channel = {"stable": 0, "beta": 1}[manifest["channel"]]
    if (envelope.channel != expected_channel or envelope.version != int(authorization["updateVersion"])
            or envelope.target != (1, 0)):
        raise PublishError("Verified update package does not bind the authorized Windows x64 version and channel.")

    version = authorization["updateVersion"]
    update_name = f"fishgram-update-win-x64-{envelope.version >> 32}-r{envelope.version & 0xffffffff}"
    if manifest["channel"] == "beta":
        update_name += "-beta"
    release_tag = "v" + manifest["version"]
    update_url = f"https://github.com/{PRODUCT_REPOSITORY}/releases/download/{release_tag}/{update_name}"
    index_channel = index_channel or manifest["channel"]
    if index_channel not in {"stable", "beta"}:
        raise PublishError("Only stable and beta index channels are supported.")
    if reuse_release and (index_channel != "stable" or manifest["channel"] != "stable"):
        raise PublishError("Existing-release index promotion is only valid for a stable package entering stable index.")
    if index_channel != manifest["channel"]:
        if not supports_split_channel_feed(root):
            raise PublishError("Beta index promotion of a stable package is held until production ParseFeed supports split channels.")
    update_index = build_index(index_channel, manifest["channel"], version, signed_package.stat().st_size,
                               gate.sha256_file(signed_package), update_url)
    if Path(qa_path.name).name != qa_path.name or "\\" in qa_path.name:
        raise PublishError("QA approval filename is unsafe for a public attachment.")
    assets = (
        Asset(update_name, signed_package.read_bytes()),
        Asset(manifest["archive"]["name"], candidate_zip.read_bytes()),
        Asset(manifest["sourceArchive"]["name"], source_zip.read_bytes()),
        Asset("candidate-record.json", record_path.read_bytes()),
        Asset(qa_path.name, qa_path.read_bytes()),
        Asset("signing-record.json", signing_record_path.read_bytes()),
    )
    page_files = tuple(PageFile("keys/" + name, (trust / name).read_bytes())
                       for name in ("root-public.pem", "issuer-public.pem", "manifest.min.json", "manifest.sig"))
    return PromotionPlan(release_tag, commit, index_channel, manifest["channel"], version,
                         assets, page_files, index_channel + ".json", update_index,
                         reuse_release=reuse_release)


def write_promotion(plan: PromotionPlan, output: Path) -> None:
    """Write a self-contained offline handoff directory; never overwrite it."""
    output = Path(output)
    if output.exists():
        raise PublishError("Promotion output already exists.")
    _validate_plan_index(plan)
    try:
        candidate_record = json.loads(next(item.data for item in plan.assets
                                           if item.name == "candidate-record.json"))
        parent_candidate = candidate_record["parentCommit"]
        source_commit = candidate_record["sourceCommit"]
    except (StopIteration, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublishError("Promotion requires the verified external candidate record and both repository pins.") from error
    if not COMMIT.fullmatch(parent_candidate) or not COMMIT.fullmatch(source_commit):
        raise PublishError("Candidate record has invalid pinned parent/source commits.")
    staging = Path(tempfile.mkdtemp(prefix=".fishgram-promotion-", dir=output.parent))
    output_created = False
    try:
        (staging / "release-assets").mkdir()
        (staging / "pages" / "keys").mkdir(parents=True)
        for asset in plan.assets:
            (staging / "release-assets" / asset.name).write_bytes(asset.data)
        for item in plan.page_files:
            destination = staging / "pages" / Path(*PurePosixPath(item.path).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.data)
        (staging / "pages" / plan.index_path).write_bytes(plan.index_bytes)
        record = {
            "schema": 1, "kind": "fishgram-offline-promotion", "repository": PRODUCT_REPOSITORY,
            "sourceRepository": SOURCE_REPOSITORY,
            "repositoryInputs": {
                PRODUCT_REPOSITORY: {"ref": "refs/heads/main", "reviewedCommit": plan.target_commit,
                                     "candidateCommit": parent_candidate},
                SOURCE_REPOSITORY: {"ref": "refs/heads/custom/main", "pinnedCommit": source_commit},
            },
            "tag": plan.tag, "targetCommit": plan.target_commit,
            "indexChannel": plan.index_channel, "packageChannel": plan.package_channel,
            "updateVersion": plan.version,
            "releaseAssets": [{"name": item.name, "size": len(item.data), "sha256": item.sha256}
                              for item in plan.assets],
            "pagesFiles": [{"path": item.path, "size": len(item.data), "sha256": item.sha256}
                           for item in plan.page_files],
            "index": {"path": plan.index_path, "size": len(plan.index_bytes),
                      "sha256": hashlib.sha256(plan.index_bytes).hexdigest()},
            "publicationStatus": "prepared-only; no GitHub or Pages writes performed",
        }
        (staging / "promotion.json").write_text(
            json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        output.mkdir()
        output_created = True
        shutil.copytree(staging / "release-assets", output / "release-assets")
        shutil.copytree(staging / "pages", output / "pages")
        shutil.copyfile(staging / "promotion.json", output / "promotion.json")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if output_created and output.exists():
            shutil.rmtree(output, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Verify signed inputs and write an offline promotion directory")
    prepare.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    prepare.add_argument("--commit", required=True, help="Reviewed main commit used for the release gate")
    prepare.add_argument("--index-channel", choices=("stable", "beta"),
                         help="Defaults to the signed package channel; beta can index a stable package.")
    prepare.add_argument("--reuse-release", action="store_true",
                         help="Verify an existing immutable stable release and promote only its stable index entry.")
    prepare.add_argument("--candidate-record", required=True, type=Path)
    prepare.add_argument("--qa", required=True, type=Path)
    prepare.add_argument("--signing-record", required=True, type=Path)
    prepare.add_argument("--candidate-zip", required=True, type=Path)
    prepare.add_argument("--source-zip", required=True, type=Path)
    prepare.add_argument("--signed-package", required=True, type=Path)
    prepare.add_argument("--packer", required=True, type=Path)
    prepare.add_argument("--trust", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "update-trust")
    prepare.add_argument("--issuer-id", required=True)
    prepare.add_argument("--output", required=True, type=Path)
    publish_parser = sub.add_parser("publish", help="Publish a previously verified promotion (explicit write opt-in required)")
    publish_parser.add_argument("--promotion", required=True, type=Path)
    publish_parser.add_argument("--enable-publication", action="store_true",
                                help="Explicitly authorize GitHub/Pages writes in this manual invocation")
    publish_parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    publish_parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    publish_parser.add_argument("--environment", default=os.environ.get("GITHUB_ENVIRONMENT", ""))
    publish_parser.add_argument("--approval-marker", default=os.environ.get("FISHGRAM_RELEASE_APPROVED", ""))
    provenance = sub.add_parser("verify-sign-run", help="Verify signing-run provenance against the exact candidate run")
    provenance.add_argument("--run-json", required=True, type=Path)
    provenance.add_argument("--jobs-json", required=True, type=Path)
    provenance.add_argument("--artifacts-json", required=True, type=Path)
    provenance.add_argument("--repository", required=True)
    provenance.add_argument("--run-id", required=True, type=int)
    provenance.add_argument("--candidate-run-id", required=True, type=int)
    provenance.add_argument("--expected-sha", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        if options.command == "verify-sign-run":
            verify_signing_run_provenance(_load_json(options.run_json, "Signing run"),
                                          _load_json(options.jobs_json, "Signing jobs"),
                                          _load_json(options.artifacts_json, "Signing artifacts"),
                                          options.repository, options.run_id,
                                          options.candidate_run_id, options.expected_sha)
            print("Exact successful signing-run provenance verified.")
            return 0
        if options.command == "publish":
            if options.enable_publication is not True:
                raise PublishError("Publication requires --enable-publication.")
            if options.approval_marker != "1":
                raise PublishError("Publication requires the workflow release-environment approval marker.")
            promotion = options.promotion.resolve(strict=True)
            try:
                metadata = _load_json(promotion / "promotion.json", "Promotion manifest")
                if (metadata.get("schema") != 1 or metadata.get("kind") != "fishgram-offline-promotion"
                        or metadata.get("repository") != PRODUCT_REPOSITORY
                        or metadata.get("sourceRepository") != SOURCE_REPOSITORY):
                    raise PublishError("Promotion manifest is not a FishGram release plan.")
                repository_inputs = metadata.get("repositoryInputs")
                if (not isinstance(repository_inputs, dict)
                        or set(repository_inputs) != {PRODUCT_REPOSITORY, SOURCE_REPOSITORY}
                        or repository_inputs[PRODUCT_REPOSITORY].get("ref") != "refs/heads/main"
                        or repository_inputs[PRODUCT_REPOSITORY].get("reviewedCommit") != metadata.get("targetCommit")
                        or repository_inputs[SOURCE_REPOSITORY].get("ref") != "refs/heads/custom/main"):
                    raise PublishError("Promotion manifest does not bind the fixed reviewed repository refs.")
                assets, pages = [], []
                asset_names = set()
                for entry in metadata["releaseAssets"]:
                    name, size, digest = entry["name"], entry["size"], entry["sha256"]
                    if (not isinstance(name, str) or Path(name).name != name or "\\" in name
                            or name in asset_names or type(size) is not int or size <= 0
                            or not isinstance(digest, str) or not HEX.fullmatch(digest)):
                        raise PublishError("Promotion release asset metadata is unsafe or duplicated.")
                    asset_names.add(name)
                    data = _promotion_file(promotion, "release-assets/" + name)
                    if (len(data) != size
                            or hashlib.sha256(data).hexdigest() != digest):
                        raise PublishError("Promotion release asset size or hash changed: " + name)
                    assets.append(Asset(name, data))
                page_paths = set()
                for entry in metadata["pagesFiles"]:
                    path_name, size, digest = entry["path"], entry["size"], entry["sha256"]
                    if (not isinstance(path_name, str) or not path_name.startswith("keys/")
                            or path_name in page_paths or type(size) is not int or size <= 0
                            or not isinstance(digest, str) or not HEX.fullmatch(digest)):
                        raise PublishError("Promotion trust-file metadata is unsafe or duplicated.")
                    page_paths.add(path_name)
                    data = _promotion_file(promotion, "pages/" + path_name)
                    if (len(data) != size
                            or hashlib.sha256(data).hexdigest() != digest):
                        raise PublishError("Promotion public trust file size or hash changed: " + path_name)
                    pages.append(PageFile(path_name, data))
                index_meta = metadata["index"]
                if (index_meta.get("path") not in {"stable.json", "beta.json"}
                        or type(index_meta.get("size")) is not int or index_meta["size"] <= 0
                        or not isinstance(index_meta.get("sha256"), str)
                        or not HEX.fullmatch(index_meta["sha256"])
                        or metadata.get("indexChannel") != index_meta["path"][:-5]):
                    raise PublishError("Promotion index metadata is invalid.")
                index_bytes = _promotion_file(promotion, "pages/" + index_meta["path"])
                if (len(index_bytes) != index_meta["size"]
                        or hashlib.sha256(index_bytes).hexdigest() != index_meta["sha256"]):
                    raise PublishError("Promotion channel index size or hash changed.")
                try:
                    candidate_record = json.loads(next(item.data for item in assets
                                                       if item.name == "candidate-record.json"))
                except (StopIteration, UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise PublishError("Promotion candidate record is missing or invalid.") from error
                if (repository_inputs[SOURCE_REPOSITORY].get("pinnedCommit") != candidate_record.get("sourceCommit")
                        or repository_inputs[PRODUCT_REPOSITORY].get("candidateCommit") != candidate_record.get("parentCommit")):
                    raise PublishError("Promotion repository pins differ from the exact candidate record.")
                plan = PromotionPlan(metadata["tag"], metadata["targetCommit"],
                                     metadata["indexChannel"], metadata["packageChannel"],
                                     metadata["updateVersion"], tuple(assets), tuple(pages),
                                     index_meta["path"], index_bytes)
            except (OSError, KeyError, TypeError, ValueError) as error:
                if isinstance(error, PublishError):
                    raise
                raise PublishError("Promotion directory is incomplete or malformed.") from error
            try:
                from github_release_transport import GitHubReleaseTransport
            except ImportError:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from github_release_transport import GitHubReleaseTransport
            api = GitHubReleaseTransport.from_environment()
            context = PublishContext(options.event, options.ref, options.environment,
                                     options.approval_marker == "1")
            promote(plan, api, enabled=True, context=context)
            print("Release, public asset hashes, Pages trust files and index read-back verified.")
            return 0
        plan = validate_inputs(options.root, options.commit, options.index_channel, options.reuse_release,
                               options.candidate_record, options.qa,
                               options.signing_record, options.candidate_zip, options.source_zip,
                               options.signed_package, options.packer, options.trust, options.issuer_id)
        write_promotion(plan, options.output)
    except (PublishError, OSError, ValueError, KeyError, RuntimeError) as error:
        prefix = "Publication refused: " if options.command == "publish" else "Promotion preparation refused: "
        print(prefix + str(error), file=sys.stderr)
        return 1
    print("Offline promotion prepared and all local candidate/signature checks passed; nothing was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
