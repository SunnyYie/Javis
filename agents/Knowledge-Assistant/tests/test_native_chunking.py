import subprocess
import sys


def test_real_native_parsers_keep_valid_symbol_boundaries_in_subprocess():
    # Real parser smoke test, isolated so an incompatible native wheel fails the test, not pytest.
    code = """
import gc
import json
from knowledge_assistant.chunking import symbol_ranges
fixtures = {
    "sample.go": "package sample\\n" + "\\n".join(
        f"func value{i}() int {{ return {i} }}" for i in range(400)),
    "sample.py": "\\n".join(f"def value{i}():\\n    return {i}\\n" for i in range(400)),
    "sample.ts": "\\n".join(f"function value{i}() {{ return {i}; }}" for i in range(400)),
    "sample.rs": "\\n".join(f"fn value{i}() -> i32 {{ {i} }}" for i in range(400)),
    "sample.cpp": "\\n".join(f"int value{i}() {{ return {i}; }}" for i in range(400)),
}
for path, text in fixtures.items():
    for _ in range(3):
        ranges = symbol_ranges(text, path)
        assert len(ranges) > 10, (path, ranges)
        assert all(0 <= a < b <= len(text.splitlines()) for a, b in ranges), (path, ranges)
        assert len(set(ranges)) == len(ranges)
        gc.collect()
        json.dumps({"path": path, "ranges": ranges})
print("Native symbol boundaries valid for five languages.")
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
