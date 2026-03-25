from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path


def _prepare_local_imports() -> None:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    for cache_dir in (root / "__pycache__", root / "videogenius_ai" / "__pycache__"):
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
    importlib.invalidate_caches()
    sys.dont_write_bytecode = True


def main() -> None:
    _prepare_local_imports()
    from videogenius_ai.gui import run

    run()


if __name__ == "__main__":
    main()
