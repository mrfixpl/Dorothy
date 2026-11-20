"""Image integrity and conservative, lossless optimisation adapters."""
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

PNG = b'\x89PNG\r\n\x1a\n'
MAX_DECODED = 256 * 1024 * 1024


def _inflate(packed, expected):
    if expected > MAX_DECODED:
        raise ValueError('PNG decoded size exceeds 256 MiB inspection limit')
    stream = zlib.decompressobj()
    try:
        raw = stream.decompress(packed, expected + 1)
    except zlib.error as exc:
        raise ValueError('Invalid PNG compressed stream') from exc
    if len(raw) != expected or not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise ValueError('Invalid PNG compressed stream or scanline length')
    return raw


def png_signature(data):
    """Validate CRCs/scanlines and fingerprint unfiltered samples + metadata.

    Supports all standard PNG colour types, bit depths and Adam7. The fingerprint
    intentionally rejects colour format/metadata changes, even if visually equal.
    """
    if not data.startswith(PNG):
        raise ValueError('Invalid PNG signature')
    pos, chunks, packed, ended = 8, [], bytearray(), False
    ihdr = None
    seen_idat = False
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError('Truncated PNG chunk')
        size = struct.unpack_from('>I', data, pos)[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + size]
        if pos + size + 12 > len(data):
            raise ValueError('PNG chunk outside resource')
        crc = struct.unpack_from('>I', data, pos + 8 + size)[0]
        if zlib.crc32(kind + body) != crc:
            raise ValueError('PNG CRC mismatch: ' + repr(kind))
        if ihdr is None and kind != b'IHDR':
            raise ValueError('PNG must start with IHDR')
        if kind == b'IHDR':
            if ihdr is not None or size != 13:
                raise ValueError('Invalid PNG IHDR')
            ihdr = body
        if kind == b'IDAT':
            seen_idat = True
            packed.extend(body)
        else:
            chunks.append((kind, body))
        pos += size + 12
        if kind == b'IEND':
            if size:
                raise ValueError('Invalid IEND')
            ended = True
            break
    if not ended or pos != len(data) or not seen_idat:
        raise ValueError('Missing PNG data/IEND or trailing bytes')
    w, h, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', ihdr)
    legal = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if not w or not h or depth not in legal.get(color, ()) or compression or filtering or interlace not in (0, 1):
        raise ValueError('Unsupported/invalid PNG header')
    if color == 3 and not any(k == b'PLTE' for k, _ in chunks):
        raise ValueError('Indexed PNG has no palette')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits = depth * channels
    bpp = max(1, (bits + 7) // 8)
    passes = [(0, 0, 1, 1)] if interlace == 0 else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    rows = []
    for x, y, dx, dy in passes:
        pw, ph = max(0, (w - x + dx - 1) // dx), max(0, (h - y + dy - 1) // dy)
        if pw and ph:
            rows.append(((pw * bits + 7) // 8, ph))
    expected = sum((stride + 1) * height for stride, height in rows)
    raw = _inflate(bytes(packed), expected)
    digest, offset = hashlib.sha256(), 0
    for stride, height in rows:
        previous = bytearray(stride)
        for _ in range(height):
            mode = raw[offset]
            row = bytearray(raw[offset + 1:offset + 1 + stride])
            offset += stride + 1
            if mode not in range(5):
                raise ValueError('Invalid PNG row filter')
            if mode:
                for i in range(stride):
                    a = row[i-bpp] if i >= bpp else 0
                    b = previous[i]
                    c = previous[i-bpp] if i >= bpp else 0
                    if mode == 1:
                        predictor = a
                    elif mode == 2:
                        predictor = b
                    elif mode == 3:
                        predictor = (a+b)//2
                    else:
                        p = a+b-c
                        pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                        predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
                    row[i] = (row[i]+predictor) & 255
            digest.update(row)
            previous = row
    return ihdr, tuple(chunks), digest.hexdigest()


def jpeg_signature(data):
    """Validate JPEG marker framing and dimensions (not an entropy decoder)."""
    if not data.startswith(b'\xff\xd8'):
        raise ValueError('Invalid JPEG SOI')
    pos, dimensions, scans = 2, None, 0
    while pos < len(data):
        if data[pos] != 255:
            raise ValueError('Expected JPEG marker')
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            if pos != len(data) or dimensions is None or not scans:
                raise ValueError('Invalid JPEG end/dimensions')
            return dimensions
        if marker in (0x01, *range(0xd0, 0xd8)):
            continue
        if pos + 2 > len(data):
            break
        size = int.from_bytes(data[pos:pos+2], 'big')
        if size < 2 or pos + size > len(data):
            raise ValueError('Invalid JPEG segment size')
        if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
            body = data[pos+2:pos+size]
            if len(body) < 6:
                raise ValueError('Truncated JPEG frame')
            dimensions = (int.from_bytes(body[3:5], 'big'), int.from_bytes(body[1:3], 'big'), body[0], body[5])
            if not all(dimensions):
                raise ValueError('Invalid JPEG dimensions')
        pos += size
        if marker == 0xda:
            scans += 1
            while pos < len(data):
                found = data.find(b'\xff', pos)
                if found < 0:
                    raise ValueError('Missing JPEG EOI')
                end = found + 1
                while end < len(data) and data[end] == 255:
                    end += 1
                if end >= len(data):
                    raise ValueError('Truncated JPEG marker')
                if data[end] == 0 or 0xd0 <= data[end] <= 0xd7:
                    pos = end + 1
                    continue
                pos = found
                break
    raise ValueError('Missing JPEG EOI')


def tool_path(name):
    """Use PATH first, then optional tools shipped in an ImageOptim app bundle."""
    found = shutil.which(name)
    if found:
        return found
    for app in (Path('/Applications/ImageOptim.app'), Path.home()/'Applications/ImageOptim.app'):
        for directory in ('Contents/MacOS', 'Contents/Resources', 'Contents/Frameworks/ImageOptimGPL.framework/Versions/A/Resources'):
            candidate = app/directory/name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def optimizer_tools():
    return {name: tool_path(name) for name in ('oxipng', 'optipng', 'pngcrush', 'jpegtran', 'jpegoptim')}


def optimise(data, kind, work, tools):
    """Only return a smaller lossless candidate; never process source files."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    extension = '.png' if kind == 'png' else '.jpg'
    source, result = work/('texture'+extension), work/('result'+extension)
    source.write_bytes(data)
    result.unlink(missing_ok=True)
    if kind == 'png':
        before = png_signature(data)
        if tools.get('oxipng'):
            command = [tools['oxipng'], '-o', '2', '--nb', '--nc', '--np', '--out', str(result), str(source)]
        elif tools.get('optipng'):
            command = [tools['optipng'], '-o2', '-nb', '-nc', '-np', '-out', str(result), str(source)]
        elif tools.get('pngcrush'):
            command = [tools['pngcrush'], '-q', str(source), str(result)]
        else:
            raise ValueError('missing_png_optimizer')
    else:
        before = jpeg_signature(data)
        if tools.get('jpegtran'):
            command = [tools['jpegtran'], '-copy', 'all', '-optimize', '-outfile', str(result), str(source)]
        elif tools.get('jpegoptim'):
            # No quality/-m option: lossless entropy optimisation only.
            command = [tools['jpegoptim'], '--preserve', str(source)]
            result = source
        else:
            raise ValueError('missing_jpeg_optimizer')
    completed = subprocess.run(command, capture_output=True, timeout=300, check=False)
    if completed.returncode:
        raise ValueError('Optimizer failed: ' + completed.stderr.decode(errors='replace')[-800:])
    candidate = result.read_bytes()
    if len(candidate) >= len(data):
        return data
    after = png_signature(candidate) if kind == 'png' else jpeg_signature(candidate)
    if before != after:
        # E.g. PNG metadata/colour format conversion; retain original bytes.
        return data
    return candidate
