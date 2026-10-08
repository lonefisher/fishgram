import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).parents[1] / "tools" / "license_inventory.py"
SPEC = importlib.util.spec_from_file_location("license_inventory", SCRIPT)
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class LicenseInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix=".license-inventory-test-", dir=Path(__file__).parents[1]
        )
        self.base = Path(self.temp.name)
        self.archive_path = self.base / "source.zip"
        self.manifest_bytes = json.dumps(
            {
                "schema": 1,
                "product": "FishGram",
                "parentCommit": "a" * 40,
                "repositories": [
                    {"path": ".", "commit": "a" * 40},
                    {"path": "vendor/linked", "commit": "b" * 40},
                    {"path": "vendor/fully-licensed", "commit": "c" * 40},
                ],
                "files": {},
            },
            sort_keys=True,
        ).encode() + b"\n"
        self.files = {
            "LICENSE": b"GNU GENERAL PUBLIC LICENSE\nVersion 3\nTerms and Conditions\n" + b"x" * 12000,
            "vendor/linked/src/item.cpp": (
                b"// Copyright 2024 Example\n"
                b"// License: https://licenses.example.invalid/item\n"
                b"int item();\n"
            ),
            "vendor/linked/README.md": b"This component is licensed under Example License. See https://licenses.example.invalid/item\n",
            "vendor/linked/NOTICE": b"Copyright 2024 Example Authors.\n",
            "vendor/linked/LEGAL": (
                b"GNU GENERAL PUBLIC LICENSE. Redistribution under this license is permitted. "
                b"Full license: https://licenses.example.invalid/full-text\n" + b"Notice only. " * 80
            ),
            "vendor/fully-licensed/licenses/LICENSE-MIT.txt": (
                b"MIT License\nCopyright 2024 Example\nPermission is hereby granted, free of charge, "
                b"to any person obtaining a copy of this software and associated documentation files, "
                b"to deal in the Software without restriction. The above copyright notice and this "
                b"permission notice shall be included in all copies or substantial portions of the Software. "
                b"THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND.\n"
                + b"Synthetic license fixture terms. " * 30
            ),
        }
        files_manifest = {
            name: {"size": len(content), "sha256": sha256(content)}
            for name, content in self.files.items()
        }
        source_manifest = json.loads(self.manifest_bytes)
        source_manifest["files"] = files_manifest
        self.manifest_bytes = json.dumps(source_manifest, sort_keys=True).encode() + b"\n"
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in self.files.items():
                archive.writestr(name, content)
            archive.writestr("source-manifest.json", self.manifest_bytes)
        self.pin_config = {
            "schemaVersion": 1,
            "officialSources": [],
            "archive": {
                "sha256": sha256(self.archive_path.read_bytes()),
                "sourceManifestSha256": sha256(self.manifest_bytes),
                "parentCommit": "a" * 40,
                "repositories": [
                    {"path": ".", "commit": "a" * 40},
                    {"path": "vendor/linked", "commit": "b" * 40},
                    {"path": "vendor/fully-licensed", "commit": "c" * 40},
                ],
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_finds_nested_license_readme_notice_and_link_only_evidence(self):
        result = inventory.inventory_archive(self.archive_path)
        linked = next(repo for repo in result["repositories"] if repo["path"] == "vendor/linked")

        self.assertEqual(linked["licensePaths"], ["vendor/linked/NOTICE", "vendor/linked/LEGAL"])
        self.assertNotIn("vendor/linked/LEGAL", linked["embeddedLicenseTextPaths"])
        self.assertIn("vendor/linked/README.md", linked["readmeEvidence"])
        self.assertIn("vendor/linked/src/item.cpp", linked["linkedOnlyEvidence"])
        self.assertEqual(linked["status"], "unresolved-linked-only")
        fully_licensed = next(repo for repo in result["repositories"] if repo["path"] == "vendor/fully-licensed")
        self.assertEqual(fully_licensed["licensePaths"], ["vendor/fully-licensed/licenses/LICENSE-MIT.txt"])
        self.assertEqual(fully_licensed["status"], "embedded-license-text")
        self.assertEqual(result["summary"]["repositoryCount"], 3)

    def test_full_mit_license_in_source_header_is_archived_evidence(self):
        files = dict(self.files)
        files['vendor/linked/src/item.cpp'] = files['vendor/fully-licensed/licenses/LICENSE-MIT.txt'] + b'\nint item();\n'
        value = json.loads(self.manifest_bytes)
        value['files'] = {name: {'size': len(data), 'sha256': sha256(data)} for name, data in files.items()}
        with zipfile.ZipFile(self.archive_path, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('source-manifest.json', json.dumps(value))
        report = inventory.inventory_archive(self.archive_path)
        repo = next(repo for repo in report['repositories'] if repo['path'] == 'vendor/linked')
        self.assertEqual(repo['status'], 'embedded-license-text')
        self.assertIn('vendor/linked/src/item.cpp', repo['embeddedLicenseTextPaths'])

    def test_pre_export_check_accepts_only_exact_archive_and_source_pins(self):
        result = inventory.pre_export_check(self.archive_path, self.pin_config, fail_on_unresolved=False)

        self.assertEqual(result["pinStatus"], "investigation-snapshot-verified")
        self.assertEqual(result["unresolvedRepositories"], ["vendor/linked"])

    def test_pre_export_check_fails_closed_for_link_only_repository(self):
        with self.assertRaisesRegex(inventory.LicenseInventoryError, "(?i)unresolved license evidence"):
            inventory.pre_export_check(self.archive_path, self.pin_config)

    def test_pre_export_check_fails_closed_when_archive_hash_differs(self):
        with self.archive_path.open("ab") as archive:
            archive.write(b"changed")

        with self.assertRaisesRegex(inventory.LicenseInventoryError, "(?i)archive SHA-256"):
            inventory.pre_export_check(self.archive_path, self.pin_config)

    def test_pre_export_check_rejects_source_file_hash_mismatch(self):
        source_manifest = json.loads(self.manifest_bytes)
        source_manifest["files"]["LICENSE"]["sha256"] = "0" * 64
        bad_manifest = json.dumps(source_manifest, sort_keys=True).encode() + b"\n"
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in self.files.items():
                archive.writestr(name, content)
            archive.writestr("source-manifest.json", bad_manifest)
        self.pin_config["archive"]["sha256"] = sha256(self.archive_path.read_bytes())
        self.pin_config["archive"]["sourceManifestSha256"] = sha256(bad_manifest)

        with self.assertRaisesRegex(inventory.LicenseInventoryError, "file hash"):
            inventory.pre_export_check(self.archive_path, self.pin_config)

    def test_pre_export_check_rejects_repository_pin_mismatch(self):
        self.pin_config["archive"]["repositories"][1]["commit"] = "f" * 40

        with self.assertRaisesRegex(inventory.LicenseInventoryError, "Repository commit pins"):
            inventory.pre_export_check(self.archive_path, self.pin_config, fail_on_unresolved=False)

    def test_config_rejects_mutated_official_license_body(self):
        body = "A pinned public license body.\n"
        config = {
            "schemaVersion": 1,
            "officialSources": [{
                "name": "Pinned license",
                "repository": "desktop-app/legal",
                "commit": "e" * 40,
                "path": "LICENSE",
                "pinnedUrl": "https://raw.githubusercontent.com/desktop-app/legal/" + "e" * 40 + "/LICENSE",
                "sha256": sha256(body.encode("utf-8")),
                "requiredInArchive": True,
                "content": body + "tampered",
            }],
        }
        config_path = self.base / "license-sources.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        with self.assertRaisesRegex(inventory.LicenseInventoryError, "body is absent or its checksum differs"):
            inventory._load_config(config_path)

    def test_official_supplement_entries_are_portable_and_path_pinned(self):
        config = inventory._load_config(SCRIPT.parents[1] / "config" / "license-sources.json")
        sources = config["officialSources"]
        entries = inventory.supplement_entries(config)

        self.assertEqual(set(entries), {source["supplementPath"] for source in sources})
        self.assertEqual(
            entries[sources[0]["supplementPath"]],
            sources[0]["content"].encode("utf-8"),
        )

    def test_inventory_requires_manifest_bound_license_supplement(self):
        source = {
            "name": "Pinned license", "repository": "owner/repository", "commit": "d" * 40,
            "path": "LICENSE", "pinnedUrl": "https://raw.githubusercontent.com/owner/repository/" + "d" * 40 + "/LICENSE",
            "sha256": sha256(b"GNU GENERAL PUBLIC LICENSE\n" + b"Terms and conditions. " * 600),
            "supplementPath": "LICENSES/official/owner-repository-LICENSE.txt",
            "appliesTo": "vendor/linked", "requiredInArchive": True,
            "content": "GNU GENERAL PUBLIC LICENSE\n" + "Terms and conditions. " * 600,
        }
        config = {"schemaVersion": 1, "officialSources": [source]}
        files = dict(self.files)
        files[source["supplementPath"]] = source["content"].encode("utf-8")
        files["config/license-sources.json"] = json.dumps(config, sort_keys=True).encode()
        supplements = inventory.supplement_manifest(config)
        manifest = {
            "schema": 1, "product": "FishGram", "parentCommit": "a" * 40,
            "repositories": [
                {"path": ".", "commit": "a" * 40},
                {"path": "vendor/linked", "commit": "b" * 40},
                {"path": "vendor/fully-licensed", "commit": "c" * 40},
            ],
            "licenseSupplements": supplements,
            "files": {name: {"size": len(data), "sha256": sha256(data)} for name, data in files.items()},
        }
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("source-manifest.json", json.dumps(manifest, sort_keys=True).encode())

        result = inventory.inventory_archive(self.archive_path, config)
        linked = next(repo for repo in result["repositories"] if repo["path"] == "vendor/linked")

        self.assertEqual(linked["status"], "embedded-license-text")
        self.assertIn(source["supplementPath"], linked["externalLicenseEvidence"])

    def test_inventory_rejects_supplement_missing_from_manifest_binding(self):
        source = {
            "name": "Pinned license", "repository": "owner/repository", "commit": "d" * 40,
            "path": "LICENSE", "pinnedUrl": "https://raw.githubusercontent.com/owner/repository/" + "d" * 40 + "/LICENSE",
            "sha256": sha256(b"GNU GENERAL PUBLIC LICENSE\n" + b"Terms and conditions. " * 600),
            "supplementPath": "LICENSES/official/owner-repository-LICENSE.txt",
            "appliesTo": "vendor/linked", "requiredInArchive": True,
            "content": "GNU GENERAL PUBLIC LICENSE\n" + "Terms and conditions. " * 600,
        }
        config = {"schemaVersion": 1, "officialSources": [source]}
        files = dict(self.files)
        files[source["supplementPath"]] = source["content"].encode("utf-8")
        manifest = {
            "schema": 1, "product": "FishGram", "parentCommit": "a" * 40,
            "repositories": [
                {"path": ".", "commit": "a" * 40},
                {"path": "vendor/linked", "commit": "b" * 40},
                {"path": "vendor/fully-licensed", "commit": "c" * 40},
            ],
            "licenseSupplements": [],
            "files": {name: {"size": len(data), "sha256": sha256(data)} for name, data in files.items()},
        }
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("source-manifest.json", json.dumps(manifest, sort_keys=True).encode())

        with self.assertRaisesRegex(inventory.LicenseInventoryError, "exact pinned license supplements"):
            inventory.inventory_archive(self.archive_path, config)

    def test_config_has_no_fixed_investigation_archive_pin(self):
        config = inventory._load_config(SCRIPT.parents[1] / "config" / "license-sources.json")

        self.assertNotIn("archive", config)
        self.assertTrue(all(source.get("supplementPath") for source in config["officialSources"]))

    def test_pre_export_check_does_not_pin_an_investigation_zip(self):
        report = inventory.pre_export_check(
            self.archive_path, {"schemaVersion": 1, "officialSources": []}, fail_on_unresolved=False
        )

        self.assertEqual(report["pinStatus"], "source-manifest-verified")

    def test_pinned_official_license_body_resolves_a_link_only_repository_when_archived(self):
        body = "GNU GENERAL PUBLIC LICENSE\nVersion 3\nTerms and Conditions\n" + "Pinned official text. " * 50
        body_hash = sha256(body.encode("utf-8"))
        official = {
            "name": "Example upstream full license",
            "commit": "d" * 40,
            "path": "LICENSE",
            "pinnedUrl": "https://raw.githubusercontent.com/owner/repository/" + "d" * 40 + "/LICENSE",
            "sha256": body_hash,
            "appliesTo": "vendor/linked",
            "supplementPath": "LICENSES/official/example-LICENSE.txt",
            "requiredInArchive": True,
            "content": body,
        }
        manifest_config = {"schemaVersion": 1, "officialSources": [official]}
        files = dict(self.files)
        files[official["supplementPath"]] = body.encode("utf-8")
        files["config/license-sources.json"] = json.dumps(
            manifest_config, sort_keys=True
        ).encode() + b"\n"
        source_manifest = {
            "schema": 1,
            "product": "FishGram",
            "parentCommit": "a" * 40,
            "repositories": [
                {"path": ".", "commit": "a" * 40},
                {"path": "vendor/linked", "commit": "b" * 40},
                {"path": "vendor/fully-licensed", "commit": "c" * 40},
            ],
            "licenseSupplements": inventory.supplement_manifest(manifest_config),
            "files": {
                name: {"size": len(data), "sha256": sha256(data)}
                for name, data in files.items()
            },
        }
        source_manifest_bytes = json.dumps(source_manifest, sort_keys=True).encode() + b"\n"
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("source-manifest.json", source_manifest_bytes)

        result = inventory.inventory_archive(self.archive_path, manifest_config)
        linked = next(repo for repo in result["repositories"] if repo["path"] == "vendor/linked")

        self.assertEqual(linked["status"], "embedded-license-text")
        self.assertTrue(linked["externalLicenseEvidence"])
        self.assertTrue(result["officialSources"][0]["present"])


if __name__ == "__main__":
    unittest.main()
