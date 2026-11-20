"""Processing workflows: input is read-only; generated assets go to output.
Explicit archive/cleanup is implemented separately in session_archive.
"""
import json
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import zlib
import struct

from .kzb import detect_variant, extracted_path, identify_file
from .rebuild import rebuild, validate, read_modifications, write_new_file
from .terminal import size_change
from .images import png_signature, jpeg_signature, optimise, optimizer_tools
from . import atlas, canim
from .atlas import extract as extract_atlas


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.input = self.root/'input'
        self.output = self.root/'output'
        self.custom = self.root/'custom'

    def files(self):
        if not self.input.is_dir():
            return []
        return sorted((p for p in self.input.rglob('*')
                       if (p.is_file() or p.is_symlink())
                       and not any(part.startswith('.') for part in p.relative_to(self.input).parts)),
                      key=lambda p:p.relative_to(self.input).as_posix().casefold())

    def scan(self, suffix=None):
        paths=self.files()
        if suffix is not None:
            return [p for p in paths if p.suffix.lower()==suffix]
        atlas_keys={(p.parent,p.stem.casefold()) for p in paths if p.suffix.lower()=='.atlas'}
        # Each atlas + JSON pair is one job; its JSON is still fully validated.
        return [p for p in paths if not (p.suffix.lower()=='.json' and (p.parent,p.stem.casefold()) in atlas_keys)]

    def counts(self):
        counts = dict.fromkeys(('kzb','canim','atlas','json','other'), 0)
        for path in self.files():
            extension = path.suffix.lower().lstrip('.')
            counts[extension if extension in counts else 'other'] += 1
        return counts

    def asset_key(self, path):
        relative=path.resolve().relative_to(self.input.resolve())
        family={'.kzb':'kzb','.canim':'canim','.atlas':'atlas','.json':'atlas'}.get(path.suffix.lower(),'other')
        parts=list(relative.parts[:-1])
        if parts and parts[0].lower()==family:parts=parts[1:]
        return Path(family,*parts,path.stem)

    def custom_folder(self,path):
        if sum(self.asset_key(p)==self.asset_key(path) for p in self.scan())>1:
            raise ValueError('Ambiguous input paths; remove duplicate root/family copies before custom rebuilding')
        canonical=self.custom/self.asset_key(path)
        if canonical.is_dir():return canonical
        # Compatibility with 0.1's flat custom/<stem>/ convention, only when
        # the input stem is unambiguous across folders and formats.
        legacy=self.custom/path.stem
        matches=[p for p in self.scan() if p.stem.casefold()==path.stem.casefold()]
        if legacy.is_dir() and len(matches)==1:return legacy
        raise ValueError('missing_custom_folder')

    def destination(self, category, name, directory=False):
        name=Path(name)
        if name.is_absolute() or any(p in ('.','..') for p in name.parts):
            raise ValueError('Invalid output name')
        base = self.output/category
        path = base/name
        if self.output.is_symlink() or not path.parent.resolve().is_relative_to(self.output.absolute()):
            raise ValueError('Output folder must not redirect outside output/')
        if path.parent.resolve().is_relative_to(self.input.resolve()):
            raise ValueError('Output folder cannot be inside input/')
        path.parent.mkdir(parents=True,exist_ok=True)
        index=2
        initial=path
        while path.exists() or path.is_symlink():
            path=initial.with_name(f'{initial.name}_{index}' if directory else f'{initial.stem}_{index}{initial.suffix}')
            index+=1
        if directory:path.mkdir()
        return path

    def built_destination(self,category,path,tag):
        directory=self.destination(category,self.asset_key(path),directory=True)
        return directory/(path.stem+'_'+tag+path.suffix.lower())

    def check_source(self,path):
        relative=path.relative_to(self.input)
        candidate=self.input
        if self.input.is_symlink():raise ValueError('Symbolic input paths are not supported')
        for part in relative.parts:
            candidate=candidate/part
            if candidate.is_symlink():raise ValueError('Symbolic input paths are not supported')


