"""Inventory license files and evidence in a pinned FishGram source archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import urlparse
import zipfile


class LicenseInventoryError(ValueError):
    """The archive or its fixed source pins cannot be safely verified."""


LICENSE_NAME = re.compile(r"^(?:licen[cs]e|copying|legal|notice)(?:[._-].*)?$", re.I)
LICENSE_ID_NAME = re.compile(r"^(?:gpl|lgpl|agpl|mpl|fdl|ofl|bsd|apache)(?:[-_.].*)?$", re.I)
README_NAME = re.compile(r"^readme(?:[._-].*)?$", re.I)
SPDX = re.compile(r"SPDX-License-Identifier\s*:", re.I)
LICENSE_WORDS = re.compile(
    r"\b(?:license|licence|licensed|licensing|copyright|spdx|gnu gpl|lgpl|mpl|bsd)\b",
    re.I,
)
FULL_LICENSE = re.compile(
    r"GNU (?:LESSER |LIBRARY |AFFERO )?GENERAL PUBLIC LICENSE|"
    r"GNU Free Documentation License|Creative Commons Legal Code|CC0 1\.0 Universal|"
    r"Permission is hereby granted|"
    r"TERMS AND CONDITIONS FOR USE|Mozilla Public License Version|"
    r"Redistribution and use in source and binary forms|"
    r"MIT License\s+Copyright|Apache License\s+Version",
    re.I,
)
GNU_LICENSE = re.compile(r"GNU (?:LESSER |LIBRARY |AFFERO )?GENERAL PUBLIC LICENSE", re.I)
URL = re.compile(r"https?://[^\s<>\]\[()\"']+", re.I)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        name
        and not path.is_absolute()
        and path.as_posix() == name
        and "\\" not in name
        and ":" not in name
        and all(part not in ("", ".", "..") for part in path.parts)
    )


def _repo_owns_path(name: str, repo_path: str, repo_paths: set[str]) -> bool:
    prefix = "" if repo_path == "." else repo_path + "/"
    if repo_path != "." and not name.startswith(prefix):
        return False
    for child in repo_paths:
        if child == repo_path or child == ".":
            continue
        if repo_path == "." or child.startswith(prefix):
            if name.startswith(child + "/"):
                return False
    return True


def _relative_to_repo(name: str, repo_path: str) -> str:
    return name if repo_path == "." else name[len(repo_path) + 1:]


def _license_named(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    base = parts[-1]
    return bool(
        LICENSE_NAME.fullmatch(base)
        or LICENSE_ID_NAME.fullmatch(base)
        or any(p.casefold() in {"licenses", "licences"} for p in parts[:-1])
    )


def _text(data: bytes) -> str | None:
    if len(data) > 1024 * 1024 or b"\0" in data[:4096]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _is_full_license(text: str) -> bool:
    if GNU_LICENSE.search(text):
        return len(text) >= 5000 and bool(
            re.search(r"TERMS AND CONDITIONS|\b[0-9]+\. (?:Additional )?Definitions", text, re.I)
        )
    if re.search(r"Creative Commons Legal Code|CC0 1\.0 Universal", text, re.I):
        return len(text) >= 5000
    if re.search(r"Apache License\s+Version", text, re.I):
        return len(text) >= 5000 and "TERMS AND CONDITIONS FOR USE" in text
    if re.search(r"Mozilla Public License Version", text, re.I):
        return len(text) >= 10000
    if re.search(r"Redistribution and use in source and binary forms", text, re.I):
        return len(text) >= 1000 and bool(re.search(r"Neither the name|All rights reserved", text, re.I))
    if re.search(r"Permission is hereby granted", text, re.I):
        return len(text) >= 700 and bool(re.search(r"THE SOFTWARE IS PROVIDED|FITNESS FOR A PARTICULAR PURPOSE", text, re.I))
    return bool(FULL_LICENSE.search(text)) and len(text) >= 10000


def inventory_archive(archive_path: str | Path, pin_config: dict | None = None) -> dict:
    """Return path-level evidence without interpreting license compatibility."""
    archive_path = Path(archive_path)
    try:
        archive_bytes = archive_path.read_bytes()
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise LicenseInventoryError("Duplicate archive member path.")
            if any(not _safe_member(name) for name in names):
                raise LicenseInventoryError("Unsafe archive member path.")
            if "source-manifest.json" not in names:
                raise LicenseInventoryError("source-manifest.json is missing.")
            contents = {name: archive.read(name) for name in names if not name.endswith("/")}
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise LicenseInventoryError("Source archive could not be read safely.") from error

    manifest_bytes = contents["source-manifest.json"]
    try:
        source_manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LicenseInventoryError("source-manifest.json is invalid JSON.") from error
    repositories = source_manifest.get("repositories")
    file_manifest = source_manifest.get("files")
    if not isinstance(repositories, list) or not isinstance(file_manifest, dict):
        raise LicenseInventoryError("Source manifest lacks repositories or file hashes.")
    repo_paths = {item.get("path") for item in repositories if isinstance(item, dict)}
    if len(repo_paths) != len(repositories) or any(not isinstance(path, str) for path in repo_paths):
        raise LicenseInventoryError("Source manifest repository paths are invalid.")
    if "." not in repo_paths:
        raise LicenseInventoryError("Source manifest has no parent repository pin.")
    if set(contents) - {"source-manifest.json"} != set(file_manifest):
        raise LicenseInventoryError("Archive members do not match source-manifest file list.")
    for name, metadata in file_manifest.items():
        if not _safe_member(name) or not isinstance(metadata, dict):
            raise LicenseInventoryError("Source manifest contains an unsafe file entry.")
        data = contents[name]
        if metadata.get("size") != len(data) or metadata.get("sha256") != sha256(data):
            raise LicenseInventoryError(f"Source file hash or size mismatch: {name}")

    official_sources = _source_presence(contents, pin_config or {})
    official_by_name = {source["name"]: source for source in official_sources}
    repos = []
    for repo in repositories:
        root = repo["path"]
        if not isinstance(root, str) or (root != "." and not _safe_member(root)):
            raise LicenseInventoryError("Source manifest has an invalid repository path.")
        if not re.fullmatch(r"[0-9a-f]{40}", repo.get("commit", "")):
            raise LicenseInventoryError(f"Invalid commit pin for repository {root}.")
        license_files = []
        full_license_paths = []
        readme_evidence = []
        spdx_paths = []
        license_notice_paths = []
        copyright_notice_paths = []
        linked_only_evidence = []
        linked_urls = set()
        required_official_sources = []
        linked_urls = set()
        for name, data in contents.items():
            if name == "source-manifest.json" or not _repo_owns_path(name, root, repo_paths):
                continue
            rel = _relative_to_repo(name, root)
            text = _text(data)
            if text is None:
                continue
            is_named = _license_named(rel)
            is_readme = bool(README_NAME.fullmatch(PurePosixPath(rel).name))
            # A complete permission/warranty notice also counts when retained
            # verbatim in a source header. SPDX and short link notices do not.
            is_full = _is_full_license(text)
            if is_named:
                if is_full:
                    classification = "full-license-text"
                elif URL.search(text) and LICENSE_WORDS.search(text):
                    classification = "linked-or-notice-only"
                elif LICENSE_WORDS.search(text):
                    classification = "license-notice-no-full-text"
                else:
                    classification = "notice-or-unclassified"
                license_files.append({"path": name, "classification": classification, "sha256": sha256(data)})
            if is_full:
                full_license_paths.append(name)
            if is_readme and LICENSE_WORDS.search(text):
                readme_evidence.append(name)
            if SPDX.search(text):
                spdx_paths.append(name)
            if re.search(r"\b(?:licensed under|released under|license(?:d)?\s*:)\b", text, re.I):
                license_notice_paths.append(name)
            if re.search(r"\bCopyright\s*(?:\(c\)|©)?\s*\d{4}", text, re.I):
                copyright_notice_paths.append(name)
            if URL.search(text) and LICENSE_WORDS.search(text) and not is_full and not is_named:
                linked_only_evidence.append(name)
                linked_urls.update(URL.findall(text))

        external_license_evidence = []
        for source in (pin_config or {}).get("officialSources", []):
            covered_urls = set(source.get("coversUrls", []))
            applies_here = source.get("appliesTo") == root or bool(linked_urls & covered_urls)
            if not applies_here or not source.get("requiredInArchive"):
                continue
            presence = official_by_name.get(source.get("name"), {})
            if presence.get("present") and source.get("path", "").casefold().startswith("license") and (
                source.get("appliesTo") == root or linked_urls & covered_urls
            ):
                external_license_evidence.extend(presence.get("archivePaths", []))
            elif not presence.get("present"):
                required_official_sources.append(source.get("name", source.get("path", "unknown")))
        full_license_paths.extend(external_license_evidence)

        if required_official_sources:
            status = "unresolved-official-source"
        elif full_license_paths:
            status = "embedded-license-text"
        elif linked_only_evidence:
            status = "unresolved-linked-only"
        elif license_files or readme_evidence or spdx_paths or license_notice_paths:
            status = "unresolved-license-text-missing"
        else:
            status = "missing-license-evidence"
        repos.append({
            "path": root,
            "commit": repo["commit"],
            "licensePaths": [item["path"] for item in license_files],
            "licenseFiles": license_files,
            "embeddedLicenseTextPaths": full_license_paths,
            "readmeEvidence": readme_evidence,
            "embeddedEvidence": {
                "spdxPaths": spdx_paths,
                "licenseNoticePaths": license_notice_paths,
                "copyrightNoticePaths": copyright_notice_paths,
            },
            "linkedOnlyEvidence": linked_only_evidence,
            "externalLicenseEvidence": external_license_evidence,
            "missingOfficialSources": required_official_sources,
            "status": status,
        })

    result = {
        "schemaVersion": 1,
        "archiveSha256": sha256(archive_bytes),
        "sourceManifestSha256": sha256(manifest_bytes),
        "parentCommit": source_manifest.get("parentCommit"),
        "repositories": repos,
        "summary": {
            "repositoryCount": len(repos),
            "archiveFileCount": len(file_manifest),
            "licensePathCount": sum(len(repo["licensePaths"]) for repo in repos),
            "linkedOnlyRepositoryCount": sum(bool(repo["linkedOnlyEvidence"]) for repo in repos),
            "unresolvedRepositoryCount": sum(repo["status"] != "embedded-license-text" for repo in repos),
        },
    }
    if pin_config is not None:
        result["officialSources"] = official_sources
    return result


def _source_presence(contents: dict[str, bytes], pin_config: dict) -> list[dict]:
    sources = pin_config.get("officialSources", [])
    result = []
    digest_paths: dict[str, list[str]] = {}
    for path, data in contents.items():
        digest_paths.setdefault(sha256(data), []).append(path)
    try:
        source_manifest = json.loads(contents["source-manifest.json"].decode("utf-8-sig"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LicenseInventoryError("Source manifest is missing or invalid.") from error
    declared = source_manifest.get("licenseSupplements", [])
    if not isinstance(declared, list):
        raise LicenseInventoryError("Source manifest license supplement pins are invalid.")
    expected_supplements = supplement_manifest(pin_config)
    if declared != expected_supplements:
        raise LicenseInventoryError("Source manifest does not bind the exact pinned license supplements.")
    for source in sources:
        digest = source.get("sha256", "")
        supplement_path = source.get("supplementPath")
        pin = {
            "name": source.get("name"), "path": supplement_path, "sha256": digest,
            "pinnedUrl": source.get("pinnedUrl"), "commit": source.get("commit"),
        }
        archive_paths = []
        if pin in declared and supplement_path in contents and sha256(contents[supplement_path]) == digest:
            archive_paths.append(supplement_path)
        result.append({
            "name": source.get("name"),
            "url": source.get("pinnedUrl"),
            "sha256": digest,
            "archivePaths": archive_paths,
            "present": bool(archive_paths),
        })
    return result


def supplement_entries(pin_config: dict) -> dict[str, bytes]:
    """Return explicit, checksum-verified official license files for source export."""
    entries = {}
    for source in pin_config.get("officialSources", []):
        if not source.get("requiredInArchive"):
            continue
        path = source.get("supplementPath")
        if not isinstance(path, str) or not _safe_member(path):
            raise LicenseInventoryError("Official license supplement path is unsafe or missing.")
        content = source.get("content")
        if not isinstance(content, str):
            raise LicenseInventoryError("Pinned official license body is missing.")
        data = content.encode("utf-8")
        if sha256(data) != source.get("sha256"):
            raise LicenseInventoryError("Pinned official license body checksum differs.")
        if path in entries:
            raise LicenseInventoryError("Official license supplement paths must be unique.")
        entries[path] = data
    return entries


def supplement_manifest(pin_config: dict) -> list[dict]:
    """Return source-manifest records matching ``supplement_entries`` exactly."""
    supplement_entries(pin_config)  # Validate every body/path before emitting metadata.
    return [
        {
            "name": source["name"],
            "path": source["supplementPath"],
            "sha256": source["sha256"],
            "pinnedUrl": source["pinnedUrl"],
            "commit": source["commit"],
        }
        for source in pin_config.get("officialSources", [])
        if source.get("requiredInArchive")
    ]


def verify_license_evidence(archive_path: str | Path, pin_config: dict) -> dict:
    """Release-gate interface: verify archive manifest hashes and fail closed on evidence."""
    report = inventory_archive(archive_path, pin_config)
    unresolved = [repo["path"] for repo in report["repositories"] if repo["status"] != "embedded-license-text"]
    report["unresolvedRepositories"] = unresolved
    report["pinStatus"] = "source-manifest-verified"
    if unresolved:
        preview = ", ".join(unresolved[:8])
        if len(unresolved) > 8:
            preview += f", and {len(unresolved) - 8} more"
        raise LicenseInventoryError(f"Unresolved license evidence blocks export: {preview}")
    return report


def pre_export_check(archive_path: str | Path, pin_config: dict, *, fail_on_unresolved: bool = True) -> dict:
    """Verify the source manifest and fail closed on missing license text."""
    pinned = pin_config.get("archive")
    if pinned is not None and not isinstance(pinned, dict):
        raise LicenseInventoryError("Investigation snapshot pin is invalid.")
    report = inventory_archive(archive_path, pin_config)
    if pinned is not None:
        if report["archiveSha256"] != pinned.get("sha256"):
            raise LicenseInventoryError("Archive SHA-256 does not match the fixed pin.")
        if report["sourceManifestSha256"] != pinned.get("sourceManifestSha256"):
            raise LicenseInventoryError("source-manifest SHA-256 does not match the fixed pin.")
        if report["parentCommit"] != pinned.get("parentCommit"):
            raise LicenseInventoryError("Parent commit does not match the fixed pin.")
        expected = pinned.get("repositories")
        actual = [{"path": repo["path"], "commit": repo["commit"]} for repo in report["repositories"]]
        if not isinstance(expected, list) or actual != expected:
            raise LicenseInventoryError("Repository commit pins do not match the fixed manifest.")
    unresolved = [repo["path"] for repo in report["repositories"] if repo["status"] != "embedded-license-text"]
    report["pinStatus"] = "investigation-snapshot-verified" if pinned is not None else "source-manifest-verified"
    report["unresolvedRepositories"] = unresolved
    if unresolved and fail_on_unresolved:
        preview = ", ".join(unresolved[:8])
        if len(unresolved) > 8:
            preview += f", and {len(unresolved) - 8} more"
        raise LicenseInventoryError(f"Unresolved license evidence blocks export: {preview}")
    return report


def _load_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise LicenseInventoryError("License source pin configuration could not be read.") from error
    return validate_config(value)


def validate_config(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get('officialSources'), list):
        raise LicenseInventoryError("Unsupported license source pin schema.")
    for source in value.get("officialSources", []):
        if not re.fullmatch(r"[0-9a-f]{40}", source.get("commit", "")):
            raise LicenseInventoryError("Official license source commit is not a fixed SHA.")
        if not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
            raise LicenseInventoryError("Official license source SHA-256 pin is invalid.")
        if source.get("pinnedUrl", "").find(source["commit"]) < 0:
            raise LicenseInventoryError("Official license source URL is not pinned to its commit.")
        parsed = urlparse(source.get("pinnedUrl", ""))
        repository = source.get("repository", "")
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                or parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com"
                or not parsed.path.startswith(f"/{repository}/{source['commit']}/")):
            raise LicenseInventoryError("Official license source must use its pinned upstream raw URL.")
        supplement_path = source.get("supplementPath")
        if source.get("requiredInArchive"):
            content = source.get("content")
            if not isinstance(content, str) or sha256(content.encode("utf-8")) != source["sha256"]:
                raise LicenseInventoryError("Pinned official license body is absent or its checksum differs.")
            if not isinstance(supplement_path, str) or not _safe_member(supplement_path):
                raise LicenseInventoryError("Official license supplement path is unsafe or missing.")
        covers_urls = source.get("coversUrls", [])
        if not isinstance(covers_urls, list) or any(not isinstance(url, str) or not url.startswith("https://") for url in covers_urls):
            raise LicenseInventoryError("Official license URL coverage is invalid.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="corresponding-source ZIP")
    parser.add_argument("--config", type=Path, default=Path(__file__).parents[1] / "config" / "license-sources.json")
    parser.add_argument("--pre-export", action="store_true", help="verify source pins and fail closed on unresolved license text")
    parser.add_argument("--output", type=Path, help="write the detailed inventory JSON to this path")
    args = parser.parse_args(argv)
    try:
        config = _load_config(args.config)
        report = pre_export_check(args.archive, config) if args.pre_export else inventory_archive(args.archive, config)
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
        if not args.pre_export and report["summary"]["unresolvedRepositoryCount"]:
            return 2
        return 0
    except LicenseInventoryError as error:
        print(f"license inventory blocked: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
