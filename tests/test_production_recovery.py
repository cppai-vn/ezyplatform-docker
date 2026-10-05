import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("production_recovery", Path(__file__).parents[1] / "scripts/apply-production-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryPreflightTests(unittest.TestCase):
    def test_tree_uses_canonical_relative_filename_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a").mkdir()
            (root / "a/file.txt").write_bytes(b"nested")
            (root / "a.txt").write_bytes(b"sibling")
            records = [[name, hashlib.sha256(data).hexdigest()] for name, data in
                       [("a/file.txt", b"nested"), ("a.txt", b"sibling")]]
            expected = hashlib.sha256(json.dumps(sorted(records), separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(recovery.tree(root), expected)

    def test_symlink_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "target").write_text("private")
            (root / "link").symlink_to(root / "target")
            with self.assertRaises(ValueError):
                recovery.tree(root)

    def test_unsafe_relative_paths_are_rejected(self):
        for path in ("/etc/passwd", "../outside", "admin/../../outside", "."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                recovery.relative(path)

    def test_missing_module_is_distinct_from_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(recovery.tree(Path(d) / "absent"))
            self.assertIsNotNone(recovery.tree(Path(d)))
