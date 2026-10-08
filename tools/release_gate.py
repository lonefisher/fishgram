"""FishGram protected candidate release gate.

Verifies the exact reviewed, pinned candidate before any credential is read:
the parent/source/recursive commits, the product build manifest, the
downloaded candidate archive bytes, the reviewed Packer tool, and the human
QA approval file. A successful gate emits a release authorization that the
signing step consumes without recompiling. Nothing here creates a GitHub
release, tag, index entry, or credential; every check fails closed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import zipfile


class GateError(ValueError):
    """A release-gate requirement is not met."""


SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SUBMODULE_LINE = re.compile(r"^ [a-f0-9]{40} \S+( .+)?$")
VERSION = re.compile(r"^(\d+\.\d+\.\d+)-r(\d+)$")
PACKAGE_DOCS = {"LICENSE", "LEGAL", "Start-FishGram.cmd", "README.txt", "build-manifest.json"}
REQUIRED_CHECKS = (
    "searchEightAcceptance",
    "updaterFullMatrix",
    "sameCandidateCloudConclusion",
    "cleanWindows11",
    "hashVerification",
    "noAccountData",
)
PACKER_TARGETS = {"windows-x64": "win64"}


def git(root, *args):
    result = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-C", str(root), *args],
        capture_output=True, text=True)
    if result.returncode:
        raise GateError("Required Git metadata is unavailable: " + " ".join(args))
    return result.stdout.rstrip("\r\n")


def git_ok(root, *args):
    result = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-C", str(root), *args],
        capture_output=True, text=True)
    return result.returncode == 0


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha(value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise GateError(label + " must be a full lowercase commit id.")
    return value


def _safe_base(name, label):
    if not isinstance(name, str) or not name or Path(name).name != name or "\\" in name or ":" in name:
        raise GateError(label + " must be a safe file name.")
    return name


def _file_entry(entry, label):
    if not isinstance(entry, dict) or set(entry) != {"size", "sha256"}:
        raise GateError(label + " must record exactly size and sha256.")
    size, digest = entry["size"], entry["sha256"]
    if type(size) is not int or size <= 0 or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise GateError(label + " has an invalid size or sha256.")
    return entry


def _recipe_at(root, commit=None):
    if commit is None:
        data = (Path(root) / "fishgram.json").read_text(encoding="utf-8-sig")
    else:
        data = git(root, "show", commit + ":fishgram.json")
    recipe = json.loads(data)
    if not isinstance(recipe, dict) or recipe.get("product") != "FishGram":
        raise GateError("Unsupported build recipe.")
    return recipe


def _update_version(upstream, revision):
    major, minor, patch = map(int, upstream.split("."))
    base = major * 1_000_000 + minor * 1000 + patch
    return base, str((base << 32) | revision)


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise GateError("Build manifest must be a JSON object.")
    return manifest


def validate_manifest(manifest, recipe=None):
    """Structural and recipe binding checks for a product candidate record."""
    if manifest.get("schema") != 1 or manifest.get("product") != "FishGram":
        raise GateError("Not a FishGram build manifest.")
    match = VERSION.fullmatch(manifest.get("version") or "")
    if not match:
        raise GateError("Manifest version is missing or malformed.")
    upstream, revision = match.group(1), int(match.group(2))
    base, update = _update_version(upstream, revision)
    if manifest.get("updateVersion") != update:
        raise GateError("Manifest update version does not match the pinned recipe version.")
    if recipe is not None:
        if manifest.get("version") != f"{recipe['upstreamVersion']}-r{recipe['revision']}":
            raise GateError("Manifest version does not match the pinned recipe.")
        if manifest.get("channel") != recipe["channel"] or manifest.get("platform") != recipe["platform"]:
            raise GateError("Manifest channel or platform differs from the pinned recipe.")
        toolchain = manifest.get("toolchain")
        if not isinstance(toolchain, dict):
            raise GateError("Manifest toolchain record is missing.")
        msvc = toolchain.get("msvc")
        if (not isinstance(msvc, str) or not msvc.startswith(str(recipe["msvc"]) + ".")
                or toolchain.get("sdk") != recipe["windowsSdk"] or toolchain.get("qt") != recipe["qt"]):
            raise GateError("Manifest toolchain differs from the pinned recipe.")
        payload = set(recipe["payloadFiles"]) | set(recipe["payloadDlls"])
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != payload:
            raise GateError("Manifest files must bind exactly the recipe payload list.")
        if "recipeAutoUpdate" in manifest and manifest["recipeAutoUpdate"] is not bool(recipe["autoUpdate"]):
            raise GateError("Manifest recipe update flag differs from the pinned recipe.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise GateError("Manifest payload record is missing.")
    for name, entry in files.items():
        _safe_base(name, "Payload name")
        _file_entry(entry, "Payload record " + str(name))
    _sha(manifest.get("parentCommit"), "Parent commit")
    _sha(manifest.get("sourceCommit"), "Source commit")
    if manifest.get("identity") != "product":
        raise GateError("Candidate must use the product identity; test builds cannot be released.")
    if manifest.get("productCandidate") is not True:
        raise GateError("Manifest was not produced by a protected product-candidate build.")
    if manifest.get("testUpdateTrust") is not False:
        raise GateError("Disposable test update trust can never be released.")
    if manifest.get("autoUpdate") is not True:
        raise GateError("Candidate must be built with the FishGram update system enabled.")
    if manifest.get("releaseReady") is not False:
        raise GateError("A manifest must not mark itself release ready.")
    submodules = manifest.get("submodules")
    if not isinstance(submodules, list) or not submodules:
        raise GateError("Manifest must record the pinned recursive dependencies.")
    for line in submodules:
        if not isinstance(line, str) or not SUBMODULE_LINE.fullmatch(line):
            raise GateError("Recursive dependency record is missing or inconsistent.")
    archive = manifest.get("archive")
    if not isinstance(archive, dict) or set(archive) != {"name", "sha256", "size"}:
        raise GateError("Manifest archive record is incomplete.")
    _safe_base(archive["name"], "Archive name")
    if archive["name"] != f"FishGram-{manifest['version']}-{manifest['platform']}-candidate.zip":
        raise GateError("Archive name does not match the recorded candidate.")
    if type(archive["size"]) is not int or archive["size"] <= 0 or not DIGEST.fullmatch(archive["sha256"]):
        raise GateError("Archive record has an invalid size or sha256.")
    tools = manifest.get("tools")
    if tools is not None:
        if not isinstance(tools, dict):
            raise GateError("Manifest tools record is malformed.")
        for name, entry in tools.items():
            _safe_base(name, "Tool name")
            _file_entry(entry, "Tool record " + str(name))
    return {"upstream": upstream, "revision": revision, "versionBase": base}


def check_checkout(root, commit):
    """Candidate-build gate: pinned recursive checkout before any credential."""
    commit = _sha(commit, "Approved commit")
    root = Path(root)
    head = git(root, "rev-parse", "HEAD").lower()
    if head != commit:
        raise GateError("Checkout is not the approved reviewed commit.")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise GateError("Parent checkout has uncommitted tracked changes.")
    pointer = git(root, "ls-files", "--stage", "--", "tdesktop")
    match = re.fullmatch(r"160000 ([a-f0-9]{40}) 0\ttdesktop", pointer)
    if not match:
        raise GateError("Pinned source gitlink is missing.")
    source = root / "tdesktop"
    if git(source, "rev-parse", "HEAD").lower() != match.group(1):
        raise GateError("Source checkout does not match the parent gitlink.")
    if git(source, "status", "--porcelain", "--untracked-files=no"):
        raise GateError("Source checkout has uncommitted tracked changes.")
    lines = git(source, "submodule", "status", "--recursive").splitlines()
    if not lines or any(not SUBMODULE_LINE.fullmatch(line) for line in lines):
        raise GateError("Recursive dependencies are missing, moved, or conflicted.")


def check_private_workspace(root):
    """Require a pristine checkout before credentials are exposed to a build."""
    root = Path(root)
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise GateError("Workspace contains uncommitted or untracked files before credentials.")
    for name in (".private", "logs", "build-modified", "reports"):
        path = root / name
        if path.exists() or path.is_symlink():
            raise GateError("Private build material already exists before credentials: " + name)
    tracked = git(root, "ls-files", "-z").split("\0")
    forbidden = (".private/", "logs/", "build-modified/", "api-", "root-private", "issuer-private")
    if any(any(part.casefold() in item.casefold() for part in forbidden) for item in tracked if item):
        raise GateError("A private credential or build path is committed in the checkout.")


def package_candidate(root, record_path, output):
    """Create the internal ZIP from the explicitly recorded product build."""
    import shutil
    root, output = Path(root).resolve(), Path(output).resolve()
    record = json.loads(Path(record_path).read_text(encoding="utf-8-sig"))
    recipe = _recipe_at(root)
    if record.get("identity") != "product" or record.get("productCandidate") is not True:
        raise GateError("Only a protected product candidate can be packaged.")
    if record.get("autoUpdate") is not True or record.get("testUpdateTrust") is not False:
        raise GateError("Candidate build must enable FishGram updates with production trust.")
    if record.get("recipeAutoUpdate") is not recipe.get("autoUpdate"):
        raise GateError("Candidate must preserve the reviewed recipe autoUpdate value.")
    if record.get("version") != f"{recipe['upstreamVersion']}-r{recipe['revision']}":
        raise GateError("Build record does not match the reviewed recipe.")
    if record.get("parentCommit") != git(root, "rev-parse", "HEAD"):
        raise GateError("Build record parent commit differs from HEAD.")
    source = root / "tdesktop"
    if record.get("sourceCommit") != git(source, "rev-parse", "HEAD"):
        raise GateError("Build record source commit differs from the pinned checkout.")
    payload_names = set(recipe["payloadFiles"]) | set(recipe["payloadDlls"])
    validate_manifest({
        "schema": 1, "product": "FishGram", "version": record["version"],
        "updateVersion": _update_version(recipe["upstreamVersion"], recipe["revision"])[1],
        "channel": recipe["channel"], "platform": recipe["platform"],
        "toolchain": record.get("toolchain"), "files": record.get("files"),
        "parentCommit": record.get("parentCommit"), "sourceCommit": record.get("sourceCommit"),
        "identity": record.get("identity"), "productCandidate": record.get("productCandidate"),
        "testUpdateTrust": record.get("testUpdateTrust"), "autoUpdate": record.get("autoUpdate"),
        "recipeAutoUpdate": record.get("recipeAutoUpdate"), "releaseReady": False,
        "submodules": git(source, "submodule", "status", "--recursive").splitlines(),
        "archive": {"name": f"FishGram-{record['version']}-{recipe['platform']}-candidate.zip",
                    "sha256": "0" * 64, "size": 1},
    }, recipe)
    build = root / "build-modified" / "Release"
    for name in payload_names:
        _safe_base(name, "Payload name")
        path = build / name
        entry = record["files"][name]
        if not path.is_file() or path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
            raise GateError("Build output differs from recorded bytes: " + name)
    packer = build / "Packer.exe"
    verify_tool(record, "Packer.exe", packer)
    if output.exists() or output.is_relative_to(root):
        raise GateError("Candidate output must be a new directory outside the checkout.")
    output.mkdir(parents=True)
    version = record["version"]
    archive_path = output / f"FishGram-{version}-{recipe['platform']}-candidate.zip"
    manifest = {
        "schema": 1, "product": "FishGram", "version": version,
        "updateVersion": _update_version(recipe["upstreamVersion"], recipe["revision"])[1],
        "channel": recipe["channel"], "platform": recipe["platform"],
        "identity": "product", "productCandidate": True, "autoUpdate": True,
        "recipeAutoUpdate": recipe["autoUpdate"], "testUpdateTrust": False,
        "releaseReady": False, "parentCommit": record["parentCommit"],
        "sourceCommit": record["sourceCommit"], "toolchain": record["toolchain"],
        "files": record["files"],
        "submodules": git(source, "submodule", "status", "--recursive").splitlines(),
        "tools": record.get("tools"),
    }
    try:
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for name in sorted(payload_names):
                bundle.write(build / name, name)
            bundle.write(source / "LICENSE", "LICENSE")
            bundle.write(source / "LEGAL", "LEGAL")
            bundle.writestr("Start-FishGram.cmd", '@echo off\r\nsetlocal\r\nif not exist "%~dp0FishGramData" mkdir "%~dp0FishGramData"\r\nstart "" "%~dp0Telegram.exe" -workdir "%~dp0FishGramData"\r\n')
            bundle.writestr("README.txt", "FishGram internal candidate for Windows 11 x64. This is not a public release. The FishGram updater signature is not Windows Authenticode.\r\n")
            bundle.writestr("build-manifest.json", json.dumps(manifest, indent=2) + "\n")
        manifest["archive"] = {"name": archive_path.name, "sha256": sha256_file(archive_path), "size": archive_path.stat().st_size}
        (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(packer, output / "Packer.exe")
    except Exception:
        for item in output.iterdir():
            item.unlink(missing_ok=True)
        output.rmdir()
        raise
    return manifest


def check_pins(root, commit, manifest, tracked=()):
    """Sign-time gate: reviewed pins using Git metadata only; nothing is built."""
    commit = _sha(commit, "Approved commit")
    root = Path(root)
    if git(root, "rev-parse", "HEAD").lower() != commit:
        raise GateError("Checkout is not the approved reviewed commit.")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise GateError("Parent checkout has uncommitted tracked changes.")
    for path in tracked:
        if not git_ok(root, "ls-files", "--error-unmatch", "--", str(path)):
            raise GateError("Required reviewed file is not committed: " + str(path))
    parent = _sha(manifest.get("parentCommit"), "Manifest parent commit")
    if not git_ok(root, "cat-file", "-e", parent + "^{commit}"):
        raise GateError("Recorded candidate parent commit is unknown to this repository.")
    if not git_ok(root, "merge-base", "--is-ancestor", parent, "HEAD"):
        raise GateError("Recorded candidate parent commit is not on the protected branch history.")
    pointer = git(root, "ls-tree", parent, "--", "tdesktop")
    match = re.fullmatch(r"160000 commit ([a-f0-9]{40})\ttdesktop", pointer)
    if not match or match.group(1) != _sha(manifest.get("sourceCommit"), "Manifest source commit"):
        raise GateError("Recorded source commit does not match the pinned gitlink.")
    recipe = _recipe_at(root, parent)
    validate_manifest(manifest, recipe)


def verify_package(manifest, package):
    """Re-verify the downloaded candidate archive against the build manifest."""
    package = Path(package)
    archive = manifest["archive"]
    if package.name != archive["name"]:
        raise GateError("Candidate archive name does not match the manifest.")
    if package.stat().st_size != archive["size"] or sha256_file(package) != archive["sha256"]:
        raise GateError("Candidate archive bytes differ from the recorded candidate.")
    expected = set(manifest["files"]) | PACKAGE_DOCS
    with zipfile.ZipFile(package) as bundle:
        names = [info.filename for info in bundle.infolist() if not info.is_dir()]
        if len(names) != len(set(names)):
            raise GateError("Candidate archive contains duplicate entries.")
        for info in bundle.infolist():
            name = info.filename
            if name != Path(name).name or "\\" in name or ":" in name or ".." in name:
                raise GateError("Candidate archive contains an unsafe entry: " + name)
            if info.flag_bits & 0x1:
                raise GateError("Candidate archive must not contain encrypted entries.")
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and ((mode & 0o170000) not in (0, 0o100000, 0o040000)):
                raise GateError("Candidate archive contains a link or special file: " + name)
        if set(names) != expected:
            raise GateError("Candidate archive content differs from the approved payload list.")
        for name, entry in manifest["files"].items():
            with bundle.open(name) as stream:
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise GateError("Candidate payload differs from the recorded bytes: " + name)
        embedded = json.loads(bundle.read("build-manifest.json").decode("utf-8-sig"))
    outer = {key: value for key, value in manifest.items() if key not in ("archive", "sourceArchive")}
    if embedded != outer:
        raise GateError("Embedded build manifest differs from the candidate record.")


def verify_tool(manifest, name, path):
    """Verify a reviewed tool binary (for example Packer.exe) from the artifact."""
    tools = manifest.get("tools")
    if not isinstance(tools, dict) or name not in tools:
        raise GateError("Reviewed tool is not recorded in the manifest: " + str(name))
    _safe_base(name, "Tool name")
    entry = tools[name]
    path = Path(path)
    if path.name != name:
        raise GateError("Tool file name does not match the manifest.")
    if path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
        raise GateError("Tool bytes differ from the reviewed build: " + name)


def verify_source_archive(manifest, source_archive):
    """Verify the corresponding-source ZIP against the same recursive pins."""
    source_archive = Path(source_archive)
    if "sourceArchive" in manifest:
        record = manifest["sourceArchive"]
        if (not isinstance(record, dict) or set(record) != {"name", "size", "sha256"}
                or record["name"] != source_archive.name
                or type(record["size"]) is not int or record["size"] <= 0
                or not isinstance(record["sha256"], str) or not DIGEST.fullmatch(record["sha256"])
                or source_archive.stat().st_size != record["size"]
                or sha256_file(source_archive) != record["sha256"]):
            raise GateError("Corresponding-source archive differs from the candidate build record.")
    with zipfile.ZipFile(source_archive) as bundle:
        names = [item.filename for item in bundle.infolist()]
        if len(names) != len(set(names)) or "source-manifest.json" not in names:
            raise GateError("Corresponding-source archive has duplicate entries or no source manifest.")
        source_manifest = json.loads(bundle.read("source-manifest.json").decode("utf-8-sig"))
        if (source_manifest.get("schema") != 1 or source_manifest.get("product") != "FishGram"
                or source_manifest.get("parentCommit") != manifest["parentCommit"]
                or source_manifest.get("version") != manifest["version"]):
            raise GateError("Corresponding-source archive belongs to a different candidate.")
        repositories = source_manifest.get("repositories")
        files = source_manifest.get("files")
        if not isinstance(repositories, list) or not isinstance(files, dict):
            raise GateError("Corresponding-source manifest is malformed.")
        module_map = {item.get("path"): item.get("commit") for item in repositories if isinstance(item, dict)}
        if len(module_map) != len(repositories) or module_map.get(".") != manifest["parentCommit"]:
            raise GateError("Corresponding-source repository identity is missing or duplicated.")
        if module_map.get("tdesktop") != manifest["sourceCommit"]:
            raise GateError("Corresponding-source archive has a different pinned source commit.")
        expected_modules = sorted(re.findall(r"^ ([a-f0-9]{40}) ", line)[0] for line in manifest["submodules"])
        actual_modules = sorted(value for path, value in module_map.items() if path not in (".", "tdesktop"))
        if actual_modules != expected_modules:
            raise GateError("Corresponding-source recursive dependencies differ from the candidate.")
        expected_names = set(files) | {"source-manifest.json"}
        if set(names) != expected_names:
            raise GateError("Corresponding-source archive contents differ from its manifest.")
        for name, entry in files.items():
            parts = name.split("/") if isinstance(name, str) else []
            if (not parts or "\\" in name or ":" in name or name.startswith("/")
                    or any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in parts)):
                raise GateError("Corresponding-source archive contains an unsafe path.")
            if not isinstance(entry, dict) or type(entry.get("size")) is not int or entry["size"] < 0 or not DIGEST.fullmatch(entry.get("sha256", "")):
                raise GateError("Corresponding-source file record is malformed: " + name)
            data = bundle.read(name)
            if len(data) != entry["size"] or sha256_bytes(data) != entry["sha256"]:
                raise GateError("Corresponding-source bytes differ from its manifest: " + name)
        try:
            config = json.loads(bundle.read('config/license-sources.json').decode('utf-8-sig'))
        except (KeyError, ValueError) as error:
            raise GateError('Corresponding-source license configuration is missing or malformed.') from error
    spec = importlib.util.spec_from_file_location('fishgram_gate_licenses', Path(__file__).with_name('license_inventory.py'))
    licenses = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(licenses)
    try:
        licenses.verify_license_evidence(source_archive, licenses.validate_config(config))
    except (ValueError, TypeError, KeyError) as error:
        raise GateError('Corresponding-source license evidence was rejected: ' + str(error)) from error


def bind_source(manifest, source_archive, output):
    """Record source archive bytes once in the protected build's external record."""
    verify_source_archive(manifest, source_archive)
    if "sourceArchive" in manifest:
        raise GateError("Candidate source archive is already bound.")
    record = dict(manifest)
    archive = Path(source_archive)
    record["sourceArchive"] = {"name": archive.name, "size": archive.stat().st_size,
                              "sha256": sha256_file(archive)}
    with Path(output).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
    return record


