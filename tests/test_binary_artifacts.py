import base64
import struct
import unittest

from intern_personal.core import Invalid
from intern_personal.git_hooks import validate

PDF=b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def woff2_envelope(payload=b'\xff\xfe\x00'):
    # A bounded header fixture; font rendering/decompression is not this guard's job.
    directory=b'\x00\x01'
    return struct.pack('>4s4sIHHIIHHIIIII',b'wOF2',b'\x00\x01\x00\x00',
                       48+len(directory)+len(payload),1,0,256,len(payload),1,0,0,0,0,0,0)+directory+payload


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

    def test_web_font_envelope_is_accepted_only_as_a_code_asset(self):
        font=woff2_envelope()
        validate('assets/fonts/example.ttf.woff2',font)
        for name,data,management in [('.intern_workspace/font.woff2',font,True),
                ('font.woff2',PNG,False),('font.bin',font,False),
                ('.codex/font.woff2',font,False),('credentials.woff2',font,False),
                ('font.woff2',woff2_envelope(b'0'*(10*1024*1024)),False),
                ('font.woff2',woff2_envelope(b'-----'+b'BEGIN RSA PRIVATE KEY'+b'-----'),False)]:
            with self.subTest(name=name),self.assertRaises(Invalid):validate(name,data,management)

    def test_truncated_font_and_invalid_optional_block_bounds_are_rejected(self):
        font=woff2_envelope()
        malformed=[b'wOF2',font[:-1],font+b'extra']
        for offset,replacement in [(12,b'\x00\x00'),(20,(len(font)).to_bytes(4,'big')),
                (28,(len(font)+1).to_bytes(4,'big')),(32,b'\x00\x00\x00\x01'),
                (40,(len(font)+1).to_bytes(4,'big')),(44,b'\x00\x00\x00\x01')]:
            data=bytearray(font);data[offset:offset+len(replacement)]=replacement;malformed.append(bytes(data))
        for data in malformed:
            with self.subTest(data=data[:48]),self.assertRaises(Invalid):validate('font.woff2',data)


if __name__=='__main__':unittest.main()
