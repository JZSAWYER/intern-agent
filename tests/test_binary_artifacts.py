import base64
import unittest

from intern_personal.core import Invalid
from intern_personal.git_hooks import validate

PDF=b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class BinaryArtifactTests(unittest.TestCase):
    def test_research_pdf_and_plot_are_validated_as_artifacts(self):
        validate('papers/example.pdf',PDF)
        validate('figures/plot.png',PNG)

    def test_binary_metadata_and_mislabelled_files_are_rejected(self):
        for name,data,management in [('.intern_workspace/paper.pdf',PDF,True),
                ('plot.png',PDF,False),('paper.pdf',PNG,False),('unknown.bin',b'\xff\xfe\0',False),
                ('data.json',b'\xff\xfe',False),('broken.py',b'\xff\xfe',False)]:
            with self.subTest(name=name),self.assertRaises(Invalid):validate(name,data,management)

    def test_artifacts_keep_size_private_path_and_credential_checks(self):
        for name,data in [('paper.pdf',PDF+b'0'*(10*1024*1024)),
                ('.codex/paper.pdf',PDF),('credentials.pdf',PDF),
                ('paper.pdf',PDF+b'\n'+b'-----'+b'BEGIN RSA PRIVATE KEY'+b'-----\n')]:
            with self.subTest(name=name),self.assertRaises(Invalid):validate(name,data)


if __name__=='__main__':unittest.main()
