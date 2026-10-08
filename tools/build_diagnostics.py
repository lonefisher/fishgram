"""Extract compiler locations/codes without publishing commands or credentials."""
import json
from pathlib import Path
import re

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MSVC = re.compile(r"(?:^|[\\/])(?P<file>[A-Za-z0-9_.-]+\.(?:cpp|cc|c|h|hpp))\((?P<line>\d+)(?:,\d+)?\)\s*:\s*(?:fatal\s+)?error\s+(?P<code>C\d{4})\b")
_LINK = re.compile(r"\b(?:fatal\s+)?error\s+(?P<code>LNK\d{4})\b")
_CMAKE = re.compile(r"CMake Error at (?:[^\r\n]*[\\/])?(?P<file>(?:[A-Za-z0-9_.-]+\.cmake|CMakeLists\.txt)):(?P<line>\d+)")
_MISSING = re.compile(r"Could NOT find (?P<package>[A-Za-z0-9_]+)\s*\(missing:\s*(?P<variables>[^)]{0,2048})\)")
# Values are a fixed public vocabulary, never copied from arbitrary log text.
_DEPENDENCIES = {
    "OpenSSL": {"OPENSSL_CRYPTO_LIBRARY", "OPENSSL_SSL_LIBRARY", "OPENSSL_INCLUDE_DIR"},
    "Qt5": {"Qt5_DIR"}, "Qt5Core": {"Qt5Core_DIR"},
    "Python3": {"Python3_EXECUTABLE", "Python3_INCLUDE_DIRS", "Python3_LIBRARIES", "Interpreter", "Development"},
    "ZLIB": {"ZLIB_LIBRARY", "ZLIB_INCLUDE_DIR"},
    "Threads": {"Threads_FOUND"}, "PkgConfig": {"PKG_CONFIG_EXECUTABLE"},
}


def extract(data: str) -> dict:
    data = _ANSI.sub("", data)
    missing = []
    for match in _MISSING.finditer(data):
        package = match["package"]
        if package in _DEPENDENCIES:
            variables = [token for token in match["variables"].split() if token in _DEPENDENCIES[package]]
            item = {"package": package, "variables": variables}
            if item not in missing:
                missing.append(item)
    errors = []
    cmake = []
    seen = set()
    for line in data.splitlines():
        line = _ANSI.sub("", line)
        compiler = _MSVC.search(line)
        linker = _LINK.search(line)
        configure = _CMAKE.search(line)
        if configure and len(cmake) < 16:
            cmake.append({"file": configure["file"], "line": int(configure["line"])})
        item = ({"file": compiler["file"], "line": int(compiler["line"]), "code": compiler["code"]}
                if compiler else {"code": linker["code"]} if linker else None)
        if item:
            identity = tuple(item.items())
            if identity not in seen:
                errors.append(item)
                seen.add(identity)
            if len(errors) == 32:
                break
    return {"schema": 1, "compilerErrors": errors, "cmakeErrors": cmake, "missingDependencies": missing,
            "ninjaStopped": "ninja: build stopped" in data,
            "cmakeFailed": "CMake Error" in data,
            "hasDiagnostic": bool(errors or cmake or missing)}


if __name__ == "__main__":
    import sys
    result = extract(Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace"))
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
