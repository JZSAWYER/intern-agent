import copy
import json
from pathlib import Path
import tempfile
import unittest

from intern_personal.core import Invalid, Store, default_config, github_url, validate_config
from intern_personal.distribution import package_files
from intern_personal.runtime import Policy, Sessions

PROJECT = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def test_shared_profile_placeholder_requires_real_input(self):
        config = json.loads((PROJECT / "templates" / "personal.example.json").read_text())
        with self.assertRaises(Invalid):
            validate_config(config)
        config["github_owner"] = "example-user"
        self.assertEqual(validate_config(config)["github_owner"], "example-user")
        self.assertIsNone(default_config()["github_owner"])

    def test_binding_placeholders_are_not_usable(self):
        data = json.loads((PROJECT / "templates" / "bindings.example.json").read_text())
        with self.assertRaises(Invalid):
            Policy(**data)

    def test_username_and_url_placeholders_rejected(self):
        for owner in ["<github-username>", "YOUR_LOGIN", "your-github-username", "change-me", "a--b", "", None]:
            if owner is not None:
                with self.assertRaises(Invalid):
                    default_config(owner)
            with self.assertRaises(Invalid):
                github_url(f"https://github.com/{owner if owner is not None else '<owner>'}/repo")

    def test_change_owner_is_local_and_rejects_implicit_repo_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.initialize()
            store.set_owner("example-user")
            self.assertEqual(store.load()["github_owner"], "example-user")
            store.add_workspace("demo", "https://github.com/example-user/demo")
            before = store.config.read_bytes()
            with self.assertRaises(Invalid):
                store.set_owner("another-user")
            self.assertEqual(store.config.read_bytes(), before)
            with self.assertRaises(Invalid):
                store.set_owner("<github-username>")

    def test_unset_owner_cannot_prepare_runtime(self):
        with tempfile.TemporaryDirectory(prefix="ia-owner-") as folder:
            store = Store(Path(folder))
            store.initialize()
            store.add_workspace("demo", "https://github.com/example-user/demo")
            with self.assertRaises(Invalid):
                Sessions(store).prepare("demo")


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "intern_personal").mkdir()
        (self.root / "intern_personal" / "safe.py").write_text('VALUE = "public"\n')
        self.manifest(["intern_personal/*.py"])

    def manifest(self, patterns):
        (self.root / "distribution.json").write_text(json.dumps({
            "schema": "intern-agent.distribution-files.v1", "include": patterns}))

    def test_real_project_allowlist_excludes_local_files(self):
        local = json.loads((PROJECT / "personal.json").read_text()) if (PROJECT / "personal.json").exists() else {}
        files = package_files(PROJECT, local.get("github_owner"))
        self.assertIn("intern_personal/runtime.py", files)
        self.assertIn("templates/personal.example.json", files)
        self.assertIn("skills/feishu-file-transfer/SKILL.md", files)
        self.assertNotIn("personal.json", files)
        self.assertFalse(any(f.startswith(("runtime/", "reports/", "reference/")) for f in files))

    def test_private_state_does_not_enter_default_allowlist(self):
        (self.root / "personal.json").write_text('{"github_owner":"private-test-owner-x91"}')
        self.assertEqual(package_files(self.root), ["intern_personal/safe.py"])

    def test_manifest_cannot_include_private_state(self):
        (self.root / "personal.json").write_text('{}')
        self.manifest(["personal.json"])
        with self.assertRaises(Invalid):
            package_files(self.root)

    def test_manifest_cannot_escape_or_include_links(self):
        self.manifest(["../*.json"])
        with self.assertRaises(Invalid):
            package_files(self.root)
        self.manifest(["intern_personal/*.py"])
        (self.root / "intern_personal" / "linked.py").symlink_to(self.root / "intern_personal" / "safe.py")
        with self.assertRaises(Invalid):
            package_files(self.root)

    def test_private_username_is_rejected_without_printing_it(self):
        (self.root / "intern_personal" / "safe.py").write_text('OWNER = "private-test-owner-x91"\n')
        with self.assertRaises(Invalid):
            package_files(self.root, "private-test-owner-x91")

    def test_plaintext_secret_literal_is_rejected(self):
        (self.root / "intern_personal" / "safe.py").write_text('APP_SECRET = "synthetic-private-value"\n')
        with self.assertRaises(Invalid):
            package_files(self.root)


if __name__ == "__main__":
    unittest.main()
