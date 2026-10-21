"""The image has every module the server imports: a new top-level .py file must be in the Dockerfile's COPY,
or the container starts with ModuleNotFoundError (defaultboard.py and gitsource.py once were not)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Dockerfile(unittest.TestCase):
    def test_every_module_is_copied(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        copied = set(re.findall(r"^COPY ((?:[\w-]+\.py )+)\./?$", text, re.M)[0].split())
        modules = {p.name for p in ROOT.glob("*.py")}
        self.assertEqual(sorted(modules - copied), [], "not in the Dockerfile's COPY")
        self.assertEqual(sorted(copied - modules), [], "copied but not there")


if __name__ == "__main__":
    unittest.main()
