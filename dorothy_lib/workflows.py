"""Filesystem workflows: input is read-only; every generated artifact is in output."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from .kzb import detect_variant, extracted_path, identify_file
from .rebuild import rebuild, validate, read_modifications, write_new_file
from .images import png_signature, jpeg_signature, optimise, optimizer_tools
from .atlas import extract as extract_atlas


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.input = self.root/'input'
        self.output = self.root/'output'
        self.custom = self.root/'custom'

    def scan(self, suffix='.kzb'):
        if not self.input.is_dir():
            return []
        return sorted((p for p in self.input.iterdir() if p.is_file() and p.suffix.lower() == suffix), key=lambda p: p.name.casefold())

    def destination(self, category, name, directory=False):
        # Resolve existing ancestors so output/ cannot redirect writes into input/.
        base = self.output/category
        if self.output.is_symlink() or not base.resolve().is_relative_to(self.output.absolute()):
            raise ValueError('Output folder must not redirect outside output/')
        if base.resolve().is_relative_to(self.input.resolve()):
            raise ValueError('Output folder cannot be inside input/')
        base.mkdir(parents=True, exist_ok=True)
        path = base/name
        index = 2
        while path.exists() or path.is_symlink():
            p = Path(name)
            path = base/(f'{name}_{index}' if directory else f'{p.stem}_{index}{p.suffix}')
            index += 1
        if directory:
            path.mkdir()
        return path


def resource(record):
    return record['group'] == 'Resource Files' or record['group'].startswith('Resource Files/')


def integrity(path):
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
    output = workspace.destination(category, path.stem, directory=True)
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


def build_custom(workspace, path):
    folder = workspace.custom/path.stem
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
    output = workspace.destination('custom', path.stem+'_custom.kzb')
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
    output = workspace.destination('optimised', path.stem+'_optimised.kzb')
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


def extract_atlases(workspace, path):
    target = workspace.destination('atlases', path.stem)
    # Atlas extractor owns directory creation.
    return extract_atlas(path, target)
