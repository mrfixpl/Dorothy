"""BOOT integration with Dorothy's unified menu and archive lifecycle."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_dorothy import ROOT
from test_boot_codec import fixture
from dorothy_lib import boot
from dorothy_lib.png_codec import png_bytes,read_png
from dorothy_lib.ui import App
from dorothy_lib.workflows import Workspace,integrity,extract_file,build_custom,optimise_file
from dorothy_lib.session_archive import archive_session


class BootWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        shutil.copytree(ROOT/'config',self.root/'config',ignore=shutil.ignore_patterns('settings.json'))
        self.ws=Workspace(self.root)
        self.source=self.ws.input/'boot/MST2/startup_1.BOOT';self.source.parent.mkdir(parents=True)
        self.original=fixture();self.source.write_bytes(self.original)
        self.key=self.ws.asset_key(self.source)
        self.assertEqual(self.key,Path('boot/MST2/startup_1'))

    def assert_unchanged(self):
        self.assertEqual(self.source.read_bytes(),self.original)
        self.assertEqual(len(self.ws.files()),1)

    def mods(self):
        path=self.ws.custom/self.key;path.mkdir(parents=True,exist_ok=True);return path

    def test_counter_and_integrity_dispatch_in_read_only_batch(self):
        self.assertEqual(self.ws.counts()['boot'],1);self.assertEqual(self.ws.counts()['other'],0)
        lines=[];app=App(self.root,tell=lines.append);app.show()
        self.assertIn('Version: v0.3.0',lines)
        self.assertTrue(any('BOOT: 1' in line for line in lines))
        self.assertTrue(app.batch('1'))
        self.assertFalse(self.ws.output.exists());self.assert_unchanged()

    def test_resources_are_ordinary_editable_rgb_not_channel_carriers(self):
        output,count=extract_file(self.ws,self.source,True)
        self.assertEqual(count,2)
        files={p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
        self.assertEqual(files,{'manifest.json','images/img_00.png','images/img_01.png'})
        for r in boot.parse(self.original)['images']:
            w,h,bpp,rgb=read_png((output/'images'/r['filename']).read_bytes())
            self.assertEqual(bpp,3)
            self.assertEqual(rgb,boot.decode_rgb(w,h,r['pixels']))
        self.assert_unchanged()

    def test_all_assets_partition_reconstructs_exact_original_and_roundtrips(self):
        output,count=extract_file(self.ws,self.source,False)
        self.assertEqual(count,2)
        header=(output/'header.bin').read_bytes();commands=(output/'commands.bin').read_bytes()
        mifa=(output/'mifa_header.bin').read_bytes();offsets=(output/'offsets.bin').read_bytes()
        records=b''.join(p.read_bytes() for p in sorted((output/'records').glob('*.bin')))
        self.assertEqual(header+commands+mifa+offsets+records+(output/'trailer.bin').read_bytes(),self.original)
        shutil.copytree(output,self.ws.custom/self.key,dirs_exist_ok=True)
        result,changed=build_custom(self.ws,self.source)
        self.assertEqual(changed,0);self.assertEqual(result.read_bytes(),self.original);self.assert_unchanged()

    def test_rgb_edit_wins_over_unchanged_native_export(self):
        output,_=extract_file(self.ws,self.source,False)
        shutil.copytree(output,self.ws.custom/self.key,dirs_exist_ok=True)
        folder=self.ws.custom/self.key
        (folder/'images/img_00.png').write_bytes(png_bytes(4,2,3,b'\0\0\xff'*8))
        result,changed=build_custom(self.ws,self.source)
        self.assertEqual(changed,1)
        original,updated=boot.parse(self.original),boot.parse(result.read_bytes())
        self.assertEqual(original['commands'],updated['commands'])
        rgb=boot.decode_rgb(4,2,updated['images'][0]['pixels']);self.assertGreater(rgb[2],240)
        self.assertEqual(original['images'][1]['pixels'],updated['images'][1]['pixels'])
        self.assert_unchanged()

    def test_native_edit_wins_over_unchanged_rgb_export(self):
        output,_=extract_file(self.ws,self.source,False);shutil.copytree(output,self.ws.custom/self.key,dirs_exist_ok=True)
        native=bytes([128,100,128,150])*4
        (self.ws.custom/self.key/'raw/img_00.mib').write_bytes(png_bytes(4,2,2,native))
        result,changed=build_custom(self.ws,self.source)
        self.assertEqual(changed,1);self.assertEqual(boot.parse(result.read_bytes())['images'][0]['pixels'],native)
        self.assert_unchanged()

    def test_conflicting_rgb_and_native_edits_fail_without_output(self):
        folder=self.mods();(folder/'images').mkdir();(folder/'raw').mkdir()
        (folder/'images/img_00.png').write_bytes(png_bytes(4,2,3,b'\0\0\xff'*8))
        (folder/'raw/img_00.mib').write_bytes(png_bytes(4,2,2,bytes([128,100,128,150])*4))
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            build_custom(self.ws,self.source)
        self.assertFalse(self.ws.output.exists());self.assert_unchanged()

    def test_changed_records_and_unknown_paths_and_wrong_dimensions_rejected(self):
        before=boot.parse(self.original);r=before['images'][0]
        for mods in ({'records/img_00.bin':b'edited'}, {'random.png':b'unknown'},
                     {'images/img_00.png':png_bytes(2,2,3,bytes(12))}):
            with self.subTest(keys=list(mods)),self.assertRaises(ValueError):boot.rebuild_assets(self.original,mods)
        self.assert_unchanged()

    def test_optimize_needs_no_external_image_tools_and_keeps_native_data(self):
        lines=[];app=App(self.root,tell=lines.append)
        with patch('dorothy_lib.workflows.optimizer_tools',side_effect=AssertionError('BOOT needs no external tools')):
            self.assertTrue(app.batch('4'))
        result=next((self.ws.output/'optimised').rglob('*.boot'))
        self.assertLessEqual(result.stat().st_size,len(self.original))
        self.assertEqual([r['pixels'] for r in boot.parse(result.read_bytes())['images']],
                         [r['pixels'] for r in boot.parse(self.original)['images']])
        self.assertTrue(any('%' in line for line in lines));self.assert_unchanged()

    def test_mixed_batches_include_boot_and_continue_after_corruption(self):
        bad=self.ws.input/'boot/broken.boot';bad.write_bytes(b'bad')
        lines=[];self.assertFalse(App(self.root,tell=lines.append).batch('1'))
        self.assertTrue(any('OK: boot/MST2/startup_1.BOOT' in line for line in lines))
        self.assertTrue(any('FAILED: boot/broken.boot' in line for line in lines))
        self.assertFalse(self.ws.output.exists())

    def test_archives_boot_assets_and_restores_input_boot_skeleton(self):
        exported,_=extract_file(self.ws,self.source,False)
        shutil.copytree(exported,self.ws.custom/self.key,dirs_exist_ok=True)
        result,_=build_custom(self.ws,self.source)
        archive=archive_session(self.ws,datetime(2026,11,20,12,34,56))
        with zipfile.ZipFile(archive['path']) as zipped:
            self.assertEqual(zipped.read('input/boot/MST2/startup_1.BOOT'),self.original)
            self.assertIn('custom/boot/MST2/startup_1/raw/img_00.mib',zipped.namelist())
            self.assertIn('output/custom/boot/MST2/startup_1/startup_1_custom.boot',zipped.namelist())
        self.assertTrue((self.ws.input/'boot').is_dir());self.assertEqual(self.ws.scan(),[])

    def test_empty_boot_and_alias_duplicates(self):
        unused=0xffffffff
        commands=struct.pack('<8I',0,0,*([unused]*6))+struct.pack('<8I',1,1,*([unused]*6))
        data=b'SCRT'+struct.pack('<4I',101,1,len(commands),0)+commands+b'MIFA'+struct.pack('<2I',4,0)
        self.source.write_bytes(data)
        output,count=extract_file(self.ws,self.source,False);self.assertEqual(count,0)
        shutil.copytree(output,self.ws.custom/self.key,dirs_exist_ok=True)
        result,count=build_custom(self.ws,self.source);self.assertEqual(count,0);self.assertEqual(result.read_bytes(),data)
        png=png_bytes(4,2,3,bytes(24))
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            boot.rebuild_assets(self.original,{'img_00.png':png,'images/img_00.png':png})


if __name__=='__main__':unittest.main()
