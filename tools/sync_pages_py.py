"""Copy the generator modules the Event Builder preview needs into docs/py/txnforge/ so GitHub Pages can serve them.

The page loads these files into Pyodide (Python in the browser). A test fails if the copies drift from src/txnforge.
Run after changing any of these modules:  python tools/sync_pages_py.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ["__init__.py", "generator.py", "levers.py", "rules.py", "validator.py"]


def sync():
    dst = ROOT / "docs" / "py" / "txnforge"
    dst.mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        text = (ROOT / "src" / "txnforge" / name).read_bytes().replace(b"\r\n", b"\n")
        (dst / name).write_bytes(text)
    return [dst / n for n in MODULES]


if __name__ == "__main__":
    for p in sync():
        print("wrote", p.relative_to(ROOT))
