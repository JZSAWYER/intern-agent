"""Content-addressed recovery parts, bounded and verified before adoption."""
import hashlib
import json
import re
from pathlib import Path
from .core import Invalid

SCHEMA='intern-agent.recovery.parts.v1'
CHUNK=128*1024
MAX_TOTAL=64*1024*1024


def write(tracker,binding,data):
    # Keep the existing restore schema and every string value, without pretty
    # indentation or expanding ordinary Unicode into repeated ASCII escapes.
    raw=(json.dumps(data,ensure_ascii=False,separators=(',',':'),sort_keys=True)+'\n').encode('utf-8',errors='backslashreplace')
    if len(raw)>MAX_TOTAL:raise Invalid('Recovery history exceeds 64 MiB; start a separate workspace archive')
    if len(raw)<=256*1024:
        tracker.write(binding,'.intern_workspace/recovery.json',raw.decode());prune(tracker,binding,set());return
    parts=[]
    offset=0
    while offset<len(raw):
        end=min(offset+CHUNK,len(raw))
        while end<len(raw) and raw[end]&0xc0==0x80:end-=1
        chunk=raw[offset:end];offset=end;digest=hashlib.sha256(chunk).hexdigest()
        path='.intern_workspace/recovery-parts/'+digest+'.json'
        tracker.write(binding,path,json.dumps(chunk.decode(),ensure_ascii=False)+'\n')
        parts.append(dict(path=path,sha256=digest,bytes=len(chunk)))
    tracker.write(binding,'.intern_workspace/recovery.json',json.dumps(dict(schema=SCHEMA,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),parts=parts),indent=2)+'\n')
    prune(tracker,binding,{p['path'] for p in parts})


def prune(tracker,binding,keep):
    """Remove only unused generated parts already preserved in Git history."""
    if not hasattr(tracker,'git'):return
    root=Path(binding['metadata_path']);directory=root/'.intern_workspace/recovery-parts'
    if not directory.exists():return
    if directory.is_symlink():raise Invalid('Linked recovery directory cannot be pruned')
    tracked=tracker.git(root,'ls-files','-z','--','.intern_workspace/recovery-parts').split('\0')
    from .git_objects import Objects
    with Objects(root) as objects:
        for name in filter(None,tracked):
            if name in keep or not re.fullmatch(r'\.intern_workspace/recovery-parts/[0-9a-f]{64}\.json',name):continue
            path=root/name
            if not path.exists() and not path.is_symlink():continue
            if path.is_symlink() or not path.is_file():raise Invalid('Linked recovery part cannot be pruned')
            try:
                if path.stat().st_size>CHUNK*6+4:raise Invalid('Modified recovery part needs review')
                chunk=json.loads(path.read_text())
                if not isinstance(chunk,str) or hashlib.sha256(chunk.encode()).hexdigest()!=path.stem:raise Invalid('Modified recovery part needs review')
            except (OSError,ValueError,UnicodeError):raise Invalid('Modified recovery part needs review') from None
            try:committed=objects.blob('HEAD:'+name)
            except Invalid:continue  # Newly staged parts have no historical copy.
            if committed!=path.read_bytes() or objects.blob(':'+name)!=committed:raise Invalid('Modified recovery part needs review')
            path.unlink()
    if hasattr(getattr(tracker,'state',None),'runtime'):
        from .record_storage import collect
        collect(tracker,binding)


def expand(data,reader):
    if not isinstance(data,dict) or data.get('schema')!=SCHEMA:return data
    if set(data)!={'schema','sha256','bytes','parts'} or not isinstance(data['parts'],list) or not 1<=len(data['parts'])<=MAX_TOTAL//CHUNK+1 or type(data['bytes']) is not int or not 0<data['bytes']<=MAX_TOTAL or not reader:raise Invalid('Malformed recovery manifest or missing part reader')
    chunks=[];total=0
    for part in data['parts']:
        if (not isinstance(part,dict) or set(part)!={'path','sha256','bytes'} or not isinstance(part['sha256'],str)
            or not re.fullmatch('[0-9a-f]{64}',part['sha256']) or part['path']!='.intern_workspace/recovery-parts/'+part['sha256']+'.json'
            or type(part['bytes']) is not int or not 0<part['bytes']<=CHUNK):raise Invalid('Invalid recovery part reference')
        try:
            text=reader(part['path'])
            if isinstance(text,bytes):text=text.decode()
            if len(text)>CHUNK*6+4:raise Invalid('Oversized recovery part')
            chunk=json.loads(text)
            if not isinstance(chunk,str):raise Invalid('Invalid recovery part encoding')
            raw=chunk.encode()
        except (OSError,ValueError,UnicodeError,KeyError):raise Invalid('Recovery part is unavailable or malformed') from None
        if len(raw)!=part['bytes'] or hashlib.sha256(raw).hexdigest()!=part['sha256']:raise Invalid('Recovery part integrity failed')
        total+=len(raw)
        if total>MAX_TOTAL:raise Invalid('Recovery history exceeds limit')
        chunks.append(raw)
    raw=b''.join(chunks)
    if total!=data['bytes'] or hashlib.sha256(raw).hexdigest()!=data['sha256']:raise Invalid('Recovery manifest integrity failed')
    try:return json.loads(raw)
    except ValueError:raise Invalid('Recovery history is malformed') from None
