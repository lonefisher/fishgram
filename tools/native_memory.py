"""Read numeric Windows memory counters without paths, process names or arguments."""
import ctypes
import json
from pathlib import Path
import struct
import sys


class PerformanceInformation(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_uint32)] + [
        (name, ctypes.c_size_t) for name in ['CommitTotal', 'CommitLimit', 'CommitPeak',
        'PhysicalTotal', 'PhysicalAvailable', 'SystemCache', 'KernelTotal', 'KernelPaged',
        'KernelNonpaged', 'PageSize']] + [
        (name, ctypes.c_uint32) for name in ['HandleCount', 'ProcessCount', 'ThreadCount']]


def collect():
    if sys.platform != 'win32':
        raise RuntimeError('Windows memory measurement requires Windows.')
    function = ctypes.WinDLL('psapi', use_last_error=True).GetPerformanceInfo
    function.argtypes = [ctypes.POINTER(PerformanceInformation), ctypes.c_uint32]
    function.restype = ctypes.c_int32
    info = PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    if not function(ctypes.byref(info), info.cb):
        raise OSError(ctypes.get_last_error(), 'Windows memory measurement failed.')
    page = info.PageSize
    if not page:
        raise RuntimeError('Windows memory measurement returned no page size.')
    return {'physicalTotalBytes': info.PhysicalTotal * page,
            'physicalAvailableBytes': info.PhysicalAvailable * page,
            'commitTotalBytes': info.CommitTotal * page,
            'commitLimitBytes': info.CommitLimit * page,
            'peakCommitBytes': info.CommitPeak * page, 'pageSizeBytes': page}


def host_architecture(path):
    # Read only the bounded PE header; the directory name does not prove the host.
    with Path(path).open('rb') as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            return 'unknown'
        offset = struct.unpack_from('<I', header, 60)[0]
        if offset < 64 or offset > 1024 * 1024:
            return 'unknown'
        source.seek(offset)
        pe = source.read(6)
    if len(pe) != 6 or pe[:4] != b'PE\0\0':
        return 'unknown'
    return {0x8664: 'x64', 0x14c: 'x86', 0xaa64: 'arm64'}.get(struct.unpack_from('<H', pe, 4)[0], 'unknown')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=['build-context', 'build-failure', 'build-complete'], required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--linker', type=Path)
    arguments = parser.parse_args()
    try:
        record = {'schema': 1, 'label': arguments.label, 'memory': collect(),
                  'linkerHostArchitecture': host_architecture(arguments.linker) if arguments.linker else None}
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record)
        arguments.output.write_text(encoded + '\n', encoding='utf-8')
    except (OSError, RuntimeError, ValueError):
        # Exception text and tracebacks may contain arbitrary private paths.
        print('FishGram memory diagnostics failed.', file=sys.stderr)
        raise SystemExit(1)
    print(encoded)
