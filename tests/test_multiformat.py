"""Synthetic, redistributable multi-format workflow fixtures for Dorothy 0.2."""
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from test_dorothy import fixture as kzb_fixture, ROOT
from dorothy_lib import atlas, canim
from dorothy_lib.ui import App
from dorothy_lib.workflows import Workspace, integrity, build_custom, optimise_file, extract_file


def canim_fixture():
    commands=bytearray();pixels=bytearray()
    for uid,bpp,opcode in ((0,1,0x11),(1,4,0x15)):
        commands.extend(struct.pack('<8I',opcode,uid,3,2,bpp,len(pixels),0xcc,uid+1))
        pixels.extend(bytes([uid*50])*(3*2*bpp))
    commands.extend(struct.pack('<4I',227,233,165,228))
    raw=b'ANIM1   '+struct.pack('<4I',800,480,len(commands),8)+commands+struct.pack('<2I',2,len(pixels))+pixels
    return zlib.compress(raw,0)


def atlas_fixture(channels=4):
    w,h,n=4,3,1
    header=b'TC\0'+struct.pack('<11I',4,4,1,1,w,h,channels,0,2,4,n)+struct.pack('<2I',7,0)
    table=b'BIN\0'+struct.pack('<8I',4,4,w,h,channels,2,4,n)+struct.pack('<I4f2H',7,2/w,1/h,1/w,1/h,2,1)
    pixels=bytes((i*13)%256 for i in range(w*h*channels))
    return header+table+pixels+b'MAXRECT\0'+bytes(68)


class MultiFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        shutil.copytree(ROOT/'config',self.root/'config',ignore=shutil.ignore_patterns('settings.json'))
        self.ws=Workspace(self.root)
        self.kzb=self.root/'input/kzb/DEMO.kzb'
        self.anim=self.root/'input/canim/brand/BOOT.canim'
        self.atlas=self.root/'input/atlas/EARLY.atlas'
        for p in (self.kzb,self.anim,self.atlas):p.parent.mkdir(parents=True,exist_ok=True)
        self.kzb.write_bytes(kzb_fixture());self.anim.write_bytes(canim_fixture());self.atlas.write_bytes(atlas_fixture())
        self.atlas.with_suffix('.JSON').write_text(json.dumps({'images':[{'UID':7,'filename':'icons/demo.png'}]}))
        self.snapshot={p.relative_to(self.ws.input):p.read_bytes() for p in self.ws.files()}

    def assert_input_unchanged(self):
        self.assertEqual({p.relative_to(self.ws.input):p.read_bytes() for p in self.ws.files()},self.snapshot)

    def test_nested_scan_pairs_json_once_and_checks_all(self):
        self.assertEqual(len(self.ws.files()),4)
        self.assertEqual(len(self.ws.scan()),3)
        self.assertEqual(len(self.ws.scan('.canim')),1)
        lines=[];app=App(self.root,tell=lines.append)
        self.assertTrue(app.batch('1'))
        self.assertIn('Completed: 3 succeeded, 0 failed.',lines)
        self.assertFalse(self.ws.output.exists())
        self.assert_input_unchanged()

    def test_resources_vs_full_extraction_for_every_format(self):
        for path in self.ws.scan():
            resources,_=extract_file(self.ws,path,True)
            full,_=extract_file(self.ws,path,False)
            if path.suffix=='.canim':
                self.assertTrue((resources/'img_0.png').exists());self.assertFalse((resources/'commands.bin').exists())
                self.assertEqual((full/'container.anim').read_bytes(),zlib.decompress(path.read_bytes()))
                self.assertTrue((full/'commands.bin').exists())
            if path.suffix=='.atlas':
                self.assertTrue((resources/'images/icons/demo.png').exists());self.assertFalse((resources/'maxrect.bin').exists())
                self.assertTrue((full/'atlas.png').exists());self.assertTrue((full/'mapping.json').exists())
            if path.suffix=='.kzb':
                self.assertFalse((resources/'Layers').exists());self.assertTrue((full/'Layers').exists())
        self.assert_input_unchanged()

    def test_custom_batch_all_formats_and_canim_colour_promotion(self):
        changes={self.kzb:('Resource Files/Images/00000_demo.png',canim.png_bytes(4,3,4,b'\xff\0\0\xff'*12)),
                 self.anim:('img_0.png',canim.png_bytes(3,2,3,b'\xff\0\0'*6)),
                 self.atlas:('images/icons/demo.png',canim.png_bytes(2,1,4,b'\0\xff\0\x80'*2))}
        for path,(relative,png) in changes.items():
            target=self.ws.custom/self.ws.asset_key(path)/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(png)
        lines=[];self.assertTrue(App(self.root,tell=lines.append).batch('5'))
        outputs=list((self.ws.output/'custom').rglob('*_custom.*'))
        for output in outputs:
            if output.suffix=='.canim':
                info,raw=canim.inspect(output.read_bytes());self.assertEqual(info['images'][0]['bpp'],3)
                self.assertEqual(info['images'][1]['opcode'],0x15)
            elif output.suffix=='.atlas':
                info,pixels,_=atlas.load(output)
                self.assertEqual(atlas.crop_pixels(pixels,info['width'],4,info['entries'][0]),b'\0\xff\0\x80'*2)
            elif output.suffix=='.kzb':self.assertEqual(integrity(output)['objects'],3)
        self.assertEqual(len(outputs),4) # KZB + CANIM + atlas and its JSON
        self.assert_input_unchanged()

    def test_optimise_all_formats_explains_raw_atlas_and_preserves_canim_pixels(self):
        tools=dict.fromkeys(['oxipng','optipng','pngcrush','jpegtran','jpegoptim']);tools['oxipng']='fake'
        lines=[]
        with patch('dorothy_lib.workflows.optimizer_tools',return_value=tools),patch('dorothy_lib.workflows.optimise',side_effect=lambda data,*args:data):
            self.assertTrue(App(self.root,tell=lines.append).batch('4'))
        output=next((self.ws.output/'optimised/canim').rglob('*.canim'))
        self.assertEqual(zlib.decompress(output.read_bytes()),zlib.decompress(self.anim.read_bytes()))
        self.assertLess(output.stat().st_size,self.anim.stat().st_size)
        output=next((self.ws.output/'optimised/atlas').rglob('*.atlas'))
        self.assertEqual(output.read_bytes(),self.atlas.read_bytes())
        self.assertTrue((output.parent/'exported/images/icons/demo.png').exists())
        self.assertTrue(any('container unchanged' in line for line in lines))
        self.assert_input_unchanged()

    def test_empty_canim_full_export_can_be_rebuilt(self):
        raw=(b'ANIM1   '+struct.pack('<4I',800,480,16,4)
             +struct.pack('<4I',227,233,165,228)+struct.pack('<2I',0,0))
        self.anim.write_bytes(zlib.compress(raw))
        output,count=extract_file(self.ws,self.anim,False)
        self.assertEqual(count,0)
        mods=self.ws.custom/self.ws.asset_key(self.anim);shutil.copytree(output,mods)
        rebuilt,count=build_custom(self.ws,self.anim)
        self.assertEqual(count,0);self.assertEqual(rebuilt.read_bytes(),self.anim.read_bytes())

    def test_menu_version_archive_a_and_coffee_before_quit(self):
        lines=[];app=App(self.root,ask=lambda _:'q',tell=lines.append)
        self.assertEqual(app.run(),0)
        self.assertIn('Version: v0.3.0',lines)
        self.assertIn('1 - Check integrity of input files',lines)
        self.assertIn('A - Archive & Clean-up',lines)
        self.assertIn('4 input files found (including companion metadata).',lines)
        coffee=next(i for i,line in enumerate(lines) if 'buycoffee.to' in line)
        bye=next(i for i,line in enumerate(lines) if 'yellow brick' in line)
        self.assertLess(coffee,bye)
        self.assert_input_unchanged()

    def test_corrupt_atlas_json_and_orphan_and_unknown_are_reported(self):
        self.atlas.with_suffix('.JSON').write_text('{broken')
        (self.ws.input/'orphan.json').write_text('{}')
        (self.ws.input/'unknown.bin').write_bytes(b'bytes')
        lines=[];self.assertFalse(App(self.root,tell=lines.append).batch('1'))
        self.assertTrue(any('OK: canim/brand/BOOT.canim' in line for line in lines))
        self.assertTrue(any('no matching .atlas' in line for line in lines))
        self.assertTrue(any('Unsupported input file' in line for line in lines))
        self.assertFalse(self.ws.output.exists())

    def test_atlas_noop_and_la_alpha_preserved_and_colour_rejected(self):
        self.atlas.write_bytes(atlas_fixture(2))
        info,pixels,_=atlas.load(self.atlas);r=info['entries'][0]
        cropped=atlas.crop_pixels(pixels,info['width'],2,r)
        png=atlas.png_bytes(2,1,2,cropped)
        same,changes=atlas.rebuild(self.atlas,{r['extracted_path']:png})
        self.assertEqual(same,self.atlas.read_bytes());self.assertEqual(changes,[])
        with self.assertRaisesRegex(ValueError,'RGBA atlas'):
            atlas.rebuild(self.atlas,{r['extracted_path']:canim.png_bytes(2,1,3,b'\xff\0\0'*2)})

    def test_same_names_in_different_brands_get_distinct_keys(self):
        another=self.root/'input/canim/other/BOOT.canim';another.parent.mkdir();another.write_bytes(canim_fixture())
        first,_=extract_file(self.ws,self.anim,True);second,_=extract_file(self.ws,another,True)
        self.assertNotEqual(first,second)
        self.assertEqual(self.ws.asset_key(self.anim),Path('canim/brand/BOOT'))
        self.assertEqual(self.ws.asset_key(another),Path('canim/other/BOOT'))

    def test_shared_json_validation_unsafe_paths_before_output(self):
        self.atlas.with_suffix('.JSON').write_text(json.dumps({'images':[{'UID':7,'filename':'../escape.png'}]}))
        with self.assertRaises(ValueError):integrity(self.atlas)
        self.assertFalse(self.ws.output.exists())


if __name__=='__main__':unittest.main()
