"""Run the pinned upstream preparation with a package-name compatibility fix."""
from pathlib import Path
import sys
import os
import json
os.environ['GIT_CONFIG_COUNT'] = '1'
os.environ['GIT_CONFIG_KEY_0'] = 'http.sslBackend'
os.environ['GIT_CONFIG_VALUE_0'] = 'openssl'
upstream = Path(__file__).resolve().parents[1] / 'tdesktop/Telegram/build/prepare/prepare.py'
source = upstream.read_text(encoding='utf-8')
recipe = json.loads((Path(__file__).resolve().parents[1] / 'fishgram.json').read_text(encoding='utf-8-sig'))
if str(recipe['qt']).startswith('5.') and "'qt5'," in source and 'qt5' not in sys.argv:
    sys.argv.append('qt5')
old = 'mingw-w64-x86_64-diffutils'
ucrt = 'mingw-w64-ucrt-x86_64-diffutils'
if source.count(old) == 1 and ucrt not in source:
    source = source.replace(old, 'diffutils')
elif source.count(ucrt) != 1 or old in source:
    raise SystemExit('Unknown MSYS2 diffutils package layout; inspect upstream before updating.')
clone_command = 'git clone https://chromium.googlesource.com/breakpad/breakpad\n'
if source.count(clone_command) != 1:
    raise SystemExit('Expected one pinned breakpad clone command.')
source = source.replace(clone_command, clone_command.replace('git clone', 'git -c http.sslBackend=openssl clone'))
sys.argv[0] = str(upstream)
exec(compile(source, str(upstream), 'exec'), {'__file__': str(upstream), '__name__': '__main__'})