def resource(record):
    return record['group'] == 'Resource Files' or record['group'].startswith('Resource Files/')


def integrity_kzb(path):
    data = path.read_bytes()
    records, _, _, _ = validate(data)
    warnings = []
    for record in records:
        if not resource(record):
            continue
        a, size = record['offset']+4, record['size']
        payload = data[a:a+size]
        name = record['group']+'/'+record['name']
        if not payload:
            warnings.append(('empty_resource', name))
            continue
        kind = identify_file(payload, record['name'])
        if kind['file_status'] == 'invalid':
            raise ValueError(name + ': ' + kind['file_warning'])
        suffix = Path(record['name']).suffix.lower()
        if kind['format'] == 'png' or suffix == '.png':
            png_signature(payload)
        elif kind['format'] == 'jpeg' or suffix in ('.jpg', '.jpeg'):
            jpeg_signature(payload)
    variant = detect_variant(data)
    if variant['profile'] == 'unknown_header':
        warnings.append(('unknown_header', path.name))
    return dict(objects=len(records), variant=variant['profile'], warnings=warnings)


def extract_kzb(workspace, path, resources_only=False):
    data = path.read_bytes()
    records, refs, groups, end = validate(data)
    selected = [(i, r) for i, r in enumerate(records) if not resources_only or resource(r)]
    category = 'resources' if resources_only else 'assets'
    output = workspace.destination(category, workspace.asset_key(path), directory=True)
    manifest = dict(source=path.name, variant=detect_variant(data), references=refs, groups=groups, index_end=end, records=[])
    for i, record in selected:
        relative = extracted_path(i, record)
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        a = record['offset']+4
        payload = data[a:a+record['size']]
        target.write_bytes(payload)
        manifest['records'].append(dict(record, extracted_path=relative.as_posix()))
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return output, len(selected)


def build_custom_kzb(workspace, path):
    folder = workspace.custom_folder(path)
    if not folder.is_dir():
        raise ValueError('missing_custom_folder')
    modifications = read_modifications(folder)
    if not modifications:
        raise ValueError('empty_custom_folder')
    original = path.read_bytes()
    records, _, _, _ = validate(original)
    allowed = {extracted_path(i, r).as_posix() for i, r in enumerate(records) if resource(r)}
    if set(modifications) - allowed:
        raise ValueError('Custom assets must match extracted Resource Files paths: ' + ', '.join(sorted(set(modifications)-allowed)))
    # PNG/JPEG replacements get the same structural checks as original textures.
    for name, payload in modifications.items():
        if name.lower().endswith('.png'):
            png_signature(payload)
        elif name.lower().endswith(('.jpg', '.jpeg')):
            jpeg_signature(payload)
        else:
            result = identify_file(payload, name)
            if result['file_status'] == 'invalid':
                raise ValueError(name + ': ' + result['file_warning'])
    result, changes = rebuild(original, modifications)
    output = workspace.built_destination('custom', path, 'custom')
    write_new_file(output, result)
    return output, len(changes)


def optimise_kzb(workspace, path, progress=None):
    data = path.read_bytes()
    records, _, _, _ = validate(data)
    textures = []
    for i, record in enumerate(records):
        if not resource(record):
            continue
        a = record['offset']+4
        payload = data[a:a+record['size']]
        kind = 'png' if payload.startswith(b'\x89PNG\r\n\x1a\n') else 'jpeg' if payload.startswith(b'\xff\xd8\xff') else None
        if kind:
            textures.append((extracted_path(i, record).as_posix(), payload, kind))
    tools = optimizer_tools()
    if any(t[2] == 'png' for t in textures) and not any(tools[k] for k in ('oxipng','optipng','pngcrush')):
        raise ValueError('missing_png_optimizer')
    if any(t[2] == 'jpeg' for t in textures) and not any(tools[k] for k in ('jpegtran','jpegoptim')):
        raise ValueError('missing_jpeg_optimizer')
    output = workspace.built_destination('optimised', path, 'optimised')
    replacements = {}
    # Working textures and tools never see paths under input/.
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.textures-') as temp:
        for index, (name, payload, kind) in enumerate(textures, 1):
            if progress:
                progress(index, len(textures), name)
            candidate = optimise(payload, kind, temp, tools)
            if candidate != payload:
                replacements[name] = candidate
    result, changes = rebuild(data, replacements)
    write_new_file(output, result)
    return output, len(changes), len(data)-len(result)


