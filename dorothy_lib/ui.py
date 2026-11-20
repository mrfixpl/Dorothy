"""Terminal menu; translatable labels live in config/lang/*.json."""
import argparse
import json
from pathlib import Path
import subprocess
import struct
import zipfile
import sys

from .session_archive import archive_session, CleanupIncomplete
from .terminal import Palette, size_change
from .workflows import Workspace, integrity, extract_kzb, build_custom, optimise_file, pull, extract_file


class Languages:
    def __init__(self, folder, code='en'):
        self.folder = Path(folder)
        self.english = json.loads((self.folder/'en.json').read_text(encoding='utf-8'))
        self.code = 'en'
        self.strings = self.english.copy()
        if code != 'en':
            self.load(code)

    def available(self):
        choices = []
        for path in sorted(self.folder.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('language_name'), str):
                    raise ValueError('Invalid language')
                for key, value in data.items():
                    if key in self.english and type(value) is not type(self.english[key]):
                        raise ValueError('Invalid value type')
                # Validate translated placeholders against English to catch typos.
                from string import Formatter
                for key, value in data.items():
                    if key in self.english and isinstance(value, str):
                        fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name is not None}
                        if fields(value) != fields(self.english[key]):
                            raise ValueError('Translation placeholder mismatch')
                choices.append((path.stem, data))
            except (OSError, ValueError):
                print(self.text('language_bad_file', file=path.name))
        return choices

    def load(self, code):
        for found, data in self.available():
            if found == code:
                self.code = code
                self.strings = dict(self.english, **data)
                return
        raise ValueError(self.text('language_invalid'))

    def text(self, key, **values):
        value = self.strings.get(key, key)
        return value.format(**values) if isinstance(value, str) else value


