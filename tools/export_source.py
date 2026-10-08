"""Export only committed build inputs, including every pinned submodule.

Reads Git objects rather than the working filesystem, so ignored runtime data
and untracked files cannot silently enter the corresponding-source archive.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile
from contextlib import ExitStack


class SourceExportError(ValueError):
    pass


def license_component():
    spec = importlib.util.spec_from_file_location('fishgram_export_licenses', Path(__file__).with_name('license_inventory.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-c', 'core.longpaths=true', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise SourceExportError('A required committed source object is unavailable.')
    return result.stdout


def safe_name(name: str, repository_index: int = 0) -> None:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name or '\\' in name
            or ':' in name or any(c in name for c in '\x00\n\r')
            or any(part in ('..', '.') or part.endswith((' ', '.')) for part in path.parts)):
        raise SourceExportError('Unsafe source archive path.')
    always_private = {'.private', '.git', '.codex', '.comet', 'tdata'}
    parent_private = {'.agents', 'runtime', 'logs', 'dist', 'packages', 'reports'}
    first_part = path.parts[0].casefold() if path.parts else ''
    for part in path.parts:
        lower = part.casefold()
        if (lower in always_private or (repository_index == 0 and first_part in parent_private)
                or lower == '.env' or lower.startswith('.env.')
                or lower.endswith(('.clixml', '.pfx', '.p12'))
                or lower.startswith(('root-private', 'issuer-private'))):
            if path.as_posix() != '.comet/config.yaml':
                raise SourceExportError('Private material cannot be corresponding source.')


def no_links(path: Path) -> None:
    for part in [path, *path.parents]:
        if os.path.lexists(part):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise SourceExportError('Source checkout and output parents cannot be links.')


def validate_symlink(name: str, content: bytes, repository_index: int) -> None:
    try:
        target = content.decode('utf-8')
    except UnicodeDecodeError as error:
        raise SourceExportError('Invalid source symlink target.') from error
    path = PurePosixPath(target)
    if not target or path.is_absolute() or '\\' in target or ':' in target or any(c in target for c in '\x00\n\r'):
        raise SourceExportError('Source symlinks must use safe package-relative targets.')
    resolved = list(PurePosixPath(name).parent.parts)
    for part in path.parts:
        if part in ('', '.'):
            continue
        if part == '..':
            if not resolved:
                raise SourceExportError('Source symlink escapes the package.')
            resolved.pop()
        else:
            resolved.append(part)
    if resolved:
        safe_name('/'.join(resolved), repository_index=repository_index)


def walk_commits(root: Path, commit: str, prefix: str, entries: dict, modules: list, depth=0) -> None:
    if depth > 24 or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise SourceExportError('Invalid recursive source identity.')
    no_links(root)
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    if head != commit:
        raise SourceExportError('A source checkout differs from its pinned commit.')
    modules.append({'path': prefix.rstrip('/') or '.', 'commit': commit})
    tree = git(root, 'ls-tree', '-rz', '--full-tree', commit)
    for record in tree.split(b'\0'):
        if not record:
            continue
        metadata, encoded = record.split(b'\t', 1)
        mode, kind, digest = metadata.decode('ascii').split()
        name = encoded.decode('utf-8')
        repository_index = len(modules) - 1
        safe_name(name, repository_index)
        destination = prefix + name
        safe_name(destination, repository_index)
        if destination in entries:
            raise SourceExportError('Duplicate corresponding-source path.')
        if mode == '160000' and kind == 'commit':
            walk_commits(root / name, digest, destination + '/', entries, modules, depth + 1)
        elif kind == 'blob' and mode in ('100644', '100755'):
            entries[destination] = (root, digest, mode, repository_index)
        elif mode == '120000' and kind == 'blob':
            entries[destination] = (root, digest, mode, repository_index)
        else:
            raise SourceExportError('Unsupported link or source tree entry.')


class BlobReader:
	def __init__(self, root):
		self.process = subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'],
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
	def __enter__(self):
		return self
	def __exit__(self, *args):
		self.process.stdin.close()
		self.process.stdout.close()
		if self.process.poll() is None:
			self.process.terminate()
		self.process.wait()
	def read(self, digest):
		self.process.stdin.write((digest + '\n').encode('ascii'))
		self.process.stdin.flush()
		header = self.process.stdout.readline().decode('ascii').strip().split()
		if len(header) != 3 or header[0] != digest or header[1] != 'blob' or not header[2].isdigit():
			raise SourceExportError('A source blob could not be read.')
		size = int(header[2])
		if size > 2 * 1024**3:
			raise SourceExportError('A source blob exceeds the export limit.')
		content = self.process.stdout.read(size)
		if len(content) != size or self.process.stdout.read(1) != b'\n':
			raise SourceExportError('A source blob was truncated.')
		return content


def export(root: Path, output: Path, parent_commit: str) -> dict:
    root, output = Path(root).absolute(), Path(output).absolute()
    no_links(root)
    no_links(output)
    if not output.parent.is_dir() or output.exists() or output.is_relative_to(root / 'tdesktop'):
        raise SourceExportError('Use a new archive file under an existing staging directory.')
    entries, modules = {}, []
    walk_commits(root, parent_commit, '', entries, modules)
    recipe_entry = entries.get('fishgram.json')
    if not recipe_entry or not any(item['path'] == 'tdesktop' for item in modules):
        raise SourceExportError('Product recipe or pinned source submodule is missing.')
    recipe = json.loads(git(recipe_entry[0], 'cat-file', 'blob', recipe_entry[1]).decode('utf-8-sig'))
    if recipe.get('product') != 'FishGram':
        raise SourceExportError('Wrong product source.')
    for required in ('AGENTS.md', 'tools/build-telegram.ps1', 'tdesktop/LICENSE', 'tdesktop/LEGAL'):
        if required not in entries:
            raise SourceExportError('Corresponding source is missing required build or license files.')
    if 'source-manifest.json' in entries:
        raise SourceExportError('Committed source conflicts with the reserved source manifest path.')
    config_entry = entries.get('config/license-sources.json')
    if not config_entry or config_entry[2] == '120000':
        raise SourceExportError('Committed license source configuration is missing or unsafe.')
    licenses = license_component()
    try:
        config = licenses.validate_config(json.loads(git(config_entry[0], 'cat-file', 'blob', config_entry[1]).decode('utf-8-sig')))
        supplements = licenses.supplement_entries(config)
        supplement_pins = licenses.supplement_manifest(config)
    except (ValueError, TypeError, KeyError) as error:
        raise SourceExportError('Committed license source configuration is invalid.') from error
    for name in supplements:
        safe_name(name)
        if not name.startswith('LICENSES/official/') or name in entries:
            raise SourceExportError('License supplement conflicts with committed source or its reserved directory.')
    manifest = {'schema': 1, 'product': 'FishGram', 'parentCommit': parent_commit,
                'version': f"{recipe['upstreamVersion']}-r{recipe['revision']}", 'repositories': modules,
                'files': {}, 'licenseSupplements': supplement_pins}
    created_output = False
    try:
        with ExitStack() as stack:
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            created_output = True
            archive_file = stack.enter_context(os.fdopen(descriptor, 'w+b'))
            archive = stack.enter_context(zipfile.ZipFile(archive_file, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6))
            readers = {}
            for name, (repository, digest, mode, repository_index) in sorted(entries.items()):
                if repository not in readers:
                    readers[repository] = stack.enter_context(BlobReader(repository))
                content = readers[repository].read(digest)
                if mode == '120000':
                    validate_symlink(name, content, repository_index)
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o120777 if mode == '120000' else 0o100755 if mode == '100755' else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
                manifest['files'][name] = {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
            for name, content in sorted(supplements.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
                manifest['files'][name] = {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
            archive.writestr('source-manifest.json', json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n')
    except Exception:
        if created_output:
            output.unlink(missing_ok=True)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--parent-commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = export(args.root, args.output, args.parent_commit)
    print(f"Exported {len(manifest['repositories'])} pinned repositories and {len(manifest['files'])} committed files.")


if __name__ == '__main__':
    main()
