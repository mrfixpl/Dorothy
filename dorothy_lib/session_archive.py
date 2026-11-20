"""Verified ZIP session backups followed by cleanup of exactly archived files.

No recursive deletion is used. If archiving/verification fails, source files
are untouched. Cleanup can fail partway (permissions/concurrent edits); in that
case the verified archive is retained and remaining source files are left alone.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import zipfile
import zlib

ROOTS = ('input', 'output', 'custom')
CHUNK = 1024 * 1024


class CleanupIncomplete(ValueError):
    def __init__(self, archive, reason):
        self.archive = archive
        super().__init__(str(reason))


@dataclass
class Entry:
    path: Path
    signature: tuple
    sha256: str = ''


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def inventory(workspace):
    files, directories = {}, {}
    def visit(path):
        relative = path.relative_to(workspace.root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Archive does not follow symbolic links: '+relative)
        if stat.S_ISDIR(info.st_mode):
            directories[relative] = (info.st_dev, info.st_ino)
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            files[relative] = Entry(path, signature(info))
        else:
            raise ValueError('Archive requires regular files/directories: '+relative)
    for name in ROOTS:
        path = workspace.root/name
        if path.exists() or path.is_symlink():
            visit(path)
        else:
            directories[name] = None
    return files, directories


def guard(workspace, entry):
    relative = entry.path.relative_to(workspace.root)
    if relative.parts[0] not in ROOTS:
        raise ValueError('Archive source outside session folders')
    current = workspace.root
    for part in relative.parts:
        current = current/part
        if current.is_symlink():
            raise ValueError('Source became a symbolic link: '+relative.as_posix())
    if signature(entry.path.lstat()) != entry.signature:
        raise ValueError('Source changed during archiving: '+relative.as_posix())


def stream_hash(stream, destination=None):
    digest = hashlib.sha256()
    while True:
        data = stream.read(CHUNK)
        if not data:
            break
        digest.update(data)
        if destination is not None:
            destination.write(data)
    return digest.hexdigest()


def write_archive(path, workspace, files, directories):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True, strict_timestamps=False) as archive:
        for name in sorted(directories):
            archive.writestr(name+'/', b'')
        for name, entry in sorted(files.items()):
            guard(workspace, entry)
            info = zipfile.ZipInfo.from_file(entry.path, arcname=name, strict_timestamps=False)
            info.compress_type = zipfile.ZIP_DEFLATED
            with entry.path.open('rb') as source:
                if signature(os.fstat(source.fileno())) != entry.signature:
                    raise ValueError('Source changed before read: '+name)
                with archive.open(info, 'w', force_zip64=True) as target:
                    entry.sha256 = stream_hash(source, target)
                if signature(os.fstat(source.fileno())) != entry.signature:
                    raise ValueError('Source changed during read: '+name)
            guard(workspace, entry)
    with path.open('r+b') as stream:
        os.fsync(stream.fileno())


def verify_archive(path, files, directories):
    expected = set(files) | {name+'/' for name in directories}
    with zipfile.ZipFile(path, 'r') as archive:
        names = archive.namelist()
        if len(names) != len(expected) or set(names) != expected:
            raise ValueError('Archive entry list mismatch')
        for name, entry in files.items():
            if archive.getinfo(name).file_size != entry.signature[3]:
                raise ValueError('Archive file-size mismatch: '+name)
            with archive.open(name) as stream:
                digest = stream_hash(stream)  # also checks ZIP CRC on full read
            if digest != entry.sha256:
                raise ValueError('Archive SHA-256 mismatch: '+name)


def unchanged_inventory(workspace, files, directories):
    current_files, current_directories = inventory(workspace)
    if current_directories != directories or set(current_files) != set(files):
        raise ValueError('Session folders changed during archiving; cleanup cancelled')
    if any(current_files[name].signature != entry.signature for name, entry in files.items()):
        raise ValueError('Source files changed during archiving; cleanup cancelled')


def cleanup(workspace, files, directories):
    removed = 0
    for _, entry in sorted(files.items()):
        # Preserve empty Git folder placeholders, but archive them too.
        if entry.path.name == '.gitkeep' and entry.signature[3] == 0:
            continue
        guard(workspace, entry)
        with entry.path.open('rb') as stream:
            if signature(os.fstat(stream.fileno())) != entry.signature or stream_hash(stream) != entry.sha256:
                raise ValueError('Source changed before cleanup: '+str(entry.path))
        guard(workspace, entry)
        entry.path.unlink()
        removed += 1
    keep = set(ROOTS) | {'input/kzb', 'input/canim', 'input/atlas'}
    for name in sorted(directories, key=lambda p: len(Path(p).parts), reverse=True):
        path = workspace.root/name
        if name in keep or not path.exists():
            continue
        if path.is_symlink():
            raise ValueError('Directory changed before cleanup: '+name)
        # A retained .gitkeep can keep a template directory; new files are never
        # removed to force a directory empty.
        remaining = list(path.iterdir())
        if remaining:
            if all(p.name == '.gitkeep' and not p.is_symlink() and p.is_file() and p.stat().st_size == 0 for p in remaining):
                continue
            raise ValueError('New or changed content remains: '+name)
        path.rmdir()
    # Restore the prepared skeleton even if the session originally lacked it.
    for name in (*ROOTS, 'input/kzb', 'input/canim', 'input/atlas'):
        path = workspace.root/name
        if path.is_symlink():
            raise ValueError('Directory changed before skeleton restoration: '+name)
        path.mkdir(parents=True, exist_ok=True)
    remaining_files, _ = inventory(workspace)
    if any(e.path.name != '.gitkeep' or e.signature[3] != 0 for e in remaining_files.values()):
        raise ValueError('New session files remain after cleanup; they were not deleted')
    return removed


def archive_session(workspace, now=None):
    files, directories = inventory(workspace)
    archive_folder = workspace.root/'archives'
    if archive_folder.is_symlink():
        raise ValueError('archives/ must not be a symbolic link')
    archive_folder.mkdir(exist_ok=True)
    stamp = (now or datetime.now().astimezone()).strftime('%Y-%m-%d_%H-%M-%S')
    output = archive_folder/f'Dorothy_Archive_{stamp}.zip'
    if output.exists() or output.is_symlink():
        raise FileExistsError('Archive already exists; try again in a second: '+str(output))
    temporary = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(dir=archive_folder, prefix='.Dorothy_Archive_', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
        write_archive(temporary, workspace, files, directories)
        verify_archive(temporary, files, directories)
        unchanged_inventory(workspace, files, directories)
        # Same-directory hard link publishes the verified file without overwrite.
        os.link(temporary, output)
        published = True
        removed = cleanup(workspace, files, directories)
        return dict(path=output, files=len(files), removed=removed,
                    original_bytes=sum(e.signature[3] for e in files.values()),
                    archive_bytes=output.stat().st_size)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, zlib.error, KeyboardInterrupt) as exc:
        if published:
            raise CleanupIncomplete(output, exc or 'Cleanup interrupted') from exc
        if isinstance(exc,zlib.error):
            raise ValueError('Archive verification failed: '+str(exc)) from exc
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
