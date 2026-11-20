#!/usr/bin/env python3
"""Extract VC TC/BIN texture atlases to PNG using only Python 3's standard library.

Input: .atlas, companion .json, or a folder (non-recursive). JSON maps UIDs to
filenames; pixels and rectangles live in the matching .atlas. Output: a sibling
folder named after the atlas, with atlas.png, images/, manifest.json and the
original MAXRECT trailer. Existing output folders are never overwritten.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import struct
import sys
import zlib


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_atlas(data):
    require(len(data) >= 47 and data[:3] == b'TC\0', 'Not a supported TC atlas')
    header = struct.unpack_from('<11I', data, 3)
    a, b, c, d, width, height, channels, e, f, g, count = header
    require((a, b, c, d, e, f, g) == (4, 4, 1, 1, 0, 2, 4), 'Unsupported TC header variant')
    require(width > 0 and height > 0 and channels in (2, 4), 'Unsupported atlas dimensions/channels')
    bin_offset = 47 + 8 * count
    require(bin_offset + 36 + 24 * count <= len(data), 'Truncated atlas tables')
    require(data[bin_offset:bin_offset + 4] == b'BIN\0', 'Missing BIN section at expected offset')
    table = [struct.unpack_from('<II', data, 47 + i * 8) for i in range(count)]
    require(all(flag == 0 for _, flag in table), 'Unsupported TC UID flags')
    require(struct.unpack_from('<8I', data, bin_offset + 4) == (4, 4, width, height, channels, 2, 4, count), 'TC/BIN header mismatch')
    entries = []
    for i in range(count):
        uid, uw, uh, ux, uy, w, h = struct.unpack_from('<I4f2H', data, bin_offset + 36 + 24 * i)
        require(uid == table[i][0], 'TC/BIN UID mismatch')
        require(all(math.isfinite(v) for v in (uw, uh, ux, uy)), 'Non-finite UV value')
        x, y = round(ux * width), round(uy * height)
        require(w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width and y + h <= height, f'UID {uid}: rectangle outside atlas')
        require(abs(uw * width - w) < 0.001 and abs(uh * height - h) < 0.001, f'UID {uid}: dimensions disagree with UVs')
        require(abs(ux * width - x) < 0.001 and abs(uy * height - y) < 0.001, f'UID {uid}: non-integer origin')
        entries.append(dict(uid=uid, x=x, y=y, width=w, height=h, uv=[uw, uh, ux, uy]))
    require(len({r['uid'] for r in entries}) == count, 'Duplicate atlas UID')
    pixel_offset = bin_offset + 36 + 24 * count
    pixel_end = pixel_offset + width * height * channels
    require(pixel_end <= len(data), 'Truncated pixel buffer')
    trailer = data[pixel_end:]
    require(trailer.startswith(b'MAXRECT\0'), 'Missing MAXRECT trailer; unsupported atlas layout')
    return dict(width=width, height=height, channels=channels, mode='LA' if channels == 2 else 'RGBA', pixel_offset=pixel_offset, pixel_end=pixel_end, entries=entries), data[pixel_offset:pixel_end], trailer


def png_bytes(width, height, channels, pixels):
    require(channels in (2, 4) and len(pixels) == width * height * channels, 'Invalid PNG pixel buffer')
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    stride = width * channels
    rows = b''.join(b'\0' + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 4 if channels == 2 else 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def crop_pixels(pixels, atlas_width, channels, record):
    stride = atlas_width * channels
    return b''.join(pixels[y * stride + record['x'] * channels:y * stride + (record['x'] + record['width']) * channels]
                    for y in range(record['y'], record['y'] + record['height']))


def image_path(name):
    require(isinstance(name, str) and bool(name), 'Invalid asset filename')
    require('\\' not in name and '\0' not in name, f'Unsafe asset filename: {name!r}')
    parts = name.split('/')
    require(not name.startswith('/') and all(p not in ('', '.', '..') and ':' not in p for p in parts), f'Unsafe asset filename: {name!r}')
    require(PurePosixPath(name).suffix.lower() == '.png', f'Expected PNG asset name: {name}')
    return Path('images', *parts)


def extract(path, output=None):
    data = path.read_bytes()
    info, pixels, trailer = parse_atlas(data)
    companion = path.with_suffix('.json')
    names = {}
    if companion.exists():
        document = json.loads(companion.read_text(encoding='utf-8-sig'))
        require(isinstance(document, dict) and isinstance(document.get('images'), list), 'Invalid JSON images mapping')
        for item in document['images']:
            require(isinstance(item, dict) and type(item.get('UID')) is int, 'Invalid JSON UID')
            uid = item['UID']
            require(uid not in names, f'Duplicate JSON UID: {uid}')
            names[uid] = item.get('filename')
        require(set(names) == {r['uid'] for r in info['entries']}, 'JSON/atlas UID sets differ')
    used_paths = set()
    for record in info['entries']:
        relative = image_path(names.get(record['uid'], f"UID_{record['uid']:05d}.png"))
        # Reject case-only collisions also on case-sensitive hosts.
        key = relative.as_posix().casefold()
        require(key not in used_paths, f'Duplicate asset output path: {relative}')
        used_paths.add(key)
        record['extracted_path'] = relative.as_posix()
    output = Path(output) if output is not None else path.with_suffix('')
    output.mkdir(exist_ok=False)
    (output/'atlas.png').write_bytes(png_bytes(info['width'], info['height'], info['channels'], pixels))
    (output/'maxrect.bin').write_bytes(trailer)
    for record in info['entries']:
        target = output/record['extracted_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        asset = crop_pixels(pixels, info['width'], info['channels'], record)
        record['pixels_sha256'] = hashlib.sha256(asset).hexdigest()
        target.write_bytes(png_bytes(record['width'], record['height'], info['channels'], asset))
    manifest = dict(source=path.name, source_sha256=hashlib.sha256(data).hexdigest(),
                    format='vc_tc_bin_v4', mapping=companion.name if names else None,
                    row_order='top_to_bottom', **info)
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return output, len(info['entries'])


