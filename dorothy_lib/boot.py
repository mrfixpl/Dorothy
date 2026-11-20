#!/usr/bin/env python3
"""MST2 SCRT/MIFA BOOT parsing, colour conversion and reference-based rebuilding.
Python 3.10+, standard library only. Used by Dorothy workflows.
"""
import csv
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import struct
import zlib

from .png_codec import png_bytes, read_png, require

LIMIT=256*1024*1024
SENTINEL=0xffffffff
OPCODES={0:'BEGIN',1:'END',2:'CLEAR_SCREEN',3:'SET_RESOLUTION',4:'DRAW',5:'DRAW_STICKER',
         6:'WAIT',7:'ANIMATION_BOUNDARY',10:'IF_STICKER',11:'ELSE',12:'ENDIF'}


def parse(data):
    require(len(data)>=32 and data[:4]==b'SCRT','Missing SCRT header')
    version,variant,command_bytes,tag=struct.unpack_from('<4I',data,4)
    require((version,variant)==(101,1),'Unsupported SCRT version/variant')
    require(command_bytes%32==0,'Command block length is not divisible by 32')
    mifa=20+command_bytes
    require(mifa+12<=len(data) and data[mifa:mifa+4]==b'MIFA','Missing MIFA at command-block end')
    size,count=struct.unpack_from('<2I',data,mifa+4)
    require(mifa+8+size==len(data),'MIFA declared length mismatch')
    table_end=mifa+12+4*count
    require(table_end<=len(data),'Offset table outside file')
    offsets=struct.unpack_from('<'+str(count)+'I',data,mifa+12)
    if count:require(offsets[0]==table_end,'Unsupported gap before first image')
    commands=[]
    for i in range(command_bytes//32):
        values=struct.unpack_from('<8I',data,20+i*32)
        commands.append(dict(label=values[0],opcode=values[1],args=list(values[2:]),
                             name=OPCODES.get(values[1],'UNKNOWN'),offset=20+i*32))
        if values[1] in (4,5):
            require(values[2]<count,f'Command {i}: image reference outside table')
    images=[];decoded_total=0;trailer=b''
    for i,offset in enumerate(offsets):
        boundary=offsets[i+1] if i+1<count else len(data)
        require(table_end<=offset and offset+8<boundary<=len(data),f'Image {i}: invalid bounds/order')
        w,h=struct.unpack_from('<2I',data,offset)
        require(w>0 and h>0 and w%2==0,f'Image {i}: unsupported dimensions (width must be even)')
        expected=w*h*2;decoded_total+=expected
        require(decoded_total<=LIMIT,'Decoded images exceed 256 MiB budget')
        packed=data[offset+8:boundary]
        decoder=zlib.decompressobj()
        try:raw=decoder.decompress(packed,expected+1)
        except zlib.error as exc:raise ValueError(f'Image {i}: invalid zlib/checksum') from exc
        require(decoder.eof and not decoder.unconsumed_tail and len(raw)==expected,f'Image {i}: decoded length/zlib end mismatch')
        consumed=len(packed)-len(decoder.unused_data)
        if i+1<count:
            require(not decoder.unused_data,f'Image {i}: unexpected data between image streams')
        else:
            trailer=decoder.unused_data
            require(len(trailer) in (0,4*(count-1)) and not any(trailer),'Unsupported non-zero/unknown image trailer')
        images.append(dict(id=i,width=w,height=h,offset=offset,compressed_offset=offset+8,
                           compressed_bytes=consumed,decoded_bytes=expected,
                           pixels_sha256=hashlib.sha256(raw).hexdigest(),pixels=raw,
                           filename=f'img_{i:02d}.png',raw_filename=f'img_{i:02d}.mib'))
    if not count:require(table_end==len(data),'Unexpected data in empty MIFA')
    warnings=[]
    if any(c['name']=='UNKNOWN' for c in commands):warnings.append('Unknown animation opcodes preserved')
    if not any(c['opcode']==1 for c in commands):warnings.append('No explicit END opcode; observed in original startup_11')
    return dict(version=version,variant=variant,tag=tag,command_bytes=command_bytes,
                commands=commands,command_count=len(commands),mifa_offset=mifa,image_count=count,
                mifa_size=size,table_end=table_end,trailer_hex=trailer.hex(),
                decoded_image_bytes=decoded_total,images=images,warnings=warnings)


def clamp(value):return min(255,max(0,value))


@lru_cache(maxsize=128)
def decode_rgb(width,height,pixels):
    """Decode shared chroma + per-pixel green pairs; NOT luminance + alpha.
    Based on the channel equations documented by Toolbox's mib2image plugin.
    This previews a subsampled format; RGB is not a reversible native encoding.
    """
    require(width%2==0 and len(pixels)==width*height*2,'Invalid packed colour pixels')
    rgb=bytearray(width*height*3)
    at=0
    for i in range(0,len(pixels),4):
        cb,g0,cr,g1=pixels[i:i+4]
        delta_r=(4*cb+8*cr)//3-512
        delta_b=(8*cb+4*cr)//3-512
        rgb[at:at+6]=bytes((clamp(g0+delta_r),g0,clamp(g0+delta_b),
                            clamp(g1+delta_r),g1,clamp(g1+delta_b)))
        at+=6
    return bytes(rgb)


def encode_rgb(width,height,rgb,reference=None):
    """Encode paired chroma with quantisation; retain unchanged native pairs.
    Green is per pixel; red/blue chroma is shared across horizontal neighbours.
    New colours are lossy. Greys encode exactly with chroma=(128,128).
    """
    require(width%2==0 and len(rgb)==width*height*3,'Invalid RGB dimensions')
    preview=decode_rgb(width,height,reference) if reference is not None else None
    if preview is not None and preview==rgb:return reference
    result=bytearray(width*height*2)
    for i in range(0,len(rgb),6):
        out=(i//6)*4
        pair=rgb[i:i+6]
        if preview is not None and pair==preview[i:i+6]:
            result[out:out+4]=reference[out:out+4]
            continue
        r0,g0,b0,r1,g1,b1=pair
        if r0==g0==b0 and r1==g1==b1:
            result[out:out+4]=bytes((128,g0,128,g1))
            continue
        r_average=math.isqrt((r0*r0+r1*r1)//2)
        b_average=math.isqrt((b0*b0+b1*b1)//2)
        cb=clamp(128+(2*b0-r_average-g0)//4)
        cr=clamp(128+(2*r1-b_average-g1)//4)
        result[out:out+4]=bytes((cb,g0,cr,g1))
    return bytes(result)


def png_rgb(data):
    w,h,bpp,pixels=read_png(data)
    if bpp==3:return w,h,pixels
    if bpp==1:return w,h,bytes(v for value in pixels for v in (value,value,value))
    require(bpp==4 and all(pixels[i]==255 for i in range(3,len(pixels),4)),
            'Native BOOT colour data has no ordinary alpha; supply an opaque RGB PNG')
    return w,h,bytes(v for i in range(0,len(pixels),4) for v in pixels[i:i+3])


def png_native(data):
    w,h,bpp,pixels=read_png(data)
    require(bpp==4 and all(pixels[i]==pixels[i+1]==pixels[i+2] for i in range(0,len(pixels),4)),
            'Raw .mib replacement must be an LA PNG carrier, not an RGB preview')
    return w,h,bytes(v for i in range(0,len(pixels),4) for v in (pixels[i],pixels[i+3]))


def comment(command):
    a=command['args'];op=command['opcode']
    if op in (4,5):
        signed=lambda v:struct.unpack('<i',struct.pack('<I',v))[0]
        return f'{command["name"]} img_{a[0]:02d} at ({signed(a[2])},{signed(a[3])})'
    if op==6:return f'WAIT {a[0]/100:.2f} seconds (Toolbox interpretation)'
    if op==10:return f'IF_STICKER value={a[1]} (Toolbox interpretation)'
    return command['name']


def public_info(info):
    return dict(info,images=[{k:v for k,v in r.items() if k!='pixels'} for r in info['images']])


METADATA_FILES={'manifest.json','animation_script.csv','commands.bin','header.bin',
                'mifa_header.bin','offsets.bin','trailer.bin'}


def extract(path,output,resources_only=False):
    data=path.read_bytes();info=parse(data)
    output.mkdir(parents=True,exist_ok=False)
    (output/'images').mkdir()
    if not resources_only:
        (output/'raw').mkdir();(output/'records').mkdir()
    for r in info['images']:
        rgb=decode_rgb(r['width'],r['height'],r['pixels'])
        (output/'images'/r['filename']).write_bytes(png_bytes(r['width'],r['height'],3,rgb))
        if not resources_only:
            (output/'raw'/r['raw_filename']).write_bytes(png_bytes(r['width'],r['height'],2,r['pixels']))
            record=data[r['offset']:r['compressed_offset']+r['compressed_bytes']]
            (output/'records'/f'img_{r["id"]:02d}.bin').write_bytes(record)
    if not resources_only:
        (output/'header.bin').write_bytes(data[:20])
        (output/'commands.bin').write_bytes(data[20:20+info['command_bytes']])
        (output/'mifa_header.bin').write_bytes(data[info['mifa_offset']:info['mifa_offset']+12])
        (output/'offsets.bin').write_bytes(data[info['mifa_offset']+12:info['table_end']])
        (output/'trailer.bin').write_bytes(bytes.fromhex(info['trailer_hex']))
        with (output/'animation_script.csv').open('w',newline='',encoding='utf8') as stream:
            writer=csv.writer(stream,delimiter=';');writer.writerow(['label','opcode',*[f'arg{i}' for i in range(1,7)],'comment'])
            for c in info['commands']:writer.writerow([c['label'],c['opcode'],*c['args'],comment(c)])
    manifest=dict(source=path.name,source_sha256=hashlib.sha256(data).hexdigest(),
                  format='mst2_scrt_mifa',resources_only=resources_only,**public_info(info))
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return info


def rebuild(original,replacements,raw_mode=False,optimise=False):
    info=parse(original);known={r['raw_filename'] if raw_mode else r['filename']:r for r in info['images']}
    require(not set(replacements)-set(known),'Unknown replacement filenames: '+', '.join(sorted(set(replacements)-set(known))))
    pixels={}
    for name,image in replacements.items():
        record=known[name]
        w,h,data=png_native(image) if raw_mode else png_rgb(image)
        require((w,h)==(record['width'],record['height']),name+': dimensions must match original')
        native=data if raw_mode else encode_rgb(w,h,data,record['pixels'])
        pixels[record['id']]=native
    return _assemble(original,info,pixels,optimise)


def _assemble(original,info,pixels,optimise=False,progress=None):
    require(not set(pixels)-{r['id'] for r in info['images']},'Unknown native image ID')
    changes=[]
    for r in info['images']:
        native=pixels.get(r['id'],r['pixels'])
        require(len(native)==r['decoded_bytes'],'Native replacement length mismatch')
        if native!=r['pixels']:changes.append(r['id'])
    blocks=[]
    for index,r in enumerate(info['images'],1):
        if progress:progress(index,info['image_count'],r['filename'])
        old=original[r['compressed_offset']:r['compressed_offset']+r['compressed_bytes']]
        native=pixels.get(r['id'],r['pixels'])
        packed=old if native==r['pixels'] else zlib.compress(native,9)
        if optimise:
            for strategy in (zlib.Z_DEFAULT_STRATEGY,zlib.Z_FILTERED,zlib.Z_RLE,zlib.Z_HUFFMAN_ONLY):
                encoder=zlib.compressobj(9,zlib.DEFLATED,zlib.MAX_WBITS,8,strategy)
                candidate=encoder.compress(native)+encoder.flush()
                if len(candidate)<len(packed):packed=candidate
        blocks.append(struct.pack('<2I',r['width'],r['height'])+packed)
    offset=info['table_end'];offsets=[]
    for block in blocks:offsets.append(offset);offset+=len(block)
    trailer=bytes.fromhex(info['trailer_hex'])
    size=4+4*len(blocks)+sum(map(len,blocks))+len(trailer)
    require(size<=0xffffffff and offset+len(trailer)<=0xffffffff,'32-bit container size exceeded')
    result=(original[:info['mifa_offset']+4]+struct.pack('<2I',size,len(blocks))
            +b''.join(struct.pack('<I',v) for v in offsets)+b''.join(blocks)+trailer)
    updated=parse(result)
    require(result[:info['mifa_offset']+4]==original[:info['mifa_offset']+4],'Animation script changed')
    for old,new in zip(info['images'],updated['images']):
        require(new['pixels']==pixels.get(old['id'],old['pixels']),'Rebuilt pixel verification failed')
    return result,changes



def rebuild_assets(original,replacements):
    """Accept the normal RGB export, native .mib export, or a full export tree.

    Unchanged alternative representations never override an edited one. If both
    representations were changed, their resulting native samples must agree.
    Metadata is reference-only; changed compressed record exports are rejected.
    """
    info=parse(original)
    known={}
    for r in info['images']:
        for name,kind in ((r['filename'],'rgb'),('images/'+r['filename'],'rgb'),
                          (r['raw_filename'],'native'),('raw/'+r['raw_filename'],'native')):
            known[name]=(r,kind)
    entries={r['id']:{} for r in info['images']}
    for name,data in replacements.items():
        if name in METADATA_FILES:continue
        if name.startswith('records/'):
            record=next((r for r in info['images'] if name==f'records/img_{r["id"]:02d}.bin'),None)
            require(record is not None,'Unknown BOOT record path: '+name)
            expected=original[record['offset']:record['compressed_offset']+record['compressed_bytes']]
            require(data==expected,'Edit images/ PNG or raw/ .mib assets, not compressed records: '+name)
            continue
        require(name in known,'Unknown BOOT replacement path: '+name)
        r,kind=known[name]
        require(kind not in entries[r['id']],'Duplicate BOOT replacement aliases: '+name)
        w,h,decoded=png_rgb(data) if kind=='rgb' else png_native(data)
        require((w,h)==(r['width'],r['height']),name+': dimensions must match original')
        native=encode_rgb(w,h,decoded,r['pixels']) if kind=='rgb' else decoded
        entries[r['id']][kind]=native
    native_replacements={}
    for r in info['images']:
        changed=[v for v in entries[r['id']].values() if v!=r['pixels']]
        require(not changed or all(v==changed[0] for v in changed),f'Conflicting RGB/native replacements for img_{r["id"]:02d}')
        if changed:native_replacements[r['id']]=changed[0]
    return _assemble(original,info,native_replacements)


def optimise_container(original,progress=None):
    info=parse(original)
    result,_=_assemble(original,info,{},optimise=True,progress=progress)
    updated=parse(result)
    changed=sum(new['compressed_bytes']<old['compressed_bytes'] for old,new in zip(info['images'],updated['images']))
    return result,changed