def scp_command(host, username, port, remote_folder, destination, legacy=True):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('invalid_host')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', username):
        raise ValueError('invalid_username')
    if not (1 <= int(port) <= 65535):
        raise ValueError('invalid_port')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]*', remote_folder):
        raise ValueError('invalid_remote_path')
    args = ['scp']
    if legacy:
        args.append('-O')
    return args + ['-P', str(port), '-o', 'ConnectTimeout=15', f'{username}@{host}:{remote_folder.rstrip("/")}/*.kzb', str(destination)]


def pull(workspace, host, username, port, remote_folder, legacy=True):
    # Validate all user input before allocating a new download folder.
    scp_command(host, username, port, remote_folder, workspace.output, legacy)
    output = workspace.destination('pulled', host, directory=True)
    command = scp_command(host, username, port, remote_folder, output, legacy)
    completed = subprocess.run(command, check=False)  # inherit terminal for SSH authentication
    if completed.returncode:
        raise ValueError('scp_failed')
    files = list(output.glob('*.kzb'))
    return output, len(files)



def integrity(path):
    suffix=path.suffix.lower()
    if suffix=='.kzb':return integrity_kzb(path)
    if suffix=='.canim':
        info,_=canim.inspect(path.read_bytes())
        return dict(objects=info['image_count'],variant='ANIM1 '+','.join(info['image_opcodes']),warnings=[])
    if suffix=='.atlas':
        info,_,_=atlas.load(path)
        return dict(objects=len(info['entries']),variant='vc_tc_bin_v4',warnings=[])
    if suffix=='.json':raise ValueError('missing_atlas_companion')
    raise ValueError('unsupported_input')


