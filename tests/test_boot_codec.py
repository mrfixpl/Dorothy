import csv
import os
import io
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from dorothy_lib.boot import parse,rebuild,decode_rgb,encode_rgb,extract,png_rgb,png_native
from dorothy_lib.png_codec import png_bytes

CORPUS=Path(os.environ['DOROTHY_BOOT_CORPUS']) if os.environ.get('DOROTHY_BOOT_CORPUS') else None


def fixture(padding=True):
    unused=0xffffffff
    commands=[(0,0,*([unused]*6)),(101,4,0,unused,0,0,unused,unused),
              (102,6,100,*([unused]*5)),(103,4,1,unused,0,0,unused,unused),(104,1,*([unused]*6))]
    script=b''.join(struct.pack('<8I',*c) for c in commands)
    images=[(4,2,bytes([128,0,128,0])*4),(2,2,bytes([64,0,255,0])*2)]
    blocks=[struct.pack('<2I',w,h)+zlib.compress(p,0) for w,h,p in images]
    base=20+len(script)+12+8;offsets=[];offset=base
    for block in blocks:offsets.append(offset);offset+=len(block)
    trailer=bytes(4) if padding else b''
    size=4+8+sum(map(len,blocks))+len(trailer)
    return b'SCRT'+struct.pack('<4I',101,1,len(script),42)+script+b'MIFA'+struct.pack('<2I',size,2)+struct.pack('<2I',*offsets)+b''.join(blocks)+trailer


class BootTests(unittest.TestCase):
    def test_noop_and_raw_roundtrip_preserve_every_byte(self):
        for padding in (False,True):
            data=fixture(padding)
            info=parse(data)
            self.assertEqual(rebuild(data,{})[0],data)
            mods={r['raw_filename']:png_bytes(r['width'],r['height'],2,r['pixels']) for r in info['images']}
            self.assertEqual(rebuild(data,mods,True)[0],data)

    def test_preview_noop_does_not_reencode_lossy_colours(self):
        data=fixture();info=parse(data)
        mods={r['filename']:png_bytes(r['width'],r['height'],3,decode_rgb(r['width'],r['height'],r['pixels'])) for r in info['images']}
        self.assertEqual(rebuild(data,mods)[0],data)

    def test_raw_change_relocates_later_images_and_preserves_script_and_tail(self):
        data=fixture();info=parse(data);r=info['images'][0]
        pixels=bytes([128,20,128,200])*4
        result,changes=rebuild(data,{r['raw_filename']:png_bytes(4,2,2,pixels)},True)
        updated=parse(result)
        self.assertEqual(changes,[0]);self.assertEqual(updated['images'][0]['pixels'],pixels)
        self.assertEqual(updated['images'][1]['pixels'],info['images'][1]['pixels'])
        self.assertEqual(updated['images'][1]['offset'],updated['images'][0]['offset']+8+updated['images'][0]['compressed_bytes'])
        self.assertEqual(result[:info['mifa_offset']+4],data[:info['mifa_offset']+4])
        self.assertEqual(updated['trailer_hex'],info['trailer_hex'])
        self.assertEqual(updated['mifa_offset']+8+updated['mifa_size'],len(result))

    def test_colour_import_and_alpha_rejection(self):
        original=fixture();info=parse(original)
        rgb=b'\x00\x00\xff'*8
        result,changes=rebuild(original,{'img_00.png':png_bytes(4,2,3,rgb)})
        self.assertEqual(changes,[0]);new=parse(result)['images'][0]
        self.assertEqual(len(new['pixels']),4*2*2)
        preview=decode_rgb(4,2,new['pixels'])
        self.assertGreater(preview[2],240);self.assertLess(preview[0],5)
        with self.assertRaisesRegex(ValueError,'no ordinary alpha'):
            rebuild(original,{'img_00.png':png_bytes(4,2,4,b'\xff\0\0\x80'*8)})

    def test_greys_encode_exactly_but_colour_quantises(self):
        for value in (0,1,50,128,254,255):
            rgb=bytes([value,value,value])*4
            native=encode_rgb(2,2,rgb)
            self.assertEqual(decode_rgb(2,2,native),rgb)
        varying=bytes(v for value in (0,45,180,255) for v in (value,value,value))
        self.assertEqual(decode_rgb(2,2,encode_rgb(2,2,varying)),varying)
        red=b'\xff\0\0'*4
        decoded=decode_rgb(2,2,encode_rgb(2,2,red))
        self.assertNotEqual(decoded,red)
        self.assertGreater(decoded[0],240)

    def test_optimisation_is_lossless_and_no_larger(self):
        data=fixture();result,_=rebuild(data,{},optimise=True)
        self.assertLessEqual(len(result),len(data))
        before,after=parse(data),parse(result)
        self.assertEqual([r['pixels'] for r in before['images']],[r['pixels'] for r in after['images']])
        self.assertEqual(before['commands'],after['commands'])

    def test_invalid_lengths_offsets_checksums_and_references(self):
        original=fixture();info=parse(original)
        for offset,value in [(12,1),(info['mifa_offset']+4,9999),(info['mifa_offset']+12,0),
                             (20+32+8,999)]:
            data=bytearray(original);struct.pack_into('<I',data,offset,value)
            with self.subTest(offset=offset),self.assertRaises(ValueError):parse(bytes(data))
        data=bytearray(original);r=info['images'][0];data[r['compressed_offset']+r['compressed_bytes']-1]^=1
        with self.assertRaises(ValueError):parse(bytes(data))
        data=bytearray(original);data[-1]=1
        with self.assertRaises(ValueError):parse(bytes(data))
        with self.assertRaises(ValueError):rebuild(original,{'unknown.png':b''})
        with self.assertRaises(ValueError):rebuild(original,{'img_00.png':png_bytes(2,2,3,bytes(12))})

    def test_export_preserves_uint_sentinel_and_separates_carriers_previews(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.boot';source.write_bytes(fixture());out=Path(temp)/'out'
            extract(source,out)
            self.assertTrue((out/'raw/img_00.mib').read_bytes().startswith(b'\x89PNG'))
            self.assertTrue((out/'images/img_00.png').exists())
            rows=list(csv.reader((out/'animation_script.csv').read_text().splitlines(),delimiter=';'))
            self.assertIn('4294967295',rows[1])
            self.assertEqual(rows[3][-1],'WAIT 1.00 seconds (Toolbox interpretation)')

    @unittest.skipUnless(CORPUS is not None and CORPUS.is_dir(),'Set DOROTHY_BOOT_CORPUS for real-file tests')
    def test_all_original_files_and_756_native_roundtrips(self):
        count=images=0
        for path in sorted(CORPUS.rglob('*.boot')):
            with self.subTest(file=path.name):
                original=path.read_bytes();info=parse(original)
                self.assertEqual(rebuild(original,{})[0],original)
                mods={r['raw_filename']:png_bytes(r['width'],r['height'],2,r['pixels']) for r in info['images']}
                self.assertEqual(rebuild(original,mods,True)[0],original)
                count+=1;images+=info['image_count']
        self.assertGreater(count,0);self.assertGreater(images,0)


if __name__=='__main__':unittest.main()
