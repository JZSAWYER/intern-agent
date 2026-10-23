from pathlib import Path
import tempfile
import unittest

from intern_personal.core import Invalid
from intern_personal.live_state import LiveState
from intern_personal.deployment import prepare_runtime,service_preferences


class DeploymentTests(unittest.TestCase):
    def test_recovery_and_transfer_skills_are_both_pinned_and_unexpected_skills_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';package=source/'intern_personal';package.mkdir(parents=True)
            (package/'feishu_live.py').write_text('version = 1\n')
            for name in ['recover-intern-agent','feishu-file-transfer']:
                file=source/'skills'/name/'SKILL.md';file.parent.mkdir(parents=True);file.write_text('# '+name+'\n')
            state=LiveState(root/'private')
            try:
                release=prepare_runtime(state,source)
                for name in ['recover-intern-agent','feishu-file-transfer']:
                    self.assertEqual((release/'skills'/name/'SKILL.md').read_bytes(),(source/'skills'/name/'SKILL.md').read_bytes())
                rogue=release/'skills/unexpected/SKILL.md';rogue.parent.mkdir();rogue.write_text('# Unexpected instruction\n')
                with self.assertRaisesRegex(Invalid,'Unexpected runtime skill'):prepare_runtime(state,source)
            finally:state.close()

    def test_complete_public_runbooks_travel_with_the_immutable_runtime(self):
        project=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            state=LiveState(Path(directory)/'private')
            try:
                release=prepare_runtime(state,project)
                for guide in (project/'docs').glob('*.md'):
                    with self.subTest(guide=guide.name):
                        self.assertEqual((release/'docs'/guide.name).read_bytes(),guide.read_bytes())
                operator=release/'docs/RECOVERY_OPERATOR.md'
                self.assertTrue(operator.is_file())
                # The operator's relative recovery/file-delivery links resolve
                # even when its project has none of the relay's documentation.
                import re
                for linked in re.findall(r'\]\(([A-Z_]+\.md)(?:#[^)]*)?\)',operator.read_text()):
                    self.assertTrue((operator.parent/linked).is_file(),linked)
                operator.write_text('tampered operator policy\n')
                with self.assertRaises(Invalid):prepare_runtime(state,project)
            finally:state.close()

    def test_runbook_updates_change_the_release_and_linked_guides_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';package=source/'intern_personal';package.mkdir(parents=True)
            (package/'feishu_live.py').write_text('version = 1\n')
            guide=source/'docs/RECOVERY_OPERATOR.md';guide.parent.mkdir();guide.write_text('# Recovery policy v1\n')
            state=LiveState(root/'private')
            try:
                first=prepare_runtime(state,source)
                guide.write_text('# Recovery policy v2\n')
                second=prepare_runtime(state,source)
                self.assertNotEqual(first,second)
                self.assertEqual((first/'docs'/guide.name).read_text(),'# Recovery policy v1\n')
                outside=root/'private-note.md';outside.write_text('private-note-sentinel\n')
                guide.unlink();guide.symlink_to(outside)
                with self.assertRaises(Invalid):prepare_runtime(state,source)
            finally:state.close()

    def test_shared_peer_guide_is_bundled_for_other_project_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'source/intern_personal';package.mkdir(parents=True)
            (package/'feishu_live.py').write_text('version = 1\n')
            guide=root/'source/docs/BOT_COMMUNICATION.md';guide.parent.mkdir();guide.write_text('# Fixed peer protocol\n')
            state=LiveState(root/'private')
            try:
                release=prepare_runtime(state,root/'source');self.assertEqual((release/'docs/BOT_COMMUNICATION.md').read_text(),guide.read_text())
                (release/'docs/BOT_COMMUNICATION.md').write_text('tampered\n')
                with self.assertRaises(Invalid):prepare_runtime(state,root/'source')
            finally:state.close()

    def test_fast_observation_refresh_is_not_a_preference_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state=LiveState(Path(directory)/'private')
            try:
                from intern_personal.speed import save_observed
                state.register_workspace('fixture',directory)
                session,_=state.reserve_session('fixture')
                save_observed(state,session,'priority','2026-10-14T01:00:00Z')
                before=service_preferences(state)
                save_observed(state,session,'priority','2026-10-14T02:00:00Z')
                self.assertEqual(service_preferences(state),before)
                save_observed(state,session,'default','2026-10-14T03:00:00Z')
                self.assertNotEqual(service_preferences(state),before)
            finally:state.close()

    def test_file_skill_is_snapshotted_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'source/intern_personal';package.mkdir(parents=True)
            (package/'feishu_live.py').write_text('version = 1\n')
            skill=root/'source/skills/feishu-file-transfer/SKILL.md';skill.parent.mkdir(parents=True)
            skill.write_text('Transfer workflow version one\n')
            state=LiveState(root/'private')
            try:
                first=prepare_runtime(state,root/'source')
                bundled=first/'skills/feishu-file-transfer/SKILL.md'
                self.assertEqual(bundled.read_text(),skill.read_text())
                self.assertEqual(bundled.stat().st_mode&0o777,0o600)
                skill.write_text('Transfer workflow version two\n')
                second=prepare_runtime(state,root/'source');self.assertNotEqual(first,second)
                (second/'skills/feishu-file-transfer/SKILL.md').write_text('Tampered\n')
                with self.assertRaises(Invalid):prepare_runtime(state,root/'source')
            finally:state.close()

    def test_branch_changes_do_not_change_running_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'source/intern_personal';package.mkdir(parents=True)
            file=package/'feishu_live.py';file.write_text('version = 1\n')
            state=LiveState(root/'private')
            try:
                first=prepare_runtime(state,root/'source')
                self.assertEqual(first,prepare_runtime(state,root/'source'))
                file.write_text('version = 2\n');second=prepare_runtime(state,root/'source')
                self.assertNotEqual(first,second)
                self.assertEqual((first/'intern_personal/feishu_live.py').read_text(),'version = 1\n')
                self.assertEqual((second/'intern_personal/feishu_live.py').stat().st_mode&0o777,0o600)
                (second/'intern_personal/feishu_live.py').write_text('tampered = True\n')
                with self.assertRaises(Invalid):prepare_runtime(state,root/'source')
            finally:state.close()

    def test_linked_backend_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'source/intern_personal';package.mkdir(parents=True)
            outside=root/'outside.py';outside.write_text('answer = 42\n')
            (package/'feishu_live.py').symlink_to(outside)
            state=LiveState(root/'private')
            try:
                with self.assertRaises(Invalid):prepare_runtime(state,root/'source')
            finally:state.close()


if __name__=='__main__':unittest.main()