class App:
    def __init__(self, root, language=None, ask=input, tell=print, color=None):
        self.workspace = Workspace(root)
        self.ask, self.tell = ask, tell
        self.palette = Palette(color if color is not None else None if tell is print else False)
        self.settings = self.workspace.root/'config'/'settings.json'
        if language is None:
            try:
                language = json.loads(self.settings.read_text())['language']
            except (OSError, ValueError, KeyError, TypeError):
                language = 'en'
        try:
            self.lang = Languages(self.workspace.root/'config'/'lang', language)
        except ValueError:
            self.lang = Languages(self.workspace.root/'config'/'lang')

    def t(self, key, **values):
        return self.lang.text(key, **values)

    def say(self, key, tone=None, **values):
        # Numeric placeholders are independently coloured; translations contain
        # no escape sequences and continue to use ordinary named placeholders.
        for name,value in values.items():
            if isinstance(value,(int,float)) or name=='percent':
                field_tone='error' if name=='failed' and value else 'success' if name in ('ok','failed') else 'info'
                values[name]=self.palette.paint(value,field_tone)
        text=self.t(key,**values)
        self.tell(self.palette.paint(text,tone) if tone else text)

    def archive(self):
        self.say('archive_note','warning')
        try:
            result=archive_session(self.workspace)
            self.say('archive_complete','success',path=result['path'],files=result['files'],removed=result['removed'])
            return True
        except CleanupIncomplete as exc:
            self.say('archive_incomplete','warning',path=exc.archive,reason=str(exc))
            return False
        except (OSError,ValueError,zipfile.BadZipFile,RuntimeError) as exc:
            self.say('archive_failed','error',reason=str(exc))
            return False

    def show(self):
        self.tell((self.workspace.root/'config'/'banner.txt').read_text())
        info = json.loads((self.workspace.root/'config'/'about.json').read_text(encoding='utf-8'))
        self.say('about_version','info',version='v'+info['version'].lstrip('v'))
        self.tell(self.t('description'))
        self.tell('')
        self.say('file_count',count=len(self.workspace.files()))
        self.say('input_types',**self.workspace.counts())
        self.say('input_jobs',count=len(self.workspace.scan()))
        self.tell('')
        self.tell(self.t('question'))
        self.tell('')
        for line in self.t('menu'):
            self.tell(line)
        self.tell('')

    def batch(self, action):
        paths = self.workspace.scan()
        if not paths:
            self.say('no_files','warning')
            return False
        if action in ('1', '4', '5'):
            self.tell(self.t({'1':'integrity_note', '4':'optimise_note', '5':'custom_note'}[action]))
        succeeded = failed = 0
        for path in paths:
            display=path.relative_to(self.workspace.input).as_posix()
            self.say('processing','progress',file=display)
            try:
                self.workspace.check_source(path)
                if action == '1':
                    result = integrity(path)
                    self.say('integrity_ok','success',file=display,**{k:result[k] for k in ('objects','variant')})
                    for code, name in result['warnings']:
                        self.say(code,'warning',name=name)
                elif action in ('2', '3'):
                    output, count = extract_file(self.workspace, path, action == '2')
                    self.say('extracted','success',count=count,path=output)
                elif action == '4':
                    progress = lambda index,total,name: self.say('texture_progress','progress',index=index,total=total,name=name)
                    notice = lambda key,**values: self.say(key,'info',**values)
                    before = path.stat().st_size
                    output, count, saved = optimise_file(self.workspace, path, progress, notice)
                    after = output.stat().st_size
                    saved,percent=size_change(before,after)
                    self.say('optimised','success' if saved>0 else 'warning' if saved<0 else 'info',path=output,count=count,saved=saved,before=before,after=after,percent=f'{percent:.2f}')
                elif action == '5':
                    output, count = build_custom(self.workspace, path)
                    self.say('built','success',path=output,count=count)
                succeeded += 1
            except (OSError, ValueError, IndexError, RecursionError, subprocess.SubprocessError, struct.error) as exc:
                reason = str(exc) or type(exc).__name__
                self.say('failed','error',file=display,reason=self.t(reason) if reason in self.lang.strings else reason)
                failed += 1
        self.say('completed',ok=succeeded,failed=failed)
        return failed == 0

    def change_language(self):
        choices = self.lang.available()
        self.tell(self.t('languages'))
        for i, (_, data) in enumerate(choices, 1):
            self.tell(f"{i} - {data['language_name']}")
        choice = self.ask(self.t('language_select')).strip()
        if not choice:
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(choices):
            self.say('language_invalid','warning')
            return
        code, data = choices[int(choice)-1]
        self.lang.load(code)
        try:
            self.settings.write_text(json.dumps({'language': code}, indent=2)+'\n', encoding='utf-8')
        except OSError as exc:
            self.say('settings_error','error',reason=str(exc))
        self.say('language_changed','success',name=data['language_name'])

    def pull(self):
        self.tell(self.t('pull_note'))
        host = self.ask(self.t('host')).strip()
        if not host:
            return
        user = self.ask(self.t('username')).strip() or 'root'
        port = self.ask(self.t('port')).strip() or '22'
        folder = self.ask(self.t('remote_folder')).strip()
        if not folder:
            return
        legacy = self.ask(self.t('legacy')).strip().lower() not in ('n', 'no')
        if not port.isdigit():
            raise ValueError(self.t('invalid_port'))
        output, count = pull(self.workspace, host, user, int(port), folder, legacy)
        self.say('pulled','success',count=count,path=output)

    def about(self):
        info = json.loads((self.workspace.root/'config'/'about.json').read_text(encoding='utf-8'))
        self.tell(self.t('about_title'))
        self.tell(self.t('about_version', version=info['version']))
        self.tell(self.t('about_author', author=info['author']))
        self.tell(self.t('description'))
        self.tell(self.t('about_libraries'))
        self.tell(self.t('about_repo', url=info['repository']))
        self.tell(self.t('about_coffee', url=info['coffee_url']))
        self.tell(self.t('about_notice'))

    def run(self):
        while True:
            self.show()
            try:
                option = self.ask(self.t('select')).strip().upper()
                if option == 'Q':
                    info = json.loads((self.workspace.root/'config'/'about.json').read_text(encoding='utf-8'))
                    self.tell(self.t('about_coffee', url=info['coffee_url']))
                    self.tell(self.t('bye'))
                    return 0
                if option == 'R':
                    continue
                if option in ('1','2','3','4','5'):
                    self.batch(option)
                elif option == 'A':
                    self.archive()
                elif option == 'L':
                    self.change_language()
                elif option == 'C':
                    self.about()
                elif option == 'P':
                    self.pull()
                else:
                    self.say('invalid_option','warning')
                self.ask(self.t('continue'))
            except EOFError:
                return 0
            except KeyboardInterrupt:
                self.say('cancelled','warning')
            except (OSError, ValueError) as exc:
                reason = str(exc)
                self.say('failed','error',file='Dorothy',reason=self.t(reason) if reason in self.lang.strings else reason)


def main(root):
    # Bootstrap English labels from the program, independent of the current cwd.
    english = Languages(Path(root)/'config'/'lang')
    parser = argparse.ArgumentParser(description=english.text('cli_description'))
    parser.add_argument('--root', type=Path, default=Path(root), help=english.text('cli_root'))
    parser.add_argument('--task', choices=['1','2','3','4','5','A'], help=english.text('cli_task'))
    parser.add_argument('--language', help=english.text('cli_language'))
    args = parser.parse_args()
    app = App(args.root, args.language)
    try:
        return (0 if (app.archive() if args.task=='A' else app.batch(args.task)) else 1) if args.task else app.run()
    except KeyboardInterrupt:
        print(app.t('cancelled'))
        return 130
