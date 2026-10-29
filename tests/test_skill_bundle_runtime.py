"""The VSIX skill action installs complete inert trees without following links."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal import skill_bundle


class RuntimeSkillBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='ia-runtime-skills-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.source=self.root/'bundled-skills'
        self.destination=self.root/'user-skills'
        self.files={'alpha-skill/SKILL.md':b'---\nname: alpha-skill\ndescription: Example alpha skill\n---\nRead [the guide](references/guide.md).\n',
                    'alpha-skill/references/guide.md':b'Read [rules](nested/rules.txt).\n',
                    'alpha-skill/references/nested/rules.txt':b'Preserve owner instructions.\n',
                    'zeta-skill/SKILL.md':b'---\nname: zeta-skill\ndescription: Example zeta skill\n---\n# Instructions\n',
                    'zeta-skill/settings.json':b'{"mode":"synthetic"}\n'}
        for name,data in self.files.items():self.write(self.source/name,data)

    def write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

    def test_complete_resources_install_and_identical_reinstall_preserves_files(self):
        result=skill_bundle.install(self.destination,self.source)
        self.assertEqual(result,dict(installed=['alpha-skill','zeta-skill'],user_instructions_preserved=True))
        for name,data in self.files.items():
            self.assertEqual((self.destination/name).read_bytes(),data)
            self.assertEqual((self.destination/name).stat().st_mode&0o777,0o600)
        note=self.destination/'alpha-skill/owner-notes.md';note.write_bytes(b'Owner notes\n')
        files={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.destination.rglob('*') if p.is_file()}
        self.assertEqual(skill_bundle.install(self.destination,self.source),result)
        self.assertEqual({p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files},files)

    def test_modified_instruction_in_later_skill_prevents_any_new_skill_write(self):
        target=self.destination/'zeta-skill/SKILL.md';self.write(target,b'Owner instructions\n')
        with self.assertRaisesRegex(Invalid,'differs'):skill_bundle.install(self.destination,self.source)
        self.assertFalse((self.destination/'alpha-skill').exists())
        self.assertEqual(target.read_bytes(),b'Owner instructions\n')

    def test_modified_reference_is_preserved_before_any_new_instruction_is_written(self):
        target=self.destination/'alpha-skill/references/guide.md';self.write(target,b'Owner reference\n')
        with self.assertRaisesRegex(Invalid,'differs'):skill_bundle.install(self.destination,self.source)
        self.assertFalse((self.destination/'alpha-skill/SKILL.md').exists())
        self.assertFalse((self.destination/'zeta-skill').exists())
        self.assertEqual(target.read_bytes(),b'Owner reference\n')

    def test_release_license_is_preserved_with_each_skill_and_conflicts_block_writes(self):
        notice=b'MIT fixture notice\n';(self.root/'LICENSE').write_bytes(notice)
        existing=self.destination/'zeta-skill/LICENSE';self.write(existing,b'Owner notice\n')
        with self.assertRaisesRegex(Invalid,'differs'):skill_bundle.install(self.destination,self.source)
        self.assertFalse((self.destination/'alpha-skill').exists())
        self.assertEqual(existing.read_bytes(),b'Owner notice\n')
        clean=self.root/'clean-user-skills';first=skill_bundle.install(clean,self.source)
        for skill in first['installed']:
            self.assertEqual((clean/skill/'LICENSE').read_bytes(),notice)
            self.assertEqual((clean/skill/'LICENSE').stat().st_mode&0o777,0o600)
        self.assertEqual(skill_bundle.install(clean,self.source),first)

    def test_destination_and_ancestor_links_do_not_create_external_files(self):
        external=self.root/'external';external.mkdir();sentinel=external/'owner.txt';sentinel.write_bytes(b'Owner\n')
        link=self.root/'destination-link';link.symlink_to(external,target_is_directory=True)
        for destination in [link,link/'nested-skills']:
            with self.subTest(destination=destination),self.assertRaises(Invalid):
                skill_bundle.install(destination,self.source)
        self.assertEqual(list(external.iterdir()),[sentinel])

    def test_linked_skill_reference_directory_and_file_are_all_rejected(self):
        external=self.root/'external';external.mkdir();owner=external/'guide.md';owner.write_bytes(b'Owner\n')
        for kind in ['skill','reference-directory','file']:
            destination=self.root/kind;destination.mkdir()
            if kind=='skill':target=destination/'alpha-skill';target.symlink_to(external,target_is_directory=True)
            elif kind=='reference-directory':
                (destination/'alpha-skill').mkdir();target=destination/'alpha-skill/references'
                target.symlink_to(external,target_is_directory=True)
            else:
                target=destination/'alpha-skill/references/guide.md';target.parent.mkdir(parents=True)
                target.symlink_to(owner)
            with self.subTest(kind=kind),self.assertRaises(Invalid):skill_bundle.install(destination,self.source)
            self.assertFalse((destination/'zeta-skill').exists())
            self.assertTrue(target.is_symlink())
        self.assertEqual(list(external.iterdir()),[owner])

    def test_source_root_and_ancestor_links_are_rejected_before_destination_creation(self):
        linked=self.root/'source-link';linked.symlink_to(self.source,target_is_directory=True)
        parent=self.root/'source-parent-link';parent.symlink_to(self.root,target_is_directory=True)
        for source in [linked,parent/self.source.name]:
            with self.subTest(source=source),self.assertRaises(Invalid):skill_bundle.install(self.destination,source)
            self.assertFalse(self.destination.exists())

    def test_source_skill_and_resource_links_are_rejected_before_writes(self):
        (self.source/'linked-skill').symlink_to(self.source/'alpha-skill',target_is_directory=True)
        with self.assertRaises(Invalid):skill_bundle.install(self.destination,self.source)
        self.assertFalse(self.destination.exists());(self.source/'linked-skill').unlink()
        resource=self.source/'alpha-skill/references/linked.md';resource.symlink_to(self.source/'zeta-skill/SKILL.md')
        with self.assertRaises(Invalid):skill_bundle.install(self.destination,self.source)
        self.assertFalse(self.destination.exists())

    def test_missing_entrypoint_reference_and_outside_reference_fail_before_writes(self):
        instruction=self.source/'alpha-skill/SKILL.md';original=instruction.read_bytes()
        for body in [b'[Missing](references/missing.md)\n',b'[Outside](../../outside.md)\n']:
            data=b'---\nname: alpha-skill\ndescription: Example\n---\n'+body
            instruction.write_bytes(data)
            with self.subTest(data=data),self.assertRaisesRegex(Invalid,'reference'):
                skill_bundle.install(self.destination,self.source)
            self.assertFalse(self.destination.exists())
        instruction.write_bytes(original);(self.source/'empty-skill').mkdir()
        with self.assertRaisesRegex(Invalid,'instructions are missing'):skill_bundle.install(self.destination,self.source)
        self.assertFalse(self.destination.exists())

    def test_non_text_executable_or_oversized_resources_fail_before_writes(self):
        resource=self.source/'zeta-skill/invalid.md'
        for data in [b'bad\x00text',b'\xff',b'x'*(skill_bundle.MAX_FILE+1)]:
            resource.write_bytes(data)
            with self.subTest(size=len(data)),self.assertRaises(Invalid):skill_bundle.install(self.destination,self.source)
            self.assertFalse(self.destination.exists())
        resource.unlink();script=self.source/'zeta-skill/run.py';script.write_bytes(b'print("do not execute")\n')
        with self.assertRaisesRegex(Invalid,'inert text'):skill_bundle.install(self.destination,self.source)
        self.assertFalse(self.destination.exists())

    def test_batch_resource_limits_prevent_partial_install(self):
        for name,limit in [('MAX_FILES',2),('MAX_TOTAL',10)]:
            with self.subTest(name=name),patch.object(skill_bundle,name,limit),self.assertRaisesRegex(Invalid,'limits exceeded'):
                skill_bundle.install(self.destination,self.source)
            self.assertFalse(self.destination.exists())

    @unittest.skipUnless(hasattr(os,'mkfifo'),'requires supported Unix file types')
    def test_special_source_and_destination_files_are_rejected_without_blocking(self):
        source=self.source/'zeta-skill/fifo.txt';os.mkfifo(source)
        with self.assertRaises(Invalid):skill_bundle.install(self.destination,self.source)
        self.assertFalse(self.destination.exists());source.unlink()
        target=self.destination/'zeta-skill/SKILL.md';target.parent.mkdir(parents=True);os.mkfifo(target)
        with self.assertRaises(Invalid):skill_bundle.install(self.destination,self.source)
        self.assertFalse((self.destination/'alpha-skill').exists())
        self.assertTrue(target.exists())

    def test_relative_overlapping_and_uncontrolled_destinations_are_rejected(self):
        for destination in [Path('relative-skills'),self.source,self.source/'nested',Path(self.root.anchor)]:
            with self.subTest(destination=destination),self.assertRaises(Invalid):
                skill_bundle.install(destination,self.source)
        self.destination.mkdir();self.destination.chmod(0o777)
        with self.assertRaisesRegex(Invalid,'owner controlled'):skill_bundle.install(self.destination,self.source)
        self.assertEqual(list(self.destination.iterdir()),[])

    def test_concurrent_user_file_is_never_overwritten(self):
        original=os.link;changed=[]
        def concurrent(source,target,**kwargs):
            descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=kwargs['dst_dir_fd'])
            with os.fdopen(descriptor,'wb') as stream:stream.write(b'Concurrent owner instructions\n')
            changed.append(target)
            return original(source,target,**kwargs)
        with patch.object(skill_bundle.os,'link',side_effect=concurrent),self.assertRaisesRegex(Invalid,'differs'):
            skill_bundle.install(self.destination,self.source)
        self.assertEqual(changed,['SKILL.md'])
        self.assertEqual((self.destination/'alpha-skill/SKILL.md').read_bytes(),b'Concurrent owner instructions\n')
        self.assertEqual(list(self.destination.rglob('.intern-agent-write-*')),[])

    def test_invalid_frontmatter_is_rejected_before_destination_creation(self):
        path=self.source/'alpha-skill/SKILL.md'
        for data in [b'No frontmatter\n',b'---\nname: another-name\ndescription: Example\n---\n',
                     b'---\nname: alpha-skill\ndescription: []\n---\n',
                     b'---\nname: alpha-skill\ndescription: Example\ndescription: Duplicate\n---\n']:
            path.write_bytes(data)
            with self.subTest(data=data),self.assertRaises(Invalid):
                skill_bundle.install(self.destination,self.source)
            self.assertFalse(self.destination.exists())

    def test_verify_checks_all_resources_and_notices_without_writing_or_execution(self):
        (self.root/'LICENSE').write_bytes(b'MIT fixture notice\n')
        skill_bundle.install(self.destination,self.source)
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.destination.rglob('*') if p.is_file()}
        report=skill_bundle.verify(self.destination,self.source)
        self.assertEqual(report['files_verified'],len(self.files)+2)
        self.assertFalse(report['skill_discovery_verified']);self.assertFalse(report['skills_execution_verified'])
        self.assertFalse(report['writes_files'])
        self.assertEqual({p:(p.read_bytes(),p.stat().st_mtime_ns) for p in before},before)
        resource=self.destination/'alpha-skill/references/guide.md';resource.unlink()
        with self.assertRaisesRegex(Invalid,'missing'):skill_bundle.verify(self.destination,self.source)
        self.assertFalse(resource.exists())


if __name__=='__main__':unittest.main()
