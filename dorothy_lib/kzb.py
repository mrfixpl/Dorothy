#!/usr/bin/env python3
"""Experimental read-only extractor for the supplied .KZB corpus; not a Studio project converter."""
from pathlib import Path
import struct,re

def detect_variant(data):
 """Observed header profiles, not a universal device identification."""
 if len(data)<44 or data[:4]!=b'.KZB':raise ValueError('Invalid KZB header')
 signature=data[12:28].hex()
 profiles={
  '0000004a000200050002015300000001':'mib_observed',
  '0000004a000200050002018300000001':'vc_observed',
 }
 return dict(profile=profiles.get(signature,'unknown_header'),layout='kzb_indexed_be_payload_le',header_signature=signature,version_word=int.from_bytes(data[20:24],'big'))

def check(condition, message='Invalid KZB structure'):
 if not condition:raise ValueError(message)

def parse(b):
 if len(b)<64 or b[:4]!=b'.KZB':raise ValueError('Invalid KZB header')
 if int.from_bytes(b[4:8],'big')!=len(b):raise ValueError('File size mismatch')
 if int.from_bytes(b[8:12],'big')!=44:raise ValueError('Unsupported header size')
 def u(p):return struct.unpack_from('>I',b,p)[0]
 def s(p):
  n=int.from_bytes(b[p:p+2],'big');check(p+2+n<=len(b), 'String outside file');return b[p+2:p+2+n].decode('utf8'),p+((n+5)//4)*4
 def rec(p,g):
  vals=struct.unpack_from('>4I',b,p);name,q=s(p+16);return dict(index_entry_offset=p,group=g,name=name,offset=vals[0],size=vals[1],type=vals[2],flags=vals[3]),q
 rs=[];gs=[]
 def group(p,parent=''):
  st=p;sz=u(p);name,p=s(p+4);name=parent+'/'+name if parent else name;gs.append(name)
  ng=u(p);p+=4
  for _ in range(ng):p=group(p,name)
  n=u(p);p+=4
  for _ in range(n):r,p=rec(p,name);rs.append(r)
  check(p==st+sz, 'Directory size mismatch: '+name)
  return p
 p=64
 for _ in range(u(60)):p=group(p)
 n=u(p);p+=4
 for _ in range(n):r,p=rec(p,'@root');rs.append(r)
 check(p==52+u(52), 'Directory section size mismatch')
 nr=u(p);p+=4;refs=[]
 for _ in range(nr):name,p=s(p);refs.append(name)
 check(p==u(44)+40, 'Index end mismatch')
 return rs,refs,gs,p

def identify_file(payload, name):
 """Recognize standalone resource formats without changing their bytes."""
 import io,zipfile
 suffix=Path(name).suffix.lower()
 result={'format':None,'file_status':'unrecognized'}
 if not payload:
  return dict(result,file_status='empty',file_warning='Resource has zero bytes in the KZB index.')
 if payload.startswith(b'\x89PNG\r\n\x1a\n'):
  result.update(format='png',file_status='recognized')
 elif payload.startswith(b'\xff\xd8\xff'):
  result.update(format='jpeg',file_status='recognized')
  if not payload.endswith(b'\xff\xd9'):
   result.update(file_status='invalid',file_warning='JPEG end marker is missing.')
 elif payload[:4] in (b'\x00\x01\x00\x00',b'true',b'OTTO'):
  result.update(format='otf' if payload[:4]==b'OTTO' else 'ttf',file_status='recognized')
  if len(payload)<12:
   result.update(file_status='invalid',file_warning='Truncated font header.')
  else:
   count=int.from_bytes(payload[4:6],'big')
   valid=count>0 and 12+16*count<=len(payload)
   if valid:
    for i in range(count):
     _,_,offset,size=struct.unpack_from('>4sIII',payload,12+16*i)
     if offset+size>len(payload):valid=False;break
   if not valid:result.update(file_status='invalid',file_warning='Font table directory exceeds resource bounds.')
 elif payload.startswith(b'PK\x03\x04') or suffix=='.xlsx':
  result.update(format='zip',file_status='recognized')
  try:
   with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    names=set(archive.namelist())
    if {'[Content_Types].xml','_rels/.rels','xl/workbook.xml'}<=names:
     result['format']='xlsx'
    elif suffix=='.xlsx':
     result.update(file_status='invalid',file_warning='ZIP lacks required XLSX package entries.')
  except (zipfile.BadZipFile,ValueError):
   result.update(file_status='invalid',file_warning='Invalid ZIP/XLSX directory.')
 return result

def extracted_path(index, record):
 """Canonical path shared by extraction and reference-based repacking."""
 safe=lambda x:re.sub(r'[^A-Za-z0-9_. -]','_',x)[:100] or '_'
 parts=[safe(x) for x in record['group'].split('/')]
 parts=[('_' if x in ('.','..') else x) for x in parts]
 filename=f"{index:05d}_"+safe(record['name'])
 if record['type']==2010:filename+='.glsl'
 elif not (record['group']=='Resource Files' or record['group'].startswith('Resource Files/')):filename+='.bin'
 return Path(*parts,filename)

