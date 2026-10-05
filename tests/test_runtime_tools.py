import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


quarantine = load("quarantine", "quarantine-core-dependencies.py")
upgrade = load("upgrade", "upgrade-local-modules.py")


class QuarantineTests(unittest.TestCase):
    def test_artifact_names(self):
        self.assertEqual(quarantine.artifact_name(Path("thymeleaf-layout-dialect-3.1.0.jar")),
                         "thymeleaf-layout-dialect")
        self.assertEqual(quarantine.artifact_name(Path("ezyplatform-http-sdk-0.0.2.jar")),
                         "ezyplatform-http-sdk")

    def fixture(self, temp):
        root = Path(temp) / "app"
        for path in ("lib/thymeleaf-layout-dialect-3.4.0.jar",
                     "admin/plugins/custom/lib/thymeleaf-layout-dialect-3.1.0.jar",
                     "admin/plugins/custom/lib/custom-admin-plugin-1.0.0.jar",
                     "web/themes/custom/lib/thymeleaf-layout-dialect-3.1.0.jar"):
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(path.encode())
        return root.resolve()

    def test_plan_does_not_move_files_or_remove_unique_plugin(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.fixture(t)
            moves = quarantine.plan(root)
            self.assertEqual(len(moves), 2)
            self.assertTrue(all(p.exists() for p, _ in moves))
            self.assertFalse(any("custom-admin-plugin" in p.name for p, _ in moves))

    def test_apply_is_reversible_and_hashes_originals(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.fixture(t)
            dest = Path(t) / "quarantine"
            manifest = quarantine.apply(root, dest, quarantine.plan(root))
            self.assertEqual(len(manifest), 2)
            for item in manifest:
                old = dest / item["source"]
                self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(), item["sha256"])
                self.assertFalse((root / item["source"]).exists())
                self.assertTrue((root / item["provider"]).exists())

    def test_quarantine_inside_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.fixture(t)
            with self.assertRaises(ValueError):
                quarantine.apply(root, root / "backup", quarantine.plan(root))

    def test_symlink_jar_rejected_before_moves(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.fixture(t)
            source = root / "admin/plugins/custom/lib/thymeleaf-layout-dialect-3.1.0.jar"
            source.unlink()
            source.symlink_to(root / "lib/thymeleaf-layout-dialect-3.4.0.jar")
            with self.assertRaises(ValueError):
                quarantine.apply(root, Path(t) / "quarantine", quarantine.plan(root))
            self.assertTrue(source.is_symlink())


class UpgradeTests(unittest.TestCase):
    def test_official_leading_slash_prefix(self):
        self.assertEqual(upgrade.archive_path("/ecommerce/admin/plugins/ecommerce/module.properties", "ecommerce"),
                         Path("admin/plugins/ecommerce/module.properties"))

    def test_chat_socket_app_supported(self):
        self.assertEqual(upgrade.archive_path("/ezychat/socket/apps/ezychat/module.properties", "ezychat"),
                         Path("socket/apps/ezychat/module.properties"))

    def test_traversal_and_foreign_module_rejected(self):
        for path in ("/ecommerce/../settings/setup.properties", "/ecommerce/admin/plugins/other/file",
                     "/ecommerce/settings/setup.properties", "/wrong/admin/plugins/ecommerce/file"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                upgrade.archive_path(path, "ecommerce")

    def test_production_database_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            runtime = Path(t) / "app"
            (runtime / "settings").mkdir(parents=True)
            (runtime / "settings/setup.properties").write_text("datasource.jdbc_url=jdbc:mysql://production:3306/blog\n")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/upgrade-local-modules.py"),
                                     "--runtime", str(runtime), "--rollback", str(Path(t) / "rollback"),
                                     "--cache", str(Path(t) / "cache")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing non-local", result.stderr)
            self.assertFalse((Path(t) / "rollback").exists())

    def test_checksum_mismatch_preserves_runtime(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            runtime, cache = t / "app", t / "cache"
            (runtime / "settings").mkdir(parents=True)
            (runtime / "settings/setup.properties").write_text("datasource.jdbc_url=jdbc:mysql://mysql:3306/blog\n")
            (runtime / "settings/platform.properties").write_text("ezyplatform.version=1.5.0\n")
            original = runtime / "admin/plugins/test/module.properties"
            original.parent.mkdir(parents=True)
            original.write_text("version=0.1.0\n")
            cache.mkdir()
            with zipfile.ZipFile(cache / "test-1.0.0.zip", "w") as z:
                z.writestr("/test/admin/plugins/test/module.properties", "version=1.0.0\n")
            lock = t / "lock.json"
            lock.write_text(json.dumps({"platform_version": "1.5.0", "plugins": [
                {"name": "test", "version": "1.0.0", "sha256": "0" * 64}]}))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/upgrade-local-modules.py"),
                                     "--runtime", str(runtime), "--rollback", str(t / "rollback"),
                                     "--cache", str(cache), "--lock", str(lock)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Checksum mismatch", result.stderr)
            self.assertEqual(original.read_text(), "version=0.1.0\n")
            self.assertFalse((t / "rollback").exists())


if __name__ == "__main__":
    unittest.main()
