import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preinstall_modules", ROOT / "preinstall-modules.py")
preinstall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preinstall)


class PreinstallModulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "ezyplatform"
        (self.root / "settings").mkdir(parents=True)
        (self.root / "settings/platform.properties").write_text("ezyplatform.version=1.5.4\n")

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, entries):
        path = Path(self.temp.name) / "module.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return path

    def official_archive(self):
        return self.archive({
            "/ezyarticle/info.properties": "java.version=1.8\n",
            "/ezyarticle/admin/plugins/ezyarticle/module.properties": "version=1.4.9\n",
            "/ezyarticle/admin/plugins/ezyarticle/lib/ezyarticle-admin.jar": "jar",
            "/ezyarticle/web/plugins/ezyarticle/module.properties": "version=1.4.9\n",
            "/ezyarticle/socket/plugins/ezyarticle/module.properties": "version=1.4.9\n",
        })

    def runtime(self, lane, kind="plugins"):
        return json.loads((self.root / ".runtime" / lane / f"{kind}.txt").read_text())

    def test_extracts_and_activates_every_lane(self):
        preinstall.install(self.root, self.official_archive())
        self.assertTrue((self.root / "admin/plugins/ezyarticle/lib/ezyarticle-admin.jar").is_file())
        self.assertFalse((self.root / "info.properties").exists())
        for lane in ("admin", "web", "socket"):
            self.assertEqual(self.runtime(lane), [{"name": "ezyarticle", "active": True}])

    def test_keeps_other_modules_and_reactivates_existing_entry(self):
        cache = self.root / ".runtime/admin/plugins.txt"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps([{"name": "ezymail", "active": True},
                                     {"name": "ezyarticle", "active": False}]))
        preinstall.install(self.root, self.official_archive())
        self.assertEqual(self.runtime("admin"), [{"name": "ezymail", "active": True},
                                                 {"name": "ezyarticle", "active": True}])

    def test_traversal_and_foreign_module_rejected(self):
        for name in ("/ezyarticle/admin/plugins/../../../evil", "/ezyarticle/admin/plugins/other/x",
                     "/ezyarticle/settings/setup.properties/x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preinstall.install(self.root, self.archive({name: "x"}))
        self.assertFalse((self.root / ".runtime").exists())

    def test_archive_without_module_rejected(self):
        with self.assertRaises(ValueError):
            preinstall.install(self.root, self.archive({"/ezyarticle/info.properties": "x"}))


if __name__ == "__main__":
    unittest.main()
