"""Private, durable file deliveries to the requesting bot's existing chat."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from .core import Invalid,git_environment
from .attachments import (MAX_BYTES,private_directory,private_path,read_regular,
                          save_atomic,validate_file)

BACKOFF=(5,30)
TRANSIENT_CODES={99991400}
HELP=('File transfer\n\nUpload files first, then send your message: file1, file2, file3, message. '
      'Uploads wait without starting Codex; your next ordinary message submits all preceding files together in this chat. '
      'PDF, Word, Markdown, TXT, images and other ordinary file attachments are accepted (maximum 20 MiB each). '
      'Codex receives private local files with their original filenames; ZIPs are checked and never extracted automatically. '
      'Your text stays unchanged. Commands do not consume waiting files. Busy sessions keep the whole file-and-message batch queued until the current turn finishes.\n'
      '/files pending — list your waiting files\n/files clear — discard your waiting files without stopping Codex\n\n'
      'To receive a result, ask: Generate the report as PDF and send it here, or: Send the results as a ZIP. '
      'Codex queues the requested artifact to this same chat using its file-transfer hook/skill.\n'
      '/send-file RELATIVE_PATH — send the current workspace file, including uncommitted results\n'
      '/send-file --committed RELATIVE_PATH — send the version saved in Git HEAD\n'
      '/files — recent transfers and delivery status\n/files permissions — check the live app\'s file permissions\n/files TRANSFER_ID — inspect one transfer\n'
      '/files retry TRANSFER_ID — retry a failed/uncertain saved snapshot after repair; check for a previous file first if uncertain\n\n'
      'Quote filenames containing spaces, or paste the full relative path. File delivery is separate from Git uploads. '
      'Incoming files are not added to Git automatically. Exports remain owner-only. '
      'Download needs application permission im:message:readonly. Upload needs im:resource:upload or im:resource. '
      'Permission changes must be published before the live bot can use them. No cloud-document/folder links or audio/video yet.')


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS file_transfers(
        id TEXT PRIMARY KEY,session TEXT NOT NULL,requested_by TEXT NOT NULL,source_seq INTEGER,
        request_key TEXT NOT NULL,name TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,
        state TEXT NOT NULL,file_key TEXT,receipt TEXT,attempts INTEGER NOT NULL DEFAULT 0,
        retry_at REAL NOT NULL DEFAULT 0,error_type TEXT,error_code INTEGER,notice_receipt TEXT,
        created_at REAL NOT NULL,updated_at REAL NOT NULL,error_reason TEXT,UNIQUE(session,request_key))''')
    if 'error_reason' not in {r[1] for r in db.execute('PRAGMA table_info(file_transfers)')}:
        db.execute('ALTER TABLE file_transfers ADD COLUMN error_reason TEXT')


def snapshot(tracker,session,relative,committed=False):
    from .workspace_tracking import WorkspaceTracking
    if not session or session['state']!='ready' or not session.get('chat_id') or not isinstance(tracker,WorkspaceTracking):
        raise Invalid('Choose a ready tracked workspace conversation')
    if not isinstance(relative,str) or not relative or '\\' in relative or any(ord(c)<32 for c in relative):
        raise Invalid('Choose a relative workspace file')
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts or private_path(path.parts):
        raise Invalid('Private configuration and paths outside the workspace cannot be exported')
    directory=Path(tracker.code_directory(session))
    if committed:
        entry=tracker.git(str(directory),'ls-tree','HEAD','--',str(path))
        if not entry.startswith(('100644 blob ','100755 blob ')):
            raise Invalid('Committed export needs a regular file present on this branch')
        size=int(tracker.git(str(directory),'cat-file','-s','HEAD:'+str(path)))
        if not 0<size<=MAX_BYTES:raise Invalid('Export must contain 1 byte to 20 MiB')
        result=subprocess.run(['git','-C',str(directory),'show','HEAD:'+str(path)],env=git_environment(),
                              capture_output=True,timeout=10,check=True)
        data=result.stdout
    else:
        # Walk with directory descriptors: a symlink replacement must not escape
        # the bound checkout between validation and opening the actual file.
        descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for component in path.parts[:-1]:
                child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
                os.close(descriptor);descriptor=child
            data=read_regular('/proc/self/fd/'+str(descriptor)+'/'+path.name)
        except OSError:raise Invalid('Export paths cannot contain symbolic links or missing directories') from None
        finally:os.close(descriptor)
    validate_file(path.name,data,credential_check=tracker.code_credentials)
    return path.name,data


def enqueue(state,tracker,session,relative,*,request_key=None,requested_by=None,source_seq=None,committed=False):
    identity=state.identity()
    current=state.db.execute("SELECT seq,sender FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal') ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone() if session else None
    sender=requested_by or (current['sender'] if current else identity['owner'])
    if not sender or sender!=identity['owner']:raise Invalid('Only the owner may export workspace files')
    if source_seq is None and current:source_seq=current['seq']
    name,data=snapshot(tracker,session,relative,committed)
    digest=hashlib.sha256(data).hexdigest()
    if request_key is None:
        # Repeated tool submissions in the same turn are idempotent. A later
        # owner turn, or a differently named artifact, can deliberately resend.
        scope=source_seq if source_seq is not None else 'local'
        request_key=str(scope)+':'+name+':'+digest
    if not isinstance(request_key,str) or not 0<len(request_key)<=256 or any(ord(c)<32 for c in request_key):
        raise Invalid('Supply a bounded stable transfer key')
    parent=private_directory(state.runtime/'file-outbox')
    ident='f_'+uuid.uuid4().hex[:16];path=parent/(ident+'.bin')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        existing=state.db.execute('SELECT * FROM file_transfers WHERE session=? AND request_key=?',(session['id'],request_key)).fetchone()
        if existing:
            if existing['sha256']!=digest or existing['name']!=name:raise Invalid('Transfer key already belongs to a different file snapshot')
            state.db.execute('COMMIT');return public(dict(existing))
        save_atomic(path,data)
        now=time.time()
        state.db.execute('''INSERT INTO file_transfers(id,session,requested_by,source_seq,request_key,name,sha256,size,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,'queued',?,?)''',(ident,session['id'],sender,source_seq,request_key,name,digest,len(data),now,now))
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK')
        if path.exists():path.unlink()
        raise
    return public(dict(state.db.execute('SELECT * FROM file_transfers WHERE id=?',(ident,)).fetchone()))


def public(row):
    # Resource keys, identities, private paths and API receipts stay local.
    value={key:row[key] for key in ('id','name','size','sha256','state','attempts','error_type','error_code','error_reason')}
    # Explain older saved failures too, without rewriting their original record.
    if not value['error_reason']:
        value['error_reason']=permission_reason(row['error_code'],'send_file' if row.get('file_key') else 'upload_file')
    return value


def permission_reason(code,operation):
    if code not in {99991672,99991676}:return None
    required={'upload_file':'im:resource:upload (or im:resource)',
              'send_file':'im:message:send_as_bot',
              'download_resource':'im:message:readonly'}.get(operation)
    if not required:return None
    next_action=('Retry the attachment download after repair.' if operation=='download_resource' else
                 'The saved file is preserved; retry its transfer after repair.')
    return ('Feishu denied '+operation+': enable application/tenant permission '+required+
            ' and publish the permission change. Keep owner-only availability. '+next_action)


def permissions(api):
    """An explicit, read-only account check; outages never prove missing scopes."""
    try:return api.file_permissions()
    except Exception as error:
        code=getattr(error,'code',None)
        return dict(schema='intern-agent.file-permissions.v1',verified=False,
                    error_type=type(error).__name__,error_code=code if type(code) is int else None,
                    next_action='Live permission check unavailable; no permission absence was inferred. Check connectivity and retry /files permissions. No app settings changed.')


def listing(state,session,ident=None):
    if not session:raise Invalid('Choose a workspace conversation')
    if ident:
        row=state.db.execute('SELECT * FROM file_transfers WHERE id=? AND session=?',(ident,session['id'])).fetchone()
        if not row:raise Invalid('Unknown file transfer in this conversation')
        return [public(dict(row))]
    return [public(dict(r)) for r in state.db.execute('SELECT * FROM file_transfers WHERE session=? ORDER BY created_at DESC LIMIT 20',(session['id'],))]


def retry(state,session,ident):
    rows=listing(state,session,ident)
    state.db.execute('BEGIN IMMEDIATE')
    try:
        changed=state.db.execute("UPDATE file_transfers SET state='queued',attempts=0,retry_at=0,error_type=NULL,error_code=NULL,error_reason=NULL,notice_receipt=NULL,updated_at=? WHERE id=? AND session=? AND state IN ('failed','uncertain')",(time.time(),ident,session['id'])).rowcount
        if not changed:raise Invalid('Only failed or uncertain transfers can be retried; delivered files are not resent')
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    return 'Retry queued for '+ident+'.'+(' The previous send was uncertain; check for a duplicate file.' if rows[0]['state']=='uncertain' else '')


def recover(state):
    # Re-uploading an unacknowledged resource creates no extra chat message.
    state.db.execute("UPDATE file_transfers SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,error_type='UploadInterrupted',updated_at=? WHERE state='uploading'",(time.time(),))
    state.db.execute("UPDATE file_transfers SET state='uncertain',error_type='SendInterrupted',updated_at=? WHERE state='sending'",(time.time(),))


def failure(state,ident,error,*,sending=False):
    row=state.db.execute('SELECT * FROM file_transfers WHERE id=?',(ident,)).fetchone()
    code=getattr(error,'code',None)
    code=code if isinstance(code,int) and not isinstance(code,bool) else None
    # A transport exception during a send is ambiguous. Never blindly repeat an
    # externally visible file after Feishu's idempotency window may have expired.
    uncertain=sending and not code
    transient=code in TRANSIENT_CODES or not sending and not isinstance(error,Invalid) and code is None
    retryable=transient and row['attempts']<=len(BACKOFF)
    phase='uncertain' if uncertain else 'queued' if retryable else 'failed'
    due=time.time()+BACKOFF[row['attempts']-1] if retryable and not uncertain else 0
    reason=(str(error)[:300] if isinstance(error,Invalid) else
            permission_reason(code,getattr(error,'operation','send_file' if sending else 'upload_file')))
    state.db.execute('UPDATE file_transfers SET state=?,retry_at=?,error_type=?,error_code=?,error_reason=?,updated_at=? WHERE id=?',
                     (phase,due,type(error).__name__,code,reason,time.time(),ident))


def deliver(relay,ident):
    state=relay.state
    state.db.execute('BEGIN IMMEDIATE')
    try:
        row=state.db.execute('SELECT * FROM file_transfers WHERE id=?',(ident,)).fetchone()
        if not row or row['state']!='queued' or row['retry_at']>time.time():state.db.execute('COMMIT');return
        session=state.session(row['session'])
        if not session or session['state'] not in {'ready','paused'} or row['requested_by']!=state.identity()['owner']:
            state.db.execute("UPDATE file_transfers SET state='cancelled',updated_at=? WHERE id=?",(time.time(),ident))
            state.db.execute('COMMIT');return
        uploading=not row['file_key']
        state.db.execute('UPDATE file_transfers SET state=?,attempts=attempts+1,updated_at=? WHERE id=?',
                         ('uploading' if uploading else 'sending',time.time(),ident))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    sending=False
    try:
        path=private_directory(state.runtime/'file-outbox')/(ident+'.bin')
        data=read_regular(path,private=True)
        if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise Invalid('File snapshot changed; transfer blocked')
        # Revalidate at delivery, including compressed archive members.
        validate_file(row['name'],data,credential_check=relay.tracker.code_credentials)
        key=row['file_key']
        if uploading:
            key=relay.api.upload_file(row['name'],data)
            if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',key):raise Invalid('Upload did not return a valid resource key')
            state.db.execute("UPDATE file_transfers SET file_key=?,state='sending',updated_at=? WHERE id=?",(key,time.time(),ident))
        sending=True
        receipt=relay.api.send_file(session['chat_id'],key,'file-transfer:'+ident)
        if not isinstance(receipt,str) or not receipt:raise Invalid('File send did not return a receipt')
        state.db.execute("UPDATE file_transfers SET state='delivered',receipt=?,error_type=NULL,error_code=NULL,error_reason=NULL,updated_at=? WHERE id=?",(receipt,time.time(),ident))
        if row['source_seq'] is not None:
            state.ledger.evidence(row['source_seq'],'Delivered file '+row['name']+'; SHA256 '+row['sha256'])
            intern=relay.tracker.intern(session)
            if intern and intern.get('metadata_mode')=='bot_branch':
                state.db.execute('''INSERT INTO bot_tracking_pending VALUES(?,NULL,0)
                    ON CONFLICT(session) DO UPDATE SET retry_at=0''',(session['id'],))
            else:
                state.db.execute('''INSERT INTO tracking_pending(workspace,error_type,retry_at) VALUES(?,NULL,0)
                    ON CONFLICT(workspace) DO UPDATE SET retry_at=0''',(session['workspace'],))
    except Exception as error:
        # Once a receipt is durable, an evidence failure cannot undo delivery.
        saved=state.db.execute('SELECT receipt FROM file_transfers WHERE id=?',(ident,)).fetchone()
        if not saved['receipt']:failure(state,ident,error,sending=sending)


def notices(relay):
    state=relay.state
    for row in state.db.execute("SELECT * FROM file_transfers WHERE state IN ('failed','uncertain') AND notice_receipt IS NULL ORDER BY created_at LIMIT 5").fetchall():
        session=state.session(row['session'])
        if not session or row['requested_by']!=state.identity()['owner']:continue
        reason=public(dict(row))['error_reason']
        message='File transfer '+row['id']+' ('+row['name']+') '+row['state']+'. '+(reason+' ' if reason else '')+('Feishu API '+str(row['error_code'])+'. ' if row['error_code'] else '')+'Use /files permissions to check live access; /files retry '+row['id']+' retries the saved snapshot.'
        if row['state']=='uncertain':message+=' Check whether the file already arrived before retrying.'
        # One notice worker owns this path. An unknown notice send is not retried.
        changed=state.db.execute("UPDATE file_transfers SET notice_receipt='sending' WHERE id=? AND notice_receipt IS NULL",(row['id'],)).rowcount
        if not changed:continue
        try:
            receipt=relay.api.send(session['chat_id'],message,'file-transfer-notice:'+row['id']+':'+str(row['attempts']))
            state.db.execute('UPDATE file_transfers SET notice_receipt=? WHERE id=?',(receipt,row['id']))
        except Exception:state.db.execute("UPDATE file_transfers SET notice_receipt='uncertain' WHERE id=?",(row['id'],))


def pump(relay):
    for row in relay.state.db.execute("SELECT id FROM file_transfers WHERE state='queued' AND retry_at<=? ORDER BY created_at LIMIT 5",(time.time(),)).fetchall():
        if relay.stop.is_set():break
        deliver(relay,row['id'])
    notices(relay)


def loop(relay):
    try:
        while not relay.stop.is_set():
            try:pump(relay)
            except Exception as error:relay.last_error=type(error).__name__
            relay.stop.wait(1)
    finally:relay.state.close()


def context_session(state,context_file):
    """A private relay-generated hook file pins the local CLI to one conversation."""
    from .live_state import private_json
    path=Path(context_file)
    parent=private_directory(state.runtime/'prompt-context')
    if path.parent!=parent or path.suffix!='.json' or path.is_symlink():raise Invalid('Use this conversation\'s relay-generated file context')
    session=state.session(path.stem)
    context=private_json(path)
    if (not session or session['state']!='ready' or context.get('thread_id')!=session['thread_id']
            or not session['thread_id']):raise Invalid('File context no longer matches this conversation')
    thread=os.environ.get('CODEX_THREAD_ID')
    if thread and thread!=session['thread_id']:raise Invalid('File context belongs to another Codex conversation')
    return session