def extract_file(workspace,path,resources_only=False):
    suffix=path.suffix.lower()
    if suffix=='.kzb':return extract_kzb(workspace,path,resources_only)
    category='resources' if resources_only else 'assets'
    if suffix=='.atlas':
        target=workspace.destination(category,workspace.asset_key(path))
        return atlas.extract(path,target,resources_only)
    if suffix=='.canim':
        data=path.read_bytes();info,raw=canim.inspect(data)
        target=workspace.destination(category,workspace.asset_key(path),directory=True)
        for record in info['images']:
            pixels=raw[record['offset']:record['offset']+record['size']]
            (target/record['filename']).write_bytes(canim.png_bytes(record['width'],record['height'],record['bpp'],pixels))
        if not resources_only:
            (target/'commands.bin').write_bytes(raw[24:24+info['command_bytes']])
            (target/'header.bin').write_bytes(raw[:24])
            (target/'container.anim').write_bytes(raw)
        manifest=dict(source=path.name,source_sha256=hashlib.sha256(data).hexdigest(),**info)
        (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
        return target,len(info['images'])
    integrity(path)  # descriptive failure for unsupported files/orphan JSON


def custom_modifications(workspace,path,ignored,allow_empty=False):
    folder=workspace.custom_folder(path)
    modifications={}
    for candidate in sorted(folder.rglob('*')):
        if candidate.is_symlink():raise ValueError('Symbolic custom paths are not supported')
        if not candidate.is_file() or candidate.name=='.DS_Store':continue
        name=candidate.relative_to(folder).as_posix()
        if name in ignored:continue
        modifications[name]=candidate.read_bytes()
    if not modifications and not allow_empty:raise ValueError('empty_custom_folder')
    return modifications


def build_custom(workspace,path):
    suffix=path.suffix.lower()
    if suffix=='.kzb':return build_custom_kzb(workspace,path)
    if suffix=='.canim':
        info,_=canim.inspect(path.read_bytes())
        mods=custom_modifications(workspace,path,{'manifest.json','commands.bin','header.bin','container.anim'},allow_empty=info['image_count']==0)
        result,changes=canim.rebuild(path.read_bytes(),mods)
        output=workspace.built_destination('custom',path,'custom')
        write_new_file(output,result)
        return output,len(changes)
    if suffix=='.atlas':
        mods=custom_modifications(workspace,path,{'manifest.json','maxrect.bin','header.bin','mapping.json'})
        if 'atlas.png' in mods:
            # A full export includes a convenience preview. Only unchanged
            # previews may be ignored; modified full sheets are never silent.
            info,pixels,_=atlas.load(path)
            w,h,bpp,decoded=canim.read_png(mods.pop('atlas.png'))
            rgba=atlas.rgba_pixels(w,h,bpp,decoded)
            original_rgba=pixels if info['channels']==4 else bytes(v for i in range(0,len(pixels),2) for v in (pixels[i],pixels[i],pixels[i],pixels[i+1]))
            if (w,h)!=(info['width'],info['height']) or rgba!=original_rgba:
                raise ValueError('Modify individual files under images/, not atlas.png')
        result,changes=atlas.rebuild(path,mods)
        output=workspace.built_destination('custom',path,'custom')
        write_new_file(output,result)
        companion=atlas.companion_json(path)
        if companion is not None:write_new_file(output.with_suffix('.json'),companion.read_bytes())
        return output,len(changes)
    integrity(path)


def optimise_file(workspace,path,progress=None,notice=None):
    suffix=path.suffix.lower()
    if suffix=='.kzb':return optimise_kzb(workspace,path,progress)
    if suffix=='.canim':
        data=path.read_bytes();info,raw=canim.inspect(data)
        if notice:notice('canim_optimisation')
        best=data
        for strategy in (zlib.Z_DEFAULT_STRATEGY,zlib.Z_FILTERED,zlib.Z_RLE,zlib.Z_HUFFMAN_ONLY):
            encoder=zlib.compressobj(9,zlib.DEFLATED,zlib.MAX_WBITS,8,strategy)
            candidate=encoder.compress(raw)+encoder.flush()
            if len(candidate)<len(best):best=candidate
        if canim.inspect(best)[1]!=raw:raise ValueError('CANIM optimisation changed decoded data')
        output=workspace.built_destination('optimised',path,'optimised')
        write_new_file(output,best)
        return output,0,len(data)-len(best)
    if suffix=='.atlas':
        data=path.read_bytes();info,_,_=atlas.load(path)
        tools=optimizer_tools()
        if not any(tools[k] for k in ('oxipng','optipng','pngcrush')):raise ValueError('missing_png_optimizer')
        output=workspace.built_destination('optimised',path,'optimised')
        exported=output.parent/'exported'
        atlas.extract(path,exported,True)
        savings=0
        total_before=0
        with tempfile.TemporaryDirectory(dir=output.parent,prefix='.textures-') as temp:
            for index,record in enumerate(info['entries'],1):
                texture=exported/record['extracted_path'];original=texture.read_bytes()
                total_before+=len(original)
                if progress:progress(index,len(info['entries']),record['extracted_path'])
                candidate=optimise(original,'png',temp,tools)
                if candidate!=original:
                    savings+=len(original)-len(candidate);texture.write_bytes(candidate)
        # Raw atlas format has no compression field: preserve its exact bytes.
        write_new_file(output,data)
        companion=atlas.companion_json(path)
        if companion is not None:write_new_file(output.with_suffix('.json'),companion.read_bytes())
        if notice:
            _,percent=size_change(total_before,total_before-savings)
            notice('atlas_optimisation',saved=savings,before=total_before,after=total_before-savings,percent=f'{percent:.2f}')
        return output,0,0
    integrity(path)


def extract_atlases(workspace,path):
    return extract_file(workspace,path,False)
