"""Owner-enabled, deduplicated release handoffs; never block workspace delivery."""
import fnmatch
import hashlib
import json
import time

from .core import Invalid,github_url
from .live_state import private_json,write_private
from .records import SHA

PATTERNS=['intern_personal/*.py','extension/*','templates/*','docs/*.md','skills/*',
          'tools/*.py','tests/*','README.md','distribution.json','requirements*',
          'release-*.json','source-distribution.json','CONTRIBUTING.md','docs/*.json',
          'LICENSE*','NOTICE*','.github/workflows/*','pyproject.toml','uv.lock',
          'package.json','package-lock.json','.gitignore']


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS release_jobs(
        id TEXT PRIMARY KEY,source_session TEXT NOT NULL,source_commit TEXT NOT NULL,
        fingerprint TEXT NOT NULL,repo_url TEXT NOT NULL,policy_hash TEXT NOT NULL,event_seq INTEGER UNIQUE,
        state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);''')


def policy(state):
    file=state.root/'release-policy.json'
    if not file.exists() and not file.is_symlink():return None
    data=private_json(file)
    if (not isinstance(data,dict) or set(data)!={'schema','source','operator','repo_url','enabled','license'}
            or data['schema']!='intern-agent.release-policy.v1' or type(data['enabled']) is not bool
            or data['license'] not in {'MIT','Apache-2.0'} or github_url(data['repo_url'])!=data['repo_url']):raise Invalid('Invalid private release policy')
    return data


def configure(state,source,operator,repo_url,license='MIT'):
    from .workspace_tracking import WorkspaceTracking
    if license not in {'MIT','Apache-2.0'}:raise Invalid('Choose a supported public source license')
    if not state.identity()['owner']:raise Invalid('Pair the owner before enabling automatic releases')
    tracker=WorkspaceTracking(state)
    for sid in [source,operator]:
        s=state.session(sid)
        if not s or s['state']!='ready' or not tracker.intern(s) or not tracker.intern(s)['published']:raise Invalid('Choose registered ready source and release conversations')
    if source==operator:raise Invalid('The release operator must be separate from the development conversation')
    from .peer_mail import manual
    if manual(state,state.session(operator)):raise Invalid('Choose a dedicated managed release operator; manual Debug stays manual')
    destination=github_url(repo_url)
    if any(tracker.binding(state.session(sid)['workspace'])['repo_url'].casefold()==destination.casefold() for sid in [source,operator]):
        raise Invalid('Use a separate public product repository; private development and tracking repositories stay private')
    receipts=state.runtime/'release-receipts';receipts.mkdir(mode=0o700,exist_ok=True)
    if receipts.is_symlink() or receipts.stat().st_mode&0o077:raise Invalid('Release receipts need private storage')
    write_private(state.root/'release-policy.json',dict(schema='intern-agent.release-policy.v1',source=source,operator=operator,
        repo_url=destination,enabled=True,license=license))
    return dict(enabled=True,publications_delegated=True)


def fingerprint(tracker,directory,head):
    entries=[]
    for item in tracker.git(directory,'ls-tree','-r','-z','--full-tree',head).split('\0'):
        if not item:continue
        header,name=item.split('\t',1);mode,kind,oid=header.split()
        if any(fnmatch.fnmatchcase(name,pattern) for pattern in PATTERNS):
            if mode not in {'100644','100755'} or kind!='blob':raise Invalid('Linked release input is not supported')
            entries.append((name,oid))
    if not entries:raise Invalid('No maintained release inputs found')
    return hashlib.sha256(json.dumps(sorted(entries),separators=(',',':')).encode()).hexdigest()


def poll(relay):
    state=relay.state
    for phase,states in [('operator_completed',('complete',)),('running',('running','awaiting_terminal','result_ready','preparing_reply','sending')),
                         ('needs_review',('uncertain_execution','uncertain_delivery','record_blocked','sync_pending')),('cancelled',('cancelled','cancelled_policy_changed'))]:
        state.db.execute('UPDATE release_jobs SET state=?,updated_at=? WHERE state!=? AND event_seq IN (SELECT seq FROM events WHERE state IN ('+
            ','.join('?' for _ in states)+'))',(phase,time.time(),phase,*states))
    settings=policy(state)
    if not settings or not settings['enabled']:return
    source=state.session(settings['source']);operator=state.session(settings['operator'])
    if not source or not operator or operator['state']!='ready':return
    tracker=relay.tracker;intern=tracker.intern(source)
    if not intern or not intern['published']:return
    directory=tracker.code_directory(source);head=tracker.git(directory,'rev-parse','HEAD')
    if not SHA.fullmatch(head):raise Invalid('Invalid release source commit')
    content=fingerprint(tracker,directory,head)
    policy_hash=hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()
    # A records-only commit does not rebuild the same public package.
    if state.db.execute('SELECT 1 FROM release_jobs WHERE source_session=? AND repo_url=? AND fingerprint=? AND policy_hash=?',
                        (source['id'],settings['repo_url'],content,policy_hash)).fetchone():return
    remote=tracker.git(directory,'ls-remote','--heads','origin','refs/heads/'+intern['code_branch'],network=True)
    if not remote or remote.split()[0]!=head:return  # Never release unpublished source.
    identity=state.identity()
    if not identity['owner'] or not operator['chat_id'] or not operator['thread_id']:return
    job='release_'+hashlib.sha256((source['id']+settings['repo_url']+content+policy_hash).encode()).hexdigest()[:24]
    text=('Owner-authorized release synchronization from the development conversation. '
        'The owner requires a separate installable public product containing maintained source, VSIX and Codex skills, '
        'and requires future development features to produce an updated VSIX there. '
        'Release source commit '+head+' from '+directory+' into '+settings['repo_url']+' using '+settings['license']+'. '
        'Follow docs/PUBLISHED_RELEASE.md and docs/PORTABLE_RECOVERY.md from that exact source. '
        'Use your own registered worktree/branch and a clean allowlisted export. Never publish this private tracking repository or its history. '
        'Exclude credentials, PATs, .intern_workspace, private state, logs, machine/account/chat identifiers and enterprise reference snapshots. '
        'Create the separate public repository if absent; initialize it only from the clean export. '
        'Build and verify the installable VSIX, recovery/file skills, source archive, checksum/provenance manifest and automated release workflow. '
        'Publish a GitHub release with those artifacts and verify the uploaded hashes. Use fresh versions; never overwrite an existing release. '
        'No PR, merge, existing-private-repository visibility change, Marketplace publication, account permission change, '
        'relay service change or research-job action. The owner authorized this public release, superseding earlier private-review-only limits for the clean new destination. '
        'Report real release URLs, artifact hashes, source commit, installation verification and any concrete blocker in your Feishu chat. '
        'Write the same secret-free result as owner-private JSON to '+str(state.runtime/'release-receipts'/(head+'.json'))+'. '
        'Do not send an automatic task or peer note to Debug Bot; it is for manual debugging.')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        inserted=state.db.execute('INSERT OR IGNORE INTO release_jobs VALUES(?,?,?,?,?,?,NULL,?,?,?)',
            (job,source['id'],head,content,settings['repo_url'],policy_hash,'queued',time.time(),time.time())).rowcount
        if inserted:
            cursor=state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?, 'agent',?,?,?,'queued')''',(identity['app_id'],job,operator['chat_id'],identity['owner'],operator['workspace'],operator['id'],text))
            seq=cursor.lastrowid
            state.ledger.ensure_turn(dict(state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()),time.time(),'local')
            state.db.execute('UPDATE release_jobs SET event_seq=? WHERE id=?',(seq,job))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise


def status(state):
    return [dict(row) for row in state.db.execute('''SELECT j.source_commit,j.repo_url,j.state,e.state AS execution_state,
        EXISTS(SELECT 1 FROM events x WHERE x.seq=j.event_seq AND x.receipt IS NOT NULL) AS operator_reply_delivered
        FROM release_jobs j LEFT JOIN events e ON e.seq=j.event_seq ORDER BY j.created_at DESC LIMIT 20''')]


def guard(relay,item):
    job=relay.state.db.execute('SELECT * FROM release_jobs WHERE event_seq=?',(item['seq'],)).fetchone()
    if not job:return
    current=policy(relay.state)
    if (not current or not current['enabled'] or current['source']!=job['source_session']
            or current['operator']!=item['session'] or current['repo_url']!=job['repo_url']
            or hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()!=job['policy_hash']):
        from .auto_recovery import RecoverySkipped
        raise RecoverySkipped('Automatic release policy changed; this undispatched handoff was cancelled')


def loop(relay):
    while not relay.stop.is_set():
        try:poll(relay);relay.release_error_type=None
        except Exception as error:relay.release_error_type=type(error).__name__
        if relay.stop.wait(30):break
