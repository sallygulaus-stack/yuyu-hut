"""Seed the approved food once per data directory, never during later edits."""
import os
from pathlib import Path
import shutil
import tempfile


def seed_food(root: Path, web: Path):
    food = root / "food"
    marker = root / ".starter-food-v1"
    if marker.exists():
        return
    food.mkdir(parents=True, exist_ok=True)
    target = food / "白米饭.png"
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=food, delete=False, suffix=".part") as tmp:
            staged = Path(tmp.name)
            try:
                with (web / "assets" / "starter-rice.png").open("rb") as source:
                    shutil.copyfileobj(source, tmp)
            except BaseException:
                tmp.close()
                staged.unlink(missing_ok=True)
                raise
        try:
            # Publish complete bytes only; never overwrite an existing/custom item.
            try:
                os.link(staged, target)
            except FileExistsError:
                pass
            except OSError:
                try:
                    dest = target.open("xb")
                except FileExistsError:
                    pass
                else:
                    with dest, staged.open("rb") as source:
                        shutil.copyfileobj(source, dest)
        finally:
            staged.unlink(missing_ok=True)
    marker.touch()
