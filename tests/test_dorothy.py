import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dorothy_lib.atlas import png_bytes
from dorothy_lib.images import png_signature
from dorothy_lib.kzb import parse
from dorothy_lib.rebuild import rebuild, validate
from dorothy_lib.ui import App, Languages
from dorothy_lib.workflows import Workspace, integrity, extract_kzb, build_custom, optimise_kzb, pull, scp_command

ROOT = Path(__file__).resolve().parents[1]


def string(value):
    raw = value.encode()
    data = struct.pack('>H', len(raw)) + raw
    return data + b'\0' * (-len(data) % 4)


def fixture():
    """Tiny redistributable KZB, with a generated image and an engine object."""
    image = png_bytes(4, 3, 4, bytes([30, 60, 90, 255])*12)
    specs = [('Resource Files/Images', [('demo.png', 10, image), ('empty.xlsx', 0, b'')]), ('Layers', [('root', 180, b'engine-data')])]
    groups = bytearray()
    slots, payloads = [], []
    for group, entries in specs:
        block = bytearray(b'\0'*4 + string(group) + struct.pack('>II', 0, len(entries)))
        for name, kind, payload in entries:
            slot = 64 + len(groups) + len(block)
            block += struct.pack('>4I', 0, len(payload), kind, 0) + string(name)
            slots.append(slot)
            payloads.append(payload)
        struct.pack_into('>I', block, 0, len(block))
        groups.extend(block)
    index = struct.pack('>5I', 0, len(slots), 0, 0, len(specs)) + groups + struct.pack('>I', 0)
    refs_start = 44 + len(index)
    index += struct.pack('>I', 0)
    end = 44 + len(index)
    header = b'.KZB' + struct.pack('>6I', 0, 44, 74, 0x20005, 0x20153, 1) + bytes(16)
    output = bytearray(header + index + b'\0'*4)
    struct.pack_into('>I', output, 44, end-40)
    struct.pack_into('>I', output, 52, refs_start-52)
    for slot, payload in zip(slots, payloads):
        struct.pack_into('>I', output, slot, len(output))
        output += struct.pack('<I', 2) + payload + b'\x01\x02'
    struct.pack_into('>I', output, 4, len(output))
    return bytes(output)


class ToolboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT/'config', self.root/'config', ignore=shutil.ignore_patterns('settings.json'))
        (self.root/'input').mkdir()
        self.path = self.root/'input'/'DEMO.kzb'
        self.original = fixture()
        self.path.write_bytes(self.original)
        self.ws = Workspace(self.root)

    def assert_input_unchanged(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(sorted(p.name for p in self.ws.input.iterdir()), ['DEMO.kzb'])

    def test_integrity_is_dry_and_crc_corruption_fails(self):
        result = integrity(self.path)
        self.assertEqual(result['objects'], 3)
        self.assertEqual(len(result['warnings']), 1)
        self.assertFalse(self.ws.output.exists())
        records, *_ = parse(self.original)
        r = records[0]
        corrupt = bytearray(self.original)
        corrupt[r['offset']+4+30] ^= 1
        self.path.write_bytes(corrupt)
        with self.assertRaises(ValueError):
            integrity(self.path)
        self.path.write_bytes(self.original)
        self.assert_input_unchanged()

    def test_invalid_deflate_is_reported_without_stopping_menu(self):
        records, *_ = parse(self.original)
        r = records[0]
        image = self.original[r['offset']+4:r['offset']+4+r['size']]
        body = b'not-a-zlib-stream'
        chunk = struct.pack('>I',len(body))+b'IDAT'+body+struct.pack('>I',zlib.crc32(b'IDAT'+body))
        broken = image[:33]+chunk+image[-12:]
        self.path.write_bytes(rebuild(self.original, {'Resource Files/Images/00000_demo.png':broken})[0])
        app=App(self.root,tell=lambda _:None)
        self.assertFalse(app.batch('1'))

    def test_resource_and_all_extraction_no_overwrite(self):
        output, count = extract_kzb(self.ws, self.path, True)
        self.assertEqual(count, 2)
        self.assertTrue((output/'Resource Files').is_dir())
        self.assertFalse((output/'Layers').exists())
        all_output, count = extract_kzb(self.ws, self.path)
        self.assertEqual(count, 3)
        self.assertTrue((all_output/'Layers').is_dir())
        again, _ = extract_kzb(self.ws, self.path)
        self.assertNotEqual(again, all_output)
        self.assert_input_unchanged()

    def test_custom_sparse_roundtrip(self):
        output, _ = extract_kzb(self.ws, self.path, True)
        replacement = png_bytes(2, 2, 4, b'\xff\x00\x00\xff'*4)
        folder = self.root/'custom'/'DEMO'/'Resource Files'/'Images'
        folder.mkdir(parents=True)
        (folder/'00000_demo.png').write_bytes(replacement)
        result, count = build_custom(self.ws, self.path)
        self.assertEqual(count, 1)
        records, *_ = validate(result.read_bytes())
        record = records[0]
        self.assertEqual(result.read_bytes()[record['offset']+4:record['offset']+4+record['size']], replacement)
        self.assertEqual(rebuild(self.original, {})[0], self.original)
        self.assert_input_unchanged()

    def test_optimisation_uses_external_tool_and_retains_pixels(self):
        # Inflate IDAT to make a deterministic smaller lossless candidate possible.
        records, *_ = parse(self.original)
        r = records[0]
        image = self.original[r['offset']+4:r['offset']+4+r['size']]
        raw = zlib.decompress(image[41:-16])
        def chunk(kind, body):
            return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
        large = image[:33]+chunk(b'IDAT', zlib.compress(raw,0))+chunk(b'IEND',b'')
        self.original = rebuild(self.original, {'Resource Files/Images/00000_demo.png':large})[0]
        self.path.write_bytes(self.original)
        tool = self.root/'fake-oxipng'
        tool.write_text('#!/usr/bin/env python3\nimport sys,zlib,struct\nfrom pathlib import Path\np=Path(sys.argv[-1]);b=p.read_bytes();n=int.from_bytes(b[33:37],"big");raw=zlib.decompress(b[41:41+n]);d=zlib.compress(raw,9);k=b"IDAT";c=struct.pack(">I",len(d))+k+d+struct.pack(">I",zlib.crc32(k+d));Path(sys.argv[sys.argv.index("--out")+1]).write_bytes(b[:33]+c+b[45+n:])\n')
        tool.chmod(0o755)
        tools = dict.fromkeys(['oxipng','optipng','pngcrush','jpegtran','jpegoptim'])
        tools['oxipng'] = str(tool)
        with patch('dorothy_lib.workflows.optimizer_tools', return_value=tools):
            result, count, saved = optimise_kzb(self.ws, self.path)
        self.assertEqual(count,1)
        self.assertGreater(saved,0)
        modified = result.read_bytes()
        records, *_ = parse(modified)
        r = records[0]
        new = modified[r['offset']+4:r['offset']+4+r['size']]
        self.assertEqual(png_signature(large), png_signature(new))
        self.assert_input_unchanged()

    def test_missing_optimizer_fails_before_build(self):
        with patch('dorothy_lib.workflows.optimizer_tools', return_value=dict.fromkeys(['oxipng','optipng','pngcrush','jpegtran','jpegoptim'])):
            with self.assertRaisesRegex(ValueError, 'missing_png_optimizer'):
                optimise_kzb(self.ws, self.path)
        self.assertFalse(self.ws.output.exists())

    def test_refresh_menu_languages_and_exit(self):
        strings = json.loads((self.root/'config'/'lang'/'en.json').read_text())
        strings['language_name'] = 'Test language'
        strings['question'] = 'Translated question'
        (self.root/'config'/'lang'/'zz.json').write_text(json.dumps(strings))
        answers = iter(['R','L','2','','1','','C','','Q'])
        lines = []
        app = App(self.root, ask=lambda _: next(answers), tell=lines.append)
        self.assertEqual(app.run(),0)
        self.assertIn('Translated question', lines)
        self.assertEqual(json.loads((self.root/'config'/'settings.json').read_text())['language'], 'zz')
        self.assertEqual(App(self.root).lang.code, 'zz')
        self.assertFalse(self.ws.output.exists())
        self.assert_input_unchanged()

    def test_scp_stays_in_output_and_rejects_shell_input(self):
        with patch('dorothy_lib.workflows.subprocess.run') as run:
            run.return_value.returncode=0
            output, _ = pull(self.ws, '192.168.1.4', 'root', 22, '/mnt/app/hmi')
            command=run.call_args.args[0]
            self.assertIn('-O', command)
            self.assertTrue(output.is_relative_to(self.ws.output))
            self.assertEqual(command[-2], 'root@192.168.1.4:/mnt/app/hmi/*.kzb')
        for host, user, remote in [('a;id','root','/mnt'),('host','root;id','/mnt'),('host','root','/mnt/$(id)')]:
            with self.assertRaises(ValueError):
                scp_command(host,user,22,remote,self.ws.output)
        self.assert_input_unchanged()

    def test_symlink_output_cannot_write_to_input(self):
        self.ws.output.symlink_to(self.ws.input, target_is_directory=True)
        with self.assertRaises(ValueError):
            extract_kzb(self.ws,self.path)
        self.assert_input_unchanged()

    def test_menu_batch_continues_after_invalid_file(self):
        (self.root/'input'/'BROKEN.kzb').write_bytes(b'broken')
        lines=[]
        app=App(self.root,tell=lines.append)
        self.assertFalse(app.batch('1'))
        self.assertTrue(any('OK: DEMO.kzb' in line for line in lines))
        self.assertTrue(any('FAILED: BROKEN.kzb' in line for line in lines))

    def test_cli_from_another_directory(self):
        result=subprocess.run([sys.executable,str(ROOT/'dorothy.py'),'--root',str(self.root),'--task','1'],cwd='/',capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assert_input_unchanged()


if __name__ == '__main__':
    unittest.main()
