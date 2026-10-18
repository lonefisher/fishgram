import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ProductPackerTargetTests(unittest.TestCase):
    def test_explicit_product_packer_without_official_special_target(self):
        root = Path(__file__).parents[1]
        cmake = shutil.which('cmake')
        if not cmake:
            finder = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
            install = subprocess.check_output([str(finder), '-latest', '-products', '*',
                                               '-property', 'installationPath'], text=True).strip()
            cmake = str(Path(install) / 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe')
        source = (root / 'tdesktop/Telegram/CMakeLists.txt').read_text()
        start = source.index('    if (DESKTOP_APP_SPECIAL_TARGET', source.index('add_executable(Updater WIN32)'))
        block = source[start:source.index('\nelseif (build_winstore)', start)]
        prelude = '''cmake_minimum_required(VERSION 3.25)
project(ProductPacker LANGUAGES CXX)
set(output_folder "${CMAKE_CURRENT_BINARY_DIR}/bin")
add_executable(Telegram main.cpp)
function(init_target)
endfunction()
function(nice_target_sources target)
  target_sources(${target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/main.cpp")
endfunction()
function(target_link_libraries)
endfunction()
'''
        with tempfile.TemporaryDirectory(prefix='fishgram-packer-target-') as folder:
            project = Path(folder)
            (project / 'main.cpp').write_text('int main() { return 0; }')
            (project / 'CMakeLists.txt').write_text(prelude + block + '''
if(NOT TARGET Packer)
  message(FATAL_ERROR "Product signing tool target is missing")
endif()
''')
            result = subprocess.run([cmake, '-S', str(project), '-B', str(project / 'build'),
                                     '-G', 'Visual Studio 17 2022', '-A', 'x64',
                                     '-DFISHGRAM_BUILD_PACKER=ON'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
