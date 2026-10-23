"""Private before-images for a bot's own drafts; never rewrite old baselines."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .core import Invalid
from .live_state import private_json,write_private

MAX_FILE=10*1024*1024
MAX_TOTAL=64*1024*1024
MAX_FILES=512


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS draft_baselines(seq INTEGER PRIMARY KEY,session TEXT NOT NULL,
        directory TEXT NOT NULL,branch TEXT NOT NULL,baseline_hash TEXT NOT NULL,manifest TEXT NOT NULL,created_at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS draft_reviews(seq INTEGER PRIMARY KEY,owner_seq INTEGER NOT NULL,
        baseline_hash TEXT NOT NULL,manifest TEXT NOT NULL,created_at REAL NOT NULL)''')


def fingerprint(baseline):return hashlib.sha256(json.dumps(baseline,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def owned(tracker,session):
    intern=tracker.intern(session)
    if not intern or intern.get('metadata_mode')!='bot_branch':return False
    root=Path(intern['code_path']).resolve()
    if not root.is_relative_to((tracker.state.runtime/'worktrees').resolve()):return False
    rows=tracker.state.db.execute('SELECT session,code_path FROM tracked_interns').fetchall()
    return all(row['session']==session['id'] for row in rows if Path(row['code_path']).resolve()==root)


def root(state):
    path=state.runtime/'draft-baselines';path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.is_symlink() or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:raise Invalid('Draft checkpoint directory must be private')
    return path


def file_path(directory,name):
    if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts or '\0' in name:raise Invalid('Invalid draft path')
    directory=Path(directory).resolve();path=directory/name
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(directory) and p!=directory):raise Invalid('Linked draft cannot be checkpointed')
    return path


def put(state,data):
    digest=hashlib.sha256(data).hexdigest();directory=root(state)/'blobs';directory.mkdir(mode=0o700,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode&0o077:raise Invalid('Draft object directory must be private')
    path=directory/digest
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.stat().st_mode&0o077 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise Invalid('Draft checkpoint integrity failed')
        return digest
    fd,temp=tempfile.mkstemp(prefix='.draft-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:Path(temp).unlink(missing_ok=True)
    return digest


def get(state,digest):
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise Invalid('Invalid draft checkpoint digest')
    path=root(state)/'blobs'/digest
    if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077 or path.stat().st_size>MAX_FILE:raise Invalid('Draft before-image is missing or unsafe')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:raise Invalid('Draft before-image integrity failed')
    return data


def capture(tracker,session,item,baseline):
    if not owned(tracker,session):return
    from .peer_mail import binding
    if binding(tracker.state,item['seq'])[1]=='request':return
    if any(name and not name.startswith('.intern_workspace/') for name in baseline['staged']):
        raise Invalid('Pre-existing staged code needs review before Codex starts; its contents were preserved')
    state=tracker.state;directory=tracker.code_directory(session);intern=tracker.intern(session)
    old=state.db.execute('SELECT * FROM draft_baselines WHERE seq=?',(item['seq'],)).fetchone()
    if old:
        if old['baseline_hash']!=fingerprint(baseline):raise Invalid('The saved starting draft checkpoint changed before dispatch')
        return
    images={};skipped=0;total=0
    def priority(name):
        source=Path(name).suffix.lower() in {'.md','.py','.txt','.toml','.yaml','.yml','.sh','.js','.ts','.tsx','.json'}
        return (not source,len(Path(name).parts),name)
    for name in sorted(baseline['dirty'],key=priority):
        if name.startswith('.intern_workspace/'):continue
        if len(images)>=MAX_FILES:skipped+=1;continue
        if any(p in {'.git','.codex','.ssh','.env','runtime','__pycache__'} for p in Path(name).parts):skipped+=1;continue
        try:path=file_path(directory,name)
        except Invalid:skipped+=1;continue
        digest=baseline['dirty'][name]
        if not path.exists():
            if digest=='absent-or-linked':images[name]=dict(missing=True)
            continue
        if not path.is_file() or path.stat().st_size>MAX_FILE or total+path.stat().st_size>MAX_TOTAL:skipped+=1;continue
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as stream:data=stream.read(MAX_FILE+1)
        if len(data)>MAX_FILE or hashlib.sha256(data).hexdigest()!=digest:raise Invalid('A starting draft changed while its before-image was being saved; Codex was not started')
        images[name]=dict(sha256=put(state,data));total+=len(data)
    manifest=root(state)/('turn_'+str(item['seq'])+'.json')
    write_private(manifest,dict(schema='intern-agent.draft-baseline.v1',session=session['id'],directory=str(Path(directory).resolve()),
        branch=intern['code_branch'],baseline_hash=fingerprint(baseline),files=images,skipped=skipped,bytes=total))
    state.db.execute('INSERT INTO draft_baselines VALUES(?,?,?,?,?,?,?)',
        (item['seq'],session['id'],str(Path(directory).resolve()),intern['code_branch'],fingerprint(baseline),str(manifest),time.time()))


def recover(tracker,session,item,copies):
    """Recover old before-images only when bytes match the original saved hash."""
    state=tracker.state;record=state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
    if not owned(tracker,session) or not record or not record['baseline']:raise Invalid('Before-image recovery needs an existing owned-worktree baseline')
    if state.db.execute('SELECT 1 FROM draft_baselines WHERE seq=?',(item['seq'],)).fetchone():raise Invalid('The starting draft checkpoint is immutable')
    baseline=json.loads(record['baseline']);images={};directory=tracker.code_directory(session)
    for name,copy in copies.items():
        if name not in baseline['dirty']:raise Invalid('Recovered path is not in the original baseline')
        path=Path(copy)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_FILE:raise Invalid('Unsafe recovery copy')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=baseline['dirty'][name]:raise Invalid('Recovery copy does not match the original starting hash')
        images[name]=dict(sha256=put(state,data))
    manifest=root(state)/('turn_'+str(item['seq'])+'.json')
    write_private(manifest,dict(schema='intern-agent.draft-baseline.v1',session=session['id'],directory=str(Path(directory).resolve()),
        branch=tracker.intern(session)['code_branch'],baseline_hash=fingerprint(baseline),files=images,recovered=True))
    state.db.execute('INSERT INTO draft_baselines VALUES(?,?,?,?,?,?,?)',
        (item['seq'],session['id'],str(Path(directory).resolve()),tracker.intern(session)['code_branch'],fingerprint(baseline),str(manifest),time.time()))
    state.ledger.evidence(item['seq'],'Recovered starting draft bytes from existing backups; every before-image matches the immutable original baseline SHA256. No prompt replay or baseline rewrite.')


def review(tracker,session,item,baseline):
    row=tracker.state.db.execute('SELECT * FROM draft_reviews WHERE seq=?',(item['seq'],)).fetchone()
    if not row:return None
    data=private_json(row['manifest'])
    if not owned(tracker,session) or row['baseline_hash']!=fingerprint(baseline) or data['session']!=session['id']:raise Invalid('Draft review no longer matches this turn or its own workspace')
    directory=tracker.code_directory(session)
    for name,digest in data['files'].items():
        path=file_path(directory,name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise Invalid('An owner-reviewed draft changed again; preserve it for another review')
        get(tracker.state,digest)
    return data


def check(tracker,session,item,baseline,changes):
    if not changes:return
    approved=review(tracker,session,item,baseline)
    if approved and changes<=set(approved['files']):return
    row=tracker.state.db.execute('SELECT * FROM draft_baselines WHERE seq=?',(item['seq'],)).fetchone()
    if row and owned(tracker,session) and row['session']==session['id'] and row['directory']==str(Path(tracker.code_directory(session)).resolve()) and row['branch']==tracker.intern(session)['code_branch'] and row['baseline_hash']==fingerprint(baseline):
        data=private_json(row['manifest']);files=data['files']
        if (data['baseline_hash']!=row['baseline_hash'] or data['session']!=session['id'] or data['directory']!=row['directory']
                or data['branch']!=row['branch']):raise Invalid('Starting draft manifest does not match its immutable baseline')
        for name in changes:
            before=files.get(name)
            if not before:break
            if before.get('missing'):
                if baseline['dirty'][name]!='absent-or-linked':break
            elif before.get('sha256')!=baseline['dirty'][name]:break
            else:get(tracker.state,before['sha256'])
        else:return
    names=', '.join(sorted(changes)[:5])
    raise Invalid('Pre-existing drafts changed without recoverable before-images: '+names+'. This is a local review, not a network retry.')


def approve_current(tracker,item,owner,paths,note):
    """Operator review of exact current bytes, with the original review retained."""
    state=tracker.state;event=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone())
    authority=state.db.execute('SELECT * FROM events WHERE seq=?',(owner['seq'],)).fetchone()
    if (not authority or authority['seq']<=item['seq'] or authority['state'] not in {'running','awaiting_terminal'}
            or authority['sender']!=state.identity()['owner'] or not authority['message_id'].startswith('om_') or not state.authorized(dict(authority))):
        raise Invalid('Draft review requires a current authenticated owner request')
    if os.environ.get('CODEX_THREAD_ID') and state.session(authority['session'])['thread_id']!=os.environ['CODEX_THREAD_ID']:raise Invalid('Draft operator does not match the authorizing owner conversation')
    session=state.session(event['session']);record=dict(state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())
    if not owned(tracker,session) or event['state']!='record_blocked' or record['activity']!='completed' or not event['result'] or not record['baseline']:raise Invalid('Only a completed saved turn in its own bot worktree can receive a draft review')
    if not note.strip() or not paths or len(paths)>MAX_FILES:raise Invalid('An explicit bounded file review and explanation are required')
    baseline=json.loads(record['baseline']);directory=tracker.code_directory(session);files={}
    if tracker.git(directory,'diff','--cached','--name-only'):raise Invalid('Preserve staged work before approving a draft publication')
    for name in sorted(set(paths)):
        if name.startswith('.intern_workspace/'):raise Invalid('Review code separately from generated records')
        path=file_path(directory,name)
        if not path.is_file() or path.stat().st_size>MAX_FILE:raise Invalid('Only bounded regular draft files can be reviewed')
        data=path.read_bytes()
        from .git_hooks import validate
        validate(name,data,False)
        if tracker.code_credentials(data.decode('utf-8',errors='ignore')):raise Invalid('Private values cannot be approved for upload')
        files[name]=put(state,data)
    manifest=root(state)/('review_'+str(item['seq'])+'.json')
    if state.db.execute('SELECT 1 FROM draft_reviews WHERE seq=?',(item['seq'],)).fetchone():raise Invalid('This saved draft review is immutable')
    write_private(manifest,dict(schema='intern-agent.draft-review.v1',session=session['id'],owner_seq=owner['seq'],files=files,note=note,
        original_event=event,original_record=record,original_baseline_hash=fingerprint(baseline),original_before_images_known=False))
    state.db.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?)',(item['seq'],owner['seq'],fingerprint(baseline),str(manifest),time.time()))
    state.ledger.evidence(item['seq'],'Current owner-requested draft review preserved the original review/baseline and exact current bytes privately. '+note)
    return dict(files=len(files),original_review_preserved=True)
