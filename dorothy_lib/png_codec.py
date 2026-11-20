"""Standard-library PNG reader/writer for native BOOT carriers and RGB assets."""
import struct
import zlib
PNG=b'\x89PNG\r\n\x1a\n'
LIMIT=256*1024*1024
def require(condition,message):
    if not condition:raise ValueError(message)

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


def png_bytes(width,height,channels,pixels):
    require(channels in (1,2,3,4) and width>0 and height>0 and len(pixels)==width*height*channels,'Invalid PNG pixels')
    def chunk(kind,body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    stride=width*channels
    data=b''.join(b'\0'+pixels[y*stride:(y+1)*stride] for y in range(height))
    return PNG+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,{1:0,2:4,3:2,4:6}[channels],0,0,0))+chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b'')

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