def extract_payload(manifest, package, output):
    """Extract only the approved payload files after full archive verification."""
    verify_package(manifest, package)
    output = Path(output)
    if output.exists():
        raise GateError("Payload extraction directory already exists.")
    output.mkdir(parents=True)
    with zipfile.ZipFile(package) as bundle:
        for name in manifest["files"]:
            data = bundle.read(name)
            target = output / name
            with target.open("xb") as stream:
                stream.write(data)
    return sorted(manifest["files"])


def qa_template(manifest, output):
    """Emit the human QA approval input bound to this candidate's hashes."""
    candidate = {
        "version": manifest["version"],
        "parentCommit": manifest["parentCommit"],
        "sourceCommit": manifest["sourceCommit"],
        "archive": dict(manifest["archive"]),
        "files": {name: {"sha256": entry["sha256"]} for name, entry in manifest["files"].items()},
    }
    if "sourceArchive" in manifest:
        candidate["sourceArchive"] = manifest["sourceArchive"]
    template = {
        "schema": 1,
        "kind": "fishgram-candidate-qa",
        "approver": "",
        "approvedAt": "",
        "environment": "",
        "candidate": candidate,
        "checks": {name: False for name in REQUIRED_CHECKS},
        "notes": "",
    }
    output = Path(output)
    if output.exists():
        raise GateError("Refusing to overwrite an existing QA file.")
    output.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return template


