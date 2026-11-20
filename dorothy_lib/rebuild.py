#!/usr/bin/env python3
"""Rebuild the supported MIB KZB variant using original index and metadata.

Only existing payloads can be replaced. Keep the relative paths produced by
the extraction menu in custom/<KZB name>/; omitted files retain their original bytes.
"""
import argparse
import os
from pathlib import Path
import struct
import sys
import tempfile

from .kzb import extracted_path, parse


def validate(data):
    records, references, groups, end = parse(data)
    if len(records) + len(references) != int.from_bytes(data[48:52], 'big'):
        raise ValueError('Index record count mismatch')
    ordered = sorted(records, key=lambda r: r['offset'])
    if not ordered or ordered[0]['offset'] != end + 4:
        raise ValueError('Invalid first object offset')
    for i, record in enumerate(ordered):
        offset, size = record['offset'], record['size']
        if offset < end or offset + 4 + size > len(data):
            raise ValueError('Object outside file bounds')
        metadata_size = int.from_bytes(data[offset:offset + 4], 'little')
        next_offset = ordered[i + 1]['offset'] if i + 1 < len(ordered) else len(data)
        if offset + 4 + size + metadata_size != next_offset:
            raise ValueError('Object metadata boundary mismatch')
    return records, references, groups, end


def rebuild(original, replacements):
    """Return rebuilt bytes and changed paths; replacements use canonical paths."""
    records, references, groups, end = validate(original)
    expected = {extracted_path(i, record).as_posix(): record for i, record in enumerate(records)}
    unknown = set(replacements) - set(expected)
    if unknown:
        raise ValueError('Files do not match reference resource paths: ' + ', '.join(sorted(unknown)))
    by_offset = {record['offset']: name for name, record in expected.items()}
    output = bytearray(original[:end + 4])
    changes = []
    for record in sorted(records, key=lambda r: r['offset']):
        offset, size = record['offset'], record['size']
        name = by_offset[offset]
        old_payload = original[offset + 4:offset + 4 + size]
        payload = replacements.get(name, old_payload)
        metadata_size = int.from_bytes(original[offset:offset + 4], 'little')
        metadata = original[offset + 4 + size:offset + 4 + size + metadata_size]
        if len(output) > 0xffffffff or len(payload) > 0xffffffff:
            raise ValueError('KZB exceeds 32-bit offset or size limits')
        struct.pack_into('>II', output, record['index_entry_offset'], len(output), len(payload))
        output.extend(original[offset:offset + 4])
        output.extend(payload)
        output.extend(metadata)
        if payload != old_payload:
            changes.append(name)
    if len(output) > 0xffffffff:
        raise ValueError('KZB exceeds 32-bit file size limit')
    struct.pack_into('>I', output, 4, len(output))
    rebuilt = bytes(output)
    check, check_refs, check_groups, check_end = validate(rebuilt)
    if references != check_refs or groups != check_groups or end != check_end:
        raise ValueError('Rebuilt index changed unexpectedly')
    for old, new in zip(records, check):
        name = by_offset[old['offset']]
        payload = replacements.get(name, original[old['offset'] + 4:old['offset'] + 4 + old['size']])
        if rebuilt[new['offset'] + 4:new['offset'] + 4 + new['size']] != payload:
            raise ValueError('Rebuilt payload verification failed')
    return rebuilt, changes


def read_modifications(folder):
    replacements = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symbolic links are not supported: {path}')
        if not path.is_file():
            continue
        name = path.relative_to(folder).as_posix()
        if name == 'manifest.json' or path.name == '.DS_Store':
            continue
        replacements[name] = path.read_bytes()
    return replacements


def write_new_file(path, data):
    # Publish only the completed file, without overwriting an existing output.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink()


