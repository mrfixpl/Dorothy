#!/usr/bin/env python3
"""Experimental standalone ANIM1/CANIM inspection, PNG extraction and rebuilding.
Python 3.10+, standard library only. Does not implement the animation VM.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

MAGIC = b'ANIM1   '
PNG = b'\x89PNG\r\n\x1a\n'
LIMIT = 256 * 1024 * 1024
MODES = {1: 'L', 3: 'RGB', 4: 'RGBA'}
IMAGE_OPCODES = (0x11, 0x15)


def require(value, message):
    if not value:
        raise ValueError(message)


def inflate(data, expected=None):
    bound = LIMIT if expected is None else expected
    require(0 <= bound <= LIMIT, 'Decoded data exceeds 256 MiB limit')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(data, bound + 1)
    except zlib.error as exc:
        raise ValueError('Invalid zlib stream/checksum') from exc
    require(len(raw) <= bound and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
            'Truncated/oversized zlib stream or trailing compressed data')
    if expected is not None:
        require(len(raw) == expected, 'Decompressed length mismatch')
    return raw


def parse_raw(raw):
    require(len(raw) >= 32 and raw[:8] == MAGIC, 'Invalid ANIM1 header')
    width, height, command_bytes, count_field = struct.unpack_from('<4I', raw, 8)
    require(width > 0 and height > 0, 'Invalid stage dimensions')
    require(command_bytes % 4 == 0, 'Command length must be word-aligned')
    pixel_base = 32 + command_bytes
    require(pixel_base <= len(raw), 'Command block outside file')
    image_count, pixel_bytes = struct.unpack_from('<2I', raw, pixel_base - 8)
    require(pixel_base + pixel_bytes == len(raw), 'Pixel-data length/footer mismatch')
    images = []
    offset = 24
    if image_count and struct.unpack_from('<I', raw, offset)[0] not in IMAGE_OPCODES:
        # Variant used by the upstream Toolbox for Seat/Porsche; untested here.
        offset += 120
    for _ in range(image_count):
        require(offset + 32 <= pixel_base - 8, 'Truncated image declaration')
        opcode, uid, w, h, bpp, start, binding, bind_id = struct.unpack_from('<8I', raw, offset)
        require(opcode in IMAGE_OPCODES and binding == 0xcc, 'Unsupported image-declaration layout')
        require(w > 0 and h > 0 and bpp in MODES, 'Unsupported image dimensions/pixel format')
        size = w * h * bpp
        require(start + size <= pixel_bytes, f'Image {uid} outside pixel data')
        images.append(dict(id=uid, opcode=opcode, opcode_hex=f'0x{opcode:02x}', width=w, height=h, bpp=bpp, mode=MODES[bpp],
                           declaration_offset=offset, relative_offset=start,
                           offset=pixel_base+start, size=size, binding_id=bind_id,
                           filename=f'img_{uid}.png'))
        offset += 32
    require(len({r['id'] for r in images}) == image_count, 'Duplicate image ID')
    end = 0
    for record in sorted(images, key=lambda r: r['relative_offset']):
        # These files use a fully packed buffer. Reject unsupported gaps/aliases
        # rather than guessing whether unknown bytes have other consumers.
        require(record['relative_offset'] == end, 'Unsupported image gaps/overlap/alias')
        end += record['size']
    require(end == pixel_bytes, 'Unaccounted image bytes')
    return dict(image_opcodes=sorted({r['opcode_hex'] for r in images}),stage_width=width, stage_height=height, command_bytes=command_bytes,
                command_count_field=count_field, pixel_base=pixel_base,
                pixel_bytes=pixel_bytes, image_count=image_count, images=images,
                commands_sha256=hashlib.sha256(raw[24:24+command_bytes]).hexdigest())


def inspect(data):
    raw = inflate(data)
    return parse_raw(raw), raw


def png_bytes(width, height, bpp, pixels):
    require(bpp in MODES and len(pixels) == width*height*bpp, 'Invalid PNG pixel buffer')
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind+body))
    stride = width*bpp
    rows = b''.join(b'\0'+pixels[y*stride:(y+1)*stride] for y in range(height))
    return (PNG + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, {1:0,3:2,4:6}[bpp], 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def read_png(data):
    """Read non-interlaced PNG, 8-bit L/LA/RGB/RGBA and 1/2/4/8-bit palettes.
    Never discard colour/alpha silently. 16-bit/Adam7 inputs must be exported as
    ordinary non-interlaced 8-bit PNG before import.
    """
    require(data.startswith(PNG), 'Replacement must be PNG')
    pos, header, palette, transparency, ended = 8, None, None, None, False
    packed = bytearray()
    while pos < len(data):
        require(pos+12 <= len(data), 'Truncated PNG chunk')
        size = struct.unpack_from('>I', data, pos)[0]
        kind, body = data[pos+4:pos+8], data[pos+8:pos+8+size]
        require(pos+size+12 <= len(data), 'PNG chunk outside file')
        require(zlib.crc32(kind+body) == struct.unpack_from('>I', data, pos+8+size)[0], 'PNG CRC mismatch')
        require(header is not None or kind == b'IHDR', 'PNG must start with IHDR')
        if kind == b'IHDR':
            require(header is None and size == 13, 'Invalid IHDR')
            header = struct.unpack('>IIBBBBB', body)
        elif kind == b'PLTE':
            palette = body
        elif kind == b'tRNS':
            transparency = body
        elif kind == b'IDAT':
            packed.extend(body)
        elif kind == b'IEND':
            require(size == 0, 'Invalid IEND')
            ended = True
        elif kind in (b'acTL', b'fcTL', b'fdAT'):
            raise ValueError('APNG replacement is not supported')
        elif not kind[0] & 32:
            raise ValueError('Unsupported critical PNG chunk')
        pos += size+12
        if ended:
            break
    require(ended and pos == len(data) and header is not None and packed, 'Missing PNG image/IEND or trailing bytes')
    w, h, depth, color, comp, filt, interlace = header
    require(w > 0 and h > 0 and comp == filt == interlace == 0, 'Use non-interlaced PNG')
    require((depth == 8 and color in (0,2,4,6)) or (color == 3 and depth in (1,2,4,8)), 'Use 8-bit L/LA/RGB/RGBA or indexed PNG')
    channels = {0:1,2:3,3:1,4:2,6:4}[color]
    stride = (w*channels*depth+7)//8
    step = max(1, (channels*depth+7)//8)
    uncompressed = inflate(bytes(packed), h*(stride+1))
    samples = bytearray()
    previous = bytearray(stride)
    offset = 0
    for y in range(h):
        mode = uncompressed[offset]
        require(mode <= 4, 'Invalid PNG row filter')
        row = bytearray(uncompressed[offset+1:offset+1+stride])
        offset += stride+1
        if mode:
            for i in range(stride):
                a = row[i-step] if i >= step else 0
                b = previous[i]
                c = previous[i-step] if i >= step else 0
                if mode == 1: pred = a
                elif mode == 2: pred = b
                elif mode == 3: pred = (a+b)//2
                else:
                    p = a+b-c
                    pa,pb,pc = abs(p-a),abs(p-b),abs(p-c)
                    pred = a if pa<=pb and pa<=pc else b if pb<=pc else c
                row[i] = (row[i]+pred)&255
        samples.extend(row)
        previous = row
    if color == 3:
        require(palette is not None and len(palette)%3 == 0 and 0 < len(palette) <= 768, 'Invalid PNG palette')
        require(transparency is None or len(transparency) <= len(palette)//3, 'Invalid palette alpha')
        out = bytearray()
        bpp = 4 if transparency is not None else 3
        for y in range(h):
            row = samples[y*stride:(y+1)*stride]
            for x in range(w):
                index = (row[(x*depth)//8] >> (8-depth-(x*depth)%8)) & ((1<<depth)-1)
                require(index*3+3 <= len(palette), 'Palette index outside PLTE')
                out.extend(palette[index*3:index*3+3])
                if bpp == 4:
                    out.append(transparency[index] if index < len(transparency) else 255)
        return w,h,bpp,bytes(out)
    if color == 4:
        return w,h,4,bytes(v for i in range(0,len(samples),2) for v in (samples[i],samples[i],samples[i],samples[i+1]))
    if transparency is not None:
        require(color in (0,2) and len(transparency) == (2 if color==0 else 6), 'Invalid tRNS')
        key = struct.unpack('>'+('H' if color==0 else '3H'), transparency)
        out = bytearray()
        for i in range(0,len(samples),channels):
            pixel = tuple(samples[i:i+channels])
            rgb = pixel*3 if color==0 else pixel
            out.extend((*rgb,0 if pixel==key else 255))
        return w,h,4,bytes(out)
    return w,h,channels,bytes(samples)


def convert_pixels(pixels, source_bpp, target_bpp):
    if source_bpp == target_bpp:
        return pixels
    out = bytearray()
    for i in range(0,len(pixels),source_bpp):
        p = pixels[i:i+source_bpp]
        r,g,b,a = (p[0],p[0],p[0],255) if source_bpp==1 else (*p,255) if source_bpp==3 else p
        if target_bpp == 1:
            require(r==g==b and a==255, 'Colour/alpha cannot be stored in one channel; use --pixel-format auto or rgba')
            out.append(r)
        elif target_bpp == 3:
            require(a==255, 'Transparency would be lost; use --pixel-format rgba')
            out.extend((r,g,b))
        else:
            out.extend((r,g,b,a))
    return bytes(out)


def rebuild(original, replacements, pixel_format='auto'):
    """Replace image payloads; update bpp, relative offsets and data-size footer.
    Preserve the animation command stream except image declaration bpp/offsets.
    Preserve the exact original compressed bytes if replacements have no effect.
    """
    require(pixel_format in ('auto','preserve','rgb','rgba'), 'Invalid pixel format')
    info, raw = inspect(original)
    known = {r['filename'] for r in info['images']}
    require(not set(replacements)-known, 'Unknown replacement filenames: '+', '.join(sorted(set(replacements)-known)))
    prefix = bytearray(raw[:info['pixel_base']])
    payload = bytearray()
    changes = []
    decoded = {}
    for name,png in replacements.items():
        decoded[name] = read_png(png)
    for record in sorted(info['images'], key=lambda r: r['relative_offset']):
        name = record['filename']
        old = raw[record['offset']:record['offset']+record['size']]
        bpp, image = record['bpp'], old
        if name in decoded:
            w,h,in_bpp,pixels = decoded[name]
            require((w,h)==(record['width'],record['height']), f'{name}: dimensions must remain {record["width"]}x{record["height"]}')
            bpp = in_bpp if pixel_format=='auto' else record['bpp'] if pixel_format=='preserve' else 3 if pixel_format=='rgb' else 4
            image = convert_pixels(pixels,in_bpp,bpp)
        require(len(payload)<=0xffffffff, '32-bit pixel offset overflow')
        struct.pack_into('<II',prefix,record['declaration_offset']+16,bpp,len(payload))
        payload.extend(image)
        if bpp!=record['bpp'] or image!=old:
            changes.append(dict(id=record['id'],file=name,old_bpp=record['bpp'],new_bpp=bpp,old_size=record['size'],new_size=len(image)))
    require(len(payload)<=0xffffffff and len(prefix)+len(payload)<=LIMIT, 'Pixel buffer exceeds size limit')
    struct.pack_into('<I',prefix,info['pixel_base']-4,len(payload))
    rebuilt_raw = bytes(prefix+payload)
    updated = parse_raw(rebuilt_raw)
    require(updated['image_count']==info['image_count'], 'Rebuilt image count mismatch')
    # Enforce preservation of every command byte except the two documented fields.
    expected_commands = bytearray(raw[24:24+info['command_bytes']])
    for r in info['images']:
        relative = r['declaration_offset']-24+16
        expected_commands[relative:relative+8] = prefix[r['declaration_offset']+16:r['declaration_offset']+24]
    require(rebuilt_raw[24:24+info['command_bytes']]==expected_commands, 'Animation program changed unexpectedly')
    if rebuilt_raw==raw:
        return original, changes
    result = zlib.compress(rebuilt_raw,9)
    verified, decoded_raw = inspect(result)
    require(decoded_raw==rebuilt_raw, 'Recompression verification failed')
    return result, changes


def extract(path, output):
    info,raw = inspect(path.read_bytes())
    output.mkdir(parents=True,exist_ok=False)
    for record in info['images']:
        pixels = raw[record['offset']:record['offset']+record['size']]
        record['pixels_sha256'] = hashlib.sha256(pixels).hexdigest()
        record['achromatic'] = record['bpp']==1 or all(pixels[i]==pixels[i+1]==pixels[i+2] for i in range(0,len(pixels),record['bpp']))
        (output/record['filename']).write_bytes(png_bytes(record['width'],record['height'],record['bpp'],pixels))
    (output/'commands.bin').write_bytes(raw[24:24+info['command_bytes']])
    manifest = dict(source=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),decoded_bytes=len(raw),**info)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return manifest


def new_file(path,data):
    # Exclusive creation avoids accidental overwrite of original/output files.
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