def verify_qa(manifest, qa):
    """Validate the human QA approval: schema, identity, and exact binding."""
    if not isinstance(qa, dict) or qa.get("schema") != 1 or qa.get("kind") != "fishgram-candidate-qa":
        raise GateError("Not a FishGram candidate QA approval.")
    allowed = {"schema", "kind", "approver", "approvedAt", "environment", "candidate", "checks", "notes"}
    if set(qa) - allowed or not {"schema", "kind", "approver", "approvedAt", "environment", "candidate", "checks"} <= set(qa):
        raise GateError("QA approval has missing or unknown fields.")
    approver = qa["approver"]
    if not isinstance(approver, str) or not USERNAME.fullmatch(approver):
        raise GateError("QA approver must be a valid maintainer account name.")
    stamp = qa["approvedAt"]
    if not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
        raise GateError("QA approval time must be an RFC3339 UTC timestamp.")
    approved = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    if approved > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise GateError("QA approval time is in the future.")
    if not isinstance(qa["environment"], str) or not qa["environment"].strip() or len(qa["environment"]) > 500:
        raise GateError("QA environment description is required.")
    if "notes" in qa and (not isinstance(qa["notes"], str) or len(qa["notes"]) > 4000):
        raise GateError("QA notes must be a bounded string.")
    candidate = qa["candidate"]
    fields = {"version", "parentCommit", "sourceCommit", "archive", "files"}
    if "sourceArchive" in manifest:
        fields.add("sourceArchive")
    if not isinstance(candidate, dict) or set(candidate) != fields:
        raise GateError("QA candidate binding is malformed.")
    if (candidate["version"] != manifest["version"]
            or candidate["parentCommit"] != manifest["parentCommit"]
            or candidate["sourceCommit"] != manifest["sourceCommit"]):
        raise GateError("QA approval does not match this candidate's version or commits.")
    if candidate["archive"] != manifest["archive"]:
        raise GateError("QA approval was issued for a different candidate archive.")
    if "sourceArchive" in manifest and candidate["sourceArchive"] != manifest["sourceArchive"]:
        raise GateError("QA approval was issued for different corresponding-source bytes.")
    expected_files = {name: {"sha256": entry["sha256"]} for name, entry in manifest["files"].items()}
    if candidate["files"] != expected_files:
        raise GateError("QA approval was issued for different candidate payload hashes.")
    checks = qa["checks"]
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_CHECKS):
        raise GateError("QA checks must cover exactly the required items.")
    if any(value is not True for value in checks.values()):
        raise GateError("Every required QA check must be explicitly passed.")


