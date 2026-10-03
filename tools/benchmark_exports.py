"""Offline 100k export benchmark with bounded input and Python allocation measurement."""

import argparse
import json
import sys
import tempfile
import time
import tracemalloc
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import AvitoItem
from streaming_export import StreamingExport


def peak_process_mib():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak",
                    "working",
                    "page_peak",
                    "page",
                    "nonpaged_peak",
                    "nonpaged",
                    "pagefile",
                    "pagefile_peak",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        if not query(get_process(), ctypes.byref(counters), counters.cb):
            return None
        return counters.peak / 1024**2
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024**2 if sys.platform == "darwin" else 1024)


def benchmark(rows, fmt, allocations=False):
    with tempfile.TemporaryDirectory() as directory:
        batch = [
            AvitoItem(
                id=str(index),
                title="Тестовое объявление",
                price=0,
                url="https://www.avito.ru/fixture",
            )
            for index in range(1000)
        ]
        if allocations:
            tracemalloc.start()
        started = time.perf_counter()
        path = Path(directory) / f"benchmark.{fmt}"
        writer = StreamingExport(path, fmt)
        for offset in range(0, rows, 1000):
            writer.write(batch[: min(1000, rows - offset)])
        writer.finish()
        peak = None
        if allocations:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        return {
            "format": fmt,
            "rows": rows,
            "seconds": round(time.perf_counter() - started, 2),
            "python_peak_mib": round(peak / 1024**2, 2) if peak else None,
            "peak_process_mib": round(peak_process_mib(), 2)
            if peak_process_mib() is not None
            else None,
            "file_mib": round(path.stat().st_size / 1024**2, 2),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--format", choices=["csv", "xlsx"], required=True)
    parser.add_argument("--allocations", action="store_true")
    args = parser.parse_args()
    print(json.dumps(benchmark(args.rows, args.format, args.allocations)))
