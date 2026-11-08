"""Regenerate a multiworld in-memory from player yamls + seed, using a vendored Archipelago checkout.

The resulting MultiWorld still has all logic rules attached, which is what lets us
compute reachability for arbitrary item sets later.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AP_DIR = ROOT / "vendor" / "Archipelago"


def _enter_ap() -> None:
    if str(AP_DIR) not in sys.path:
        sys.path.insert(0, str(AP_DIR))
    os.chdir(AP_DIR)  # AP resolves host.yaml, custom_worlds, etc. relative to its own dir


def regenerate(yaml_dir: Path, seed: int):
    _enter_ap()
    import ModuleUpdate
    ModuleUpdate.update_ran = True  # deps are installed up front; never prompt for input inside the server
    import Generate
    import Main

    # Generate treats every file in the folder as a player file, so isolate the yamls.
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="sdt_yamls_"))
    for f in yaml_dir.iterdir():
        if f.suffix.lower() in (".yaml", ".yml"):
            shutil.copy(f, tmp / f.name)

    argv = [
        "--player_files_path", str(tmp),
        "--seed", str(seed),
        "--skip_output",
        "--log_level", "warning",
    ]
    args = Generate.mystery_argparse(argv)
    erargs, seed = Generate.main(args)
    return Main.main(erargs, seed)
