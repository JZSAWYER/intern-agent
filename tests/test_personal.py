from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid, Store, default_config, github_url, git_environment, validate_config
from intern_personal.planner import migration_plan


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "personal")
        self.store.initialize()

    def test_github_transport_normalization(self):
        for url in ["https://github.com/alice/project", "git@github.com:alice/project.git"]:
            self.assertEqual(github_url(url), "https://github.com/alice/project.git")

    def test_non_github_and_credential_urls_rejected(self):
        for url in ["https://codeup.aliyun.com/org/project", "git@codeup.aliyun.com:org/project",
                    "https://github.com.evil.test/a/b", "https://github.com@evil.test/a/b",
                    "https://token@github.com/a/b", "https://github.com/a/b?token=fake",
                    "https://github.com/a/../b", "https://github.com/a/%2e%2e",
                    "file:///tmp/project", "ext::sh -c command", "-u", "https://github.com/a/b\n"]:
            with self.subTest(url=url), self.assertRaises(Invalid):
                github_url(url)

    def test_configuration_rejects_legacy_overrides_and_secrets(self):
        for key, value in [("provider", "codeup"), ("app_secret", "synthetic-secret"),
                           ("state_root", "/work-agents"), ("execution", "live")]:
            config = default_config()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(Invalid):
                validate_config(config)

    def test_configuration_not_overwritten(self):
        before = self.store.config.read_bytes()
        with self.assertRaises(Invalid):
            self.store.initialize("alice")
        self.assertEqual(self.store.config.read_bytes(), before)

    def test_personal_state_ignores_environment_and_home_files(self):
        fake_home = self.root / "home"
        fake_home.mkdir()
        (fake_home / ".codeup_env").write_text("export NEVER_LOAD=synthetic-secret\n")
        with patch.dict(os.environ, {"HOME": str(fake_home), "WORK_AGENTS_ROOT": "/legacy",
                                     "INTERN_ENTERPRISE_POLICY": "/legacy/policy.json",
                                     "GIT_DIR": "/legacy/.git", "GIT_SSH_COMMAND": "unsafe"}):
            self.store.add_workspace("demo", "git@github.com:alice/demo.git")
            environment = git_environment()
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("GIT_SSH_COMMAND", environment)
        self.assertNotIn("HOME", environment)
        self.assertEqual(environment["GIT_ALLOW_PROTOCOL"], "")
        self.assertTrue((self.store.state / "workspaces.json").exists())
        self.assertEqual(self.store.config.stat().st_mode & 0o777, 0o600)

    def test_state_symlink_escape_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.store.state.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(Invalid):
            self.store.add_workspace("demo", "https://github.com/alice/demo")
        self.assertEqual(list(outside.iterdir()), [])

    def test_duplicate_and_traversal_workspaces_rejected(self):
        self.store.add_workspace("demo", "https://github.com/alice/demo")
        before = (self.store.state / "workspaces.json").read_bytes()
        for wid, url in [("demo", "https://github.com/alice/other"),
                         ("other", "git@github.com:alice/demo.git"),
                         ("../outside", "https://github.com/alice/other")]:
            with self.assertRaises(Invalid):
                self.store.add_workspace(wid, url)
        self.assertEqual((self.store.state / "workspaces.json").read_bytes(), before)

    def test_owner_and_metadata_contract(self):
        other = Store(self.root / "owned")
        other.initialize("alice")
        with self.assertRaises(Invalid):
            other.add_workspace("demo", "https://github.com/bob/demo")
        for branch in [None, "../bad", "-option", "bad.lock", "a b"]:
            with self.assertRaises(Invalid):
                other.add_workspace("demo", "https://github.com/alice/demo", "metadata_branch", branch)
        entry = other.add_workspace("demo", "https://github.com/alice/demo", "metadata_branch", "intern_workspace")
        self.assertEqual(entry["metadata_branch"], "intern_workspace")

    def test_concurrent_registration_does_not_lose_workspaces(self):
        def add(index):
            return self.store.add_workspace(f"demo{index}", f"https://github.com/alice/demo{index}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(add, range(12)))
        self.assertEqual(len(json.loads((self.store.state / "workspaces.json").read_text())), 12)

    def test_registry_with_non_github_record_is_not_extended(self):
        self.store.write(self.store.state / "workspaces.json", {
            "legacy": {"provider": "codeup", "repo_url": "https://codeup.invalid/a/b"}})
        before = (self.store.state / "workspaces.json").read_bytes()
        with self.assertRaises(Invalid):
            self.store.add_workspace("demo", "https://github.com/alice/demo")
        self.assertEqual((self.store.state / "workspaces.json").read_bytes(), before)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "state" / "v1" / "ws_example"
        self.workspace.mkdir(parents=True)
        self.record = self.workspace / "workspace.json"
        self.record.write_text(json.dumps({"provider": "codeup", "repo_url": "https://synthetic-secret@codeup.invalid/x",
                                          "provider_config": {"token": "synthetic-secret"},
                                          "metadata_mode": "metadata_branch", "metadata_branch": "intern_workspace"}))

    def git(self, *args):
        result = subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(self.repo), *args],
                                env=git_environment(), check=True, capture_output=True, text=True)
        return result.stdout

    def setup_repo(self):
        self.repo = self.workspace / "source"
        self.repo.mkdir()
        self.git("init", "--initial-branch=main")
        (self.repo / "example.txt").write_text("original\n")
        self.git("add", "example.txt")
        self.git("-c", "user.name=Offline Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        self.git("remote", "add", "origin", "https://codeup.invalid/never-contact")

    def test_missing_repository_and_destination_reported_without_network(self):
        before = self.record.read_bytes()
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = migration_plan(self.root)
        item = result["workspaces"][0]
        self.assertIn("no_local_repository_found", item["blockers"])
        self.assertIn("explicit_github_destination_required", item["blockers"])
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertEqual(before, self.record.read_bytes())

    def test_dirty_work_and_metadata_preserved_without_git_mutation(self):
        self.setup_repo()
        self.git("branch", "intern_workspace")
        (self.repo / "example.txt").write_text("uncommitted\n")
        files = [self.record, self.repo / ".git" / "index", self.repo / ".git" / "config", self.repo / "example.txt"]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        result = migration_plan(self.root, {"ws_example": "git@github.com:alice/example.git"})
        item = result["workspaces"][0]
        self.assertIn("preserve_uncommitted_work_before_migration", item["blockers"])
        self.assertTrue(item["local_repositories"][0]["metadata_branch_available_locally"])
        self.assertFalse(item["ready"])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    def test_clean_repository_still_requires_authorization_and_completeness_checks(self):
        self.setup_repo()
        result = migration_plan(self.root, {"ws_example": "https://github.com/alice/example"})
        item = result["workspaces"][0]
        self.assertIn("metadata_branch_not_found_locally", item["blockers"])
        self.assertFalse(item["ready"])
        self.assertIn("migration_authorization", item["remaining_checks"])

    def test_destination_validation_and_collisions(self):
        with self.assertRaises(Invalid):
            migration_plan(self.root, {"ws_example": "https://codeup.invalid/a/b"})
        with self.assertRaises(Invalid):
            migration_plan(self.root, {"unknown": "https://github.com/alice/example"})
        other = self.workspace.parent / "ws_other"
        other.mkdir()
        (other / "workspace.json").write_bytes(self.record.read_bytes())
        result = migration_plan(self.root, {wid: "https://github.com/alice/example" for wid in ["ws_example", "ws_other"]})
        self.assertTrue(all("duplicate_github_destination" in item["blockers"] for item in result["workspaces"]))

    def test_malformed_record_does_not_hide_other_records(self):
        self.record.write_text("not-json")
        result = migration_plan(self.root)
        self.assertEqual(result["workspaces"][0]["blockers"], ["invalid_workspace_record"])

    def test_source_symlink_is_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / ".git").mkdir()
        (self.workspace / "source").symlink_to(outside, target_is_directory=True)
        result = migration_plan(self.root)
        self.assertEqual(result["summary"]["local_repositories"], 0)


if __name__ == "__main__":
    unittest.main()
