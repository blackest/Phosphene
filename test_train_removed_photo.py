"""Re-training after removing a photo works (ov-review D-01).

crop_and_caption() renumbers the dataset char_001..N on every run and the
preprocess step encodes every png in images_renamed/. A photo removed from a
12-photo dataset left the previous run's char_012.png behind: the retry
encoded 12 frames for 11 photos and stopped with "preprocess wrote 12
latents; expected 11" - and every retry after it did the same.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from lora_lab import train_character as TC  # noqa: E402


class RemovedPhotoLeavesNoFrame(unittest.TestCase):
    def _sources(self, root: Path, n: int) -> list[Path]:
        out = []
        for i in range(n):
            f = root / "images" / f"photo_{i:02d}.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 48), (i * 20 % 255, 80, 120)).save(f)
            out.append(f)
        return out

    def _crop(self, root: Path, sources: list[Path]) -> list[Path]:
        renamed, _ = TC.crop_and_caption(
            source_files=sources, cropped_dir=root / "cropped",
            captions_dir=root / "captions", images_renamed_dir=root / "images_renamed",
            trigger="chartest_a", caption_strategy="class_word", width=32, height=32)
        return renamed

    def test_the_second_run_has_exactly_its_own_frames(self):
        root = Path(tempfile.mkdtemp(prefix="train-rm-"))
        sources = self._sources(root, 4)
        self.assertEqual(len(self._crop(root, sources)), 4)
        sources[1].unlink()                         # /train/remove-image
        renamed = self._crop(root, [s for s in sources if s.exists()])
        on_disk = sorted(p.name for p in (root / "images_renamed").glob("*.png"))
        self.assertEqual(on_disk, sorted(p.name for p in renamed))
        self.assertEqual(len(on_disk), 3)
        self.assertEqual(len(list((root / "cropped").glob("*.jpg"))), 3)


if __name__ == "__main__":
    unittest.main()