def authorize(manifest, qa, qa_name, qa_bytes, output):
    """Emit the release authorization consumed by signing; never compiles."""
    info = validate_manifest(manifest)
    verify_qa(manifest, qa)
    tools = manifest.get("tools") or {}
    if "Packer.exe" not in tools:
        raise GateError("Signing requires the reviewed Packer tool recorded at build time.")
    if manifest["platform"] not in PACKER_TARGETS:
        raise GateError("No update-package target for platform: " + str(manifest["platform"]))
    authorization = {
        "schema": 1,
        "kind": "fishgram-release-authorization",
        "version": manifest["version"],
        "updateVersion": manifest["updateVersion"],
        "channel": manifest["channel"],
        "platform": manifest["platform"],
        "parentCommit": manifest["parentCommit"],
        "sourceCommit": manifest["sourceCommit"],
        "submodules": list(manifest["submodules"]),
        "archive": dict(manifest["archive"]),
        "payload": manifest["files"],
        "packer": {
            "tool": "Packer.exe",
            "toolSha256": tools["Packer.exe"]["sha256"],
            "target": PACKER_TARGETS[manifest["platform"]],
            "versionBase": info["versionBase"],
            "counter": info["revision"],
            "channel": manifest["channel"],
            "files": sorted(manifest["files"]),
        },
        "qa": {
            "file": _safe_base(qa_name, "QA file name"),
            "sha256": sha256_bytes(qa_bytes),
            "approver": qa["approver"],
            "approvedAt": qa["approvedAt"],
        },
        "allowedActions": ["sign-update-package"],
        "blocked": {
            "githubRelease": "No release, tag, or attachment is created by this chain.",
            "indexPublish": "Channel index updates only after public download verification.",
            "authenticode": "Windows Authenticode is separate from the FishGram v2 update signature.",
        },
    }
    output = Path(output)
    if output.exists():
        raise GateError("Refusing to overwrite an existing release authorization.")
    output.write_text(json.dumps(authorization, indent=2) + "\n", encoding="utf-8")
    return authorization


