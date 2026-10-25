"""Exact Windows dependency cache identity, shared by both build workflows."""
import argparse
import hashlib
import json
from pathlib import Path


def preparation_files(root):
    files = {
        'tools/common.ps1',
        'tools/prepare-dependencies.ps1',
        'tools/prepare-compat.py',
        'tools/refresh-build-python.ps1',
        'tdesktop/Telegram/build/qt_version.py',
        # prepare.py imports this module even on Windows.
        'tdesktop/Telegram/build/build_mac.py',
        # Qt 6 preparation hashes and passes this file to Qt's CMake build.
        'tdesktop/cmake/external/angle/qt.cmake',
        'tdesktop/Telegram/build/prepare/prepare.py',
    }
    prepare = root / 'tdesktop/Telegram/build/prepare'
    files.update(path.relative_to(root).as_posix() for path in prepare.rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts
                 and path.suffix != '.pyc')
    return sorted(files)


def cache_key(root, runner_label):
    root = Path(root)
    recipe = json.loads((root / 'fishgram.json').read_text(encoding='utf-8-sig'))
    inputs = {
        'runner': runner_label,
        'recipe': {field: recipe[field]
                   for field in ('platform', 'msvc', 'windowsSdk', 'qt')},
        'preparation': {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in preparation_files(root)
        },
    }
    encoded = json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'deps-windows-v2-' + hashlib.sha256(encoded).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runner-label', required=True)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    key = cache_key(args.root, args.runner_label)
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8', newline='\n') as output:
            output.write(f'key={key}\n')
    print(key)


if __name__ == '__main__':
    main()
