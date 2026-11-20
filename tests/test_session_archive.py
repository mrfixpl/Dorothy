"""Archive tests operate only on disposable temporary workspaces."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_dorothy import ROOT
from dorothy_lib import session_archive as archives
from dorothy_lib.ui import App
from dorothy_lib.workflows import Workspace
from dorothy_lib.terminal import Palette, size_change, supports_color

STAMP=datetime(2026,11,20,12,34,56)
ANSI=re.compile(r'\x1b\[[0-9;]*m')


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.ws=Workspace(self.root)
        shutil.copytree(ROOT/'config',self.root/'config',ignore=shutil.ignore_patterns('settings.json'))
        self.original={
            'input/kzb/source.kzb':b'original data',
            'input/canim/.hidden':b'hidden data',
            'output/subfolder/file.bin':bytes(range(256))*100,
            'custom/Unicode_zażółć.png':b'custom image data',
            'input/atlas/.gitkeep':b'',
            'output/.gitkeep':b''}
        for name,data in self.original.items():
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        (self.root/'custom/empty_folder').mkdir()

    def assert_originals_untouched(self):
        for name,data in self.original.items():self.assertEqual((self.root/name).read_bytes(),data)

    def test_verified_zip_contains_every_file_then_cleans_and_restores_layout(self):
        result=archives.archive_session(self.ws,STAMP)
        self.assertEqual(result['path'].name,'Dorothy_Archive_2026-11-20_12-34-56.zip')
        self.assertEqual(result['files'],len(self.original));self.assertEqual(result['removed'],4)
        with zipfile.ZipFile(result['path']) as backup:
            self.assertIsNone(backup.testzip())
            for name,data in self.original.items():self.assertEqual(backup.read(name),data)
            self.assertIn('custom/empty_folder/',backup.namelist())
            self.assertFalse(any(name.startswith('archives/') for name in backup.namelist()))
        for name in ('input/kzb','input/canim','input/atlas','output','custom'):
            self.assertTrue((self.root/name).is_dir())
        for name in self.original:
            if not name.endswith('.gitkeep'):self.assertFalse((self.root/name).exists())
        self.assertFalse(list((self.root/'archives').glob('*.tmp')))

    def test_verification_failure_keeps_all_sources(self):
        with patch.object(archives,'verify_archive',side_effect=ValueError('bad archive')):
            with self.assertRaisesRegex(ValueError,'bad archive'):
                archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched()
        self.assertFalse(list((self.root/'archives').glob('*.zip')))

    def test_write_failure_keeps_all_sources(self):
        with patch.object(archives,'write_archive',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched()

    def test_corrupt_zip_is_detected_before_cleanup(self):
        original=archives.write_archive
        def corrupt(path,*args):
            original(path,*args)
            path.write_bytes(b'not a ZIP')
        with patch.object(archives,'write_archive',side_effect=corrupt):
            with self.assertRaises(zipfile.BadZipFile):archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched()

    def test_corrupt_compressed_member_is_detected_before_cleanup(self):
        import struct
        original=archives.write_archive
        def corrupt(path,*args):
            original(path,*args)
            with zipfile.ZipFile(path) as backup:
                info=backup.getinfo('output/subfolder/file.bin')
            data=bytearray(path.read_bytes());offset=info.header_offset
            name_len,extra_len=struct.unpack_from('<HH',data,offset+26)
            start=offset+30+name_len+extra_len
            data[start+info.compress_size//2]^=0x80
            path.write_bytes(data)
        with patch.object(archives,'write_archive',side_effect=corrupt):
            with self.assertRaises((ValueError,zipfile.BadZipFile)):
                archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched()

    def test_source_change_during_verification_cancels_all_cleanup(self):
        original=archives.verify_archive
        path=self.root/'input/kzb/source.kzb'
        def verify_then_change(*args):
            original(*args)
            path.write_bytes(b'changed concurrently')
        with patch.object(archives,'verify_archive',side_effect=verify_then_change):
            with self.assertRaisesRegex(ValueError,'changed'):
                archives.archive_session(self.ws,STAMP)
        self.assertEqual(path.read_bytes(),b'changed concurrently')
        self.assertEqual((self.root/'output/subfolder/file.bin').read_bytes(),self.original['output/subfolder/file.bin'])
        self.assertFalse(list((self.root/'archives').glob('*.zip')))

    def test_new_file_during_verification_cancels_cleanup(self):
        original=archives.verify_archive
        def add_file(*args):
            original(*args);(self.ws.input/'new.txt').write_text('new data')
        with patch.object(archives,'verify_archive',side_effect=add_file):
            with self.assertRaisesRegex(ValueError,'changed'):archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched();self.assertTrue((self.ws.input/'new.txt').exists())

    def test_cleanup_failure_retains_verified_archive_and_remaining_files(self):
        with patch.object(archives,'cleanup',side_effect=OSError('permission denied')):
            with self.assertRaises(archives.CleanupIncomplete) as caught:
                archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched()
        with zipfile.ZipFile(caught.exception.archive) as backup:
            self.assertEqual(backup.read('input/kzb/source.kzb'),b'original data')

    def test_partial_cleanup_failure_keeps_backup_of_already_removed_files(self):
        original_unlink=Path.unlink
        blocked=self.root/'input/kzb/source.kzb'
        def remove(path,*args,**kwargs):
            if path==blocked:raise PermissionError('simulated cleanup failure')
            return original_unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',remove):
            with self.assertRaises(archives.CleanupIncomplete) as caught:
                archives.archive_session(self.ws,STAMP)
        removed=self.root/'custom/Unicode_zażółć.png'
        self.assertFalse(removed.exists());self.assertTrue(blocked.exists())
        with zipfile.ZipFile(caught.exception.archive) as backup:
            self.assertEqual(backup.read('custom/Unicode_zażółć.png'),self.original['custom/Unicode_zażółć.png'])

    def test_interrupt_during_cleanup_reports_the_retained_archive(self):
        with patch.object(archives,'cleanup',side_effect=KeyboardInterrupt):
            with self.assertRaises(archives.CleanupIncomplete) as caught:
                archives.archive_session(self.ws,STAMP)
        self.assertTrue(caught.exception.archive.is_file());self.assert_originals_untouched()

    def test_new_files_after_publish_are_never_deleted(self):
        original=archives.cleanup
        def create_then_cleanup(*args):
            (self.ws.input/'new.txt').write_bytes(b'unarchived')
            return original(*args)
        with patch.object(archives,'cleanup',side_effect=create_then_cleanup):
            with self.assertRaises(archives.CleanupIncomplete):archives.archive_session(self.ws,STAMP)
        self.assertEqual((self.ws.input/'new.txt').read_bytes(),b'unarchived')
        self.assertEqual(len(list((self.root/'archives').glob('*.zip'))),1)

    def test_timestamp_collision_never_overwrites_existing_archive(self):
        (self.root/'archives').mkdir()
        path=self.root/'archives/Dorothy_Archive_2026-11-20_12-34-56.zip';path.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):archives.archive_session(self.ws,STAMP)
        self.assertEqual(path.read_bytes(),b'existing');self.assert_originals_untouched()

    def test_symbolic_sources_and_archive_folder_are_rejected(self):
        target=self.root/'outside';target.write_bytes(b'untouched')
        link=self.ws.input/'link'
        link.symlink_to(target)
        with self.assertRaises(ValueError):archives.archive_session(self.ws,STAMP)
        self.assertEqual(target.read_bytes(),b'untouched');self.assert_originals_untouched()
        link.unlink();(self.root/'archives').symlink_to(self.ws.input,target_is_directory=True)
        with self.assertRaises(ValueError):archives.archive_session(self.ws,STAMP)
        self.assert_originals_untouched()

    def test_archive_menu_returns_to_empty_session(self):
        answers=iter(['A','','Q']);lines=[]
        self.assertEqual(App(self.root,ask=lambda _:next(answers),tell=lines.append).run(),0)
        self.assertTrue(any('Verified archive:' in line for line in lines))
        self.assertIn('0 input files found (including companion metadata).',lines)
        self.assertTrue(any('KZB: 0 | CANIM: 0' in line for line in lines))
        self.assertEqual(len(list((self.root/'archives').glob('*.zip'))),1)

    def test_empty_session_archive_creates_a_usable_skeleton(self):
        with tempfile.TemporaryDirectory() as temp:
            ws=Workspace(temp)
            result=archives.archive_session(ws,STAMP)
            self.assertEqual(result['files'],0)
            for name in ('input/kzb','input/canim','input/atlas','output','custom'):
                self.assertTrue((ws.root/name).is_dir())
            with zipfile.ZipFile(result['path']) as backup:self.assertEqual(set(backup.namelist()),{'input/','output/','custom/'})


class PresentationTests(unittest.TestCase):
    def test_percent_uses_original_file_size(self):
        self.assertEqual(size_change(1000,750),(250,25.0))
        self.assertEqual(size_change(100,100),(0,0.0))
        self.assertEqual(size_change(0,0),(0,0.0))
        self.assertEqual(size_change(100,120),(-20,-20.0))

    def test_ansi_is_optional_and_no_color_is_respected(self):
        self.assertEqual(Palette(False).paint('OK','success'),'OK')
        self.assertEqual(Palette(True).paint('OK','success'),'\x1b[32mOK\x1b[0m')
        class TTY:
            def isatty(self):return True
        with patch.dict(os.environ,{'NO_COLOR':''}):self.assertFalse(supports_color(TTY()))
        with patch.dict(os.environ,{'TERM':'dumb'}):self.assertFalse(supports_color(TTY()))

    def test_optimisation_percentage_matches_the_actual_output_size(self):
        from test_multiformat import canim_fixture
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();shutil.copytree(ROOT/'config',root/'config')
            source=root/'input/canim/boot.canim';source.parent.mkdir(parents=True);source.write_bytes(canim_fixture())
            lines=[];app=App(root,tell=lines.append)
            self.assertTrue(app.batch('4'))
            output=next((root/'output').rglob('*.canim'))
            before,after=source.stat().st_size,output.stat().st_size
            expected=(before-after)/before*100
            self.assertTrue(any(f'{before} -> {after} bytes' in line and f'({expected:.2f}%)' in line for line in lines))

    def test_per_type_counts_and_coloured_statuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();shutil.copytree(ROOT/'config',root/'config')
            source=root/'input';source.mkdir()
            for name in ('one.KZB','two.canim','three.atlas','three.JSON','other.bin'):(source/name).touch()
            lines=[];app=App(root,tell=lines.append,color=True);app.show()
            plain=[ANSI.sub('',line) for line in lines]
            self.assertIn('KZB: 1 | CANIM: 1 | ATLAS: 1 | JSON: 1 | Other: 1',plain)
            self.assertTrue(any('\x1b[36m1\x1b[0m' in line for line in lines))
            self.assertFalse(app.batch('1'))
            self.assertTrue(any('\x1b[31mFAILED:' in line for line in lines))


if __name__=='__main__':unittest.main()