def _manifest_and_recipe(options):
    manifest = load_manifest(options.manifest)
    recipe = None
    if getattr(options, "root", None):
        recipe = _recipe_at(options.root)
    elif getattr(options, "recipe", None):
        recipe = json.loads(Path(options.recipe).read_text(encoding="utf-8-sig"))
    return manifest, recipe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)

    checkout = commands.add_parser("check-checkout", help="Verify the pinned recursive checkout before credentials")
    checkout.add_argument("--root", required=True)
    checkout.add_argument("--commit", required=True)

    pins = commands.add_parser("check-pins", help="Verify reviewed pins and manifest against Git metadata only")
    pins.add_argument("--root", required=True)
    pins.add_argument("--commit", required=True)
    pins.add_argument("--manifest", required=True)
    pins.add_argument("--tracked", action="append", default=[])

    private = commands.add_parser("check-private-workspace", help="Require a clean workspace before credentials")
    private.add_argument("--root", required=True)

    package = commands.add_parser("package-candidate", help="Package recorded product build bytes")
    package.add_argument("--root", required=True)
    package.add_argument("--record", required=True)
    package.add_argument("--output", required=True)

    vm = commands.add_parser("verify-manifest", help="Validate a build manifest against a recipe")
    vm.add_argument("--manifest", required=True)
    vm.add_argument("--root")
    vm.add_argument("--recipe")

    vp = commands.add_parser("verify-package", help="Verify a downloaded candidate archive")
    vp.add_argument("--manifest", required=True)
    vp.add_argument("--package", required=True)
    vp.add_argument("--root")
    vp.add_argument("--recipe")

    vt = commands.add_parser("verify-tool", help="Verify a reviewed tool binary from the artifact")
    vt.add_argument("--manifest", required=True)
    vt.add_argument("--name", required=True)
    vt.add_argument("--path", required=True)

    vs = commands.add_parser("verify-source", help="Verify corresponding source and recursive pins")
    vs.add_argument("--manifest", required=True)
    vs.add_argument("--source", required=True)
    bs = commands.add_parser("bind-source", help="Bind source ZIP bytes to a new candidate record")
    bs.add_argument("--manifest", required=True)
    bs.add_argument("--source", required=True)
    bs.add_argument("--output", required=True)

    xp = commands.add_parser("extract-payload", help="Extract only approved payload files after verification")
    xp.add_argument("--manifest", required=True)
    xp.add_argument("--package", required=True)
    xp.add_argument("--output", required=True)
    xp.add_argument("--root")
    xp.add_argument("--recipe")

    qt = commands.add_parser("qa-template", help="Write a QA approval template bound to the candidate")
    qt.add_argument("--manifest", required=True)
    qt.add_argument("--output", required=True)

    vq = commands.add_parser("verify-qa", help="Verify a QA approval file against the manifest")
    vq.add_argument("--manifest", required=True)
    vq.add_argument("--qa", required=True)
    vq.add_argument("--root")
    vq.add_argument("--recipe")

    az = commands.add_parser("authorize", help="Emit the release authorization for the signing step")
    az.add_argument("--manifest", required=True)
    az.add_argument("--qa", required=True)
    az.add_argument("--output", required=True)
    az.add_argument("--root")
    az.add_argument("--recipe")

    options = parser.parse_args()
    try:
        if options.operation == "check-checkout":
            check_checkout(options.root, options.commit)
            print("Pinned recursive checkout verified before credentials.")
        elif options.operation == "check-private-workspace":
            check_private_workspace(options.root)
            print("Workspace private-material guard passed before credentials.")
        elif options.operation == "package-candidate":
            package_candidate(options.root, options.record, options.output)
            print("Internal product candidate packaged from recorded build bytes.")
        elif options.operation == "check-pins":
            manifest = load_manifest(options.manifest)
            check_pins(options.root, options.commit, manifest, options.tracked)
            print("Reviewed pins verified; manifest matches the recorded commits and recipe.")
        elif options.operation == "verify-manifest":
            manifest, recipe = _manifest_and_recipe(options)
            validate_manifest(manifest, recipe)
            print("Build manifest verified.")
        elif options.operation == "verify-package":
            manifest, recipe = _manifest_and_recipe(options)
            validate_manifest(manifest, recipe)
            verify_package(manifest, options.package)
            print("Candidate archive verified against the build manifest.")
        elif options.operation == "verify-tool":
            manifest = load_manifest(options.manifest)
            validate_manifest(manifest)
            verify_tool(manifest, options.name, options.path)
            print("Reviewed tool verified: " + options.name)
        elif options.operation == "verify-source":
            manifest = load_manifest(options.manifest)
            validate_manifest(manifest)
            verify_source_archive(manifest, options.source)
            print("Corresponding source and recursive pins verified.")
        elif options.operation == "bind-source":
            manifest = load_manifest(options.manifest)
            validate_manifest(manifest)
            bind_source(manifest, options.source, options.output)
            print("Corresponding-source bytes bound to the protected candidate record.")
        elif options.operation == "extract-payload":
            manifest, recipe = _manifest_and_recipe(options)
            validate_manifest(manifest, recipe)
            names = extract_payload(manifest, options.package, options.output)
            print("Extracted approved payload: " + ", ".join(names))
        elif options.operation == "qa-template":
            manifest = load_manifest(options.manifest)
            validate_manifest(manifest)
            qa_template(manifest, options.output)
            print("QA approval template written: " + options.output)
        elif options.operation == "verify-qa":
            manifest, recipe = _manifest_and_recipe(options)
            validate_manifest(manifest, recipe)
            verify_qa(manifest, json.loads(Path(options.qa).read_text(encoding="utf-8-sig")))
            print("QA approval verified against the candidate manifest.")
        elif options.operation == "authorize":
            manifest, recipe = _manifest_and_recipe(options)
            if recipe is not None:
                validate_manifest(manifest, recipe)
            qa_bytes = Path(options.qa).read_bytes()
            qa = json.loads(qa_bytes.decode("utf-8-sig"))
            authorize(manifest, qa, Path(options.qa).name, qa_bytes, options.output)
            print("Release authorization written: " + options.output)
    except GateError as error:
        parser.exit(2, "GATE FAILED: " + str(error) + "\n")
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile, KeyError) as error:
        parser.exit(2, "GATE FAILED: " + type(error).__name__ + ": " + str(error) + "\n")


if __name__ == "__main__":
    main()
