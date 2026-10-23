"""Attach an existing tmux TUI without starting/resuming a competing Codex."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from .core import Invalid
from .live_state import private_json, write_private
from .turn_limits import deadline as turn_deadline


# A supported 20 MiB file/image can exceed 4 MiB as a JSON/base64 tool record.
# Keep observation bounded without losing the completion behind that record.
MAX_LINE = 32*1024*1024


def native_environment_message(payload):
    """Recognize Codex-generated context, never an owner's lookalike text."""
    metadata=payload.get('internal_chat_message_metadata_passthrough')
    content=payload.get('content',[])
    if (not isinstance(metadata,dict)
            or metadata.get('content_item_kinds')!=['environments.environment_context']
            or len(content)!=1 or content[0].get('type')!='input_text'):
        return False
    text=content[0].get('text','').strip()
    return (text.startswith('<environment_context>') and text.endswith('</environment_context>')
            and text.count('<environment_context>')==1 and text.count('</environment_context>')==1)


def native_context_message(payload):
    """Skip provenance-tagged relay references, never owner lookalike text."""
    if native_environment_message(payload):return True
    metadata=payload.get('internal_chat_message_metadata_passthrough')
    content=payload.get('content',[])
    if (not isinstance(metadata,dict) or len(content)!=1 or content[0].get('type')!='input_text'):
        return False
    kinds=metadata.get('content_item_kinds')
    from .prompt_context import CONTEXT_KEYS
    if not isinstance(kinds,list) or len(kinds)!=1 or kinds[0] not in {'additional_content.'+key for key in CONTEXT_KEYS}:
        return False
    key=kinds[0].removeprefix('additional_content.')
    opening='<external_'+key+'>';closing='</external_'+key+'>'
    text=content[0].get('text','').strip()
    return text.startswith(opening) and text.endswith(closing) and text.count(opening)==1 and text.count(closing)==1


def tmux(config, *args, data=None, timeout=10):
    p=subprocess.run(['/usr/bin/tmux','-S',config['tmux_socket'],*args],input=data,
                     capture_output=True,timeout=timeout)
    if p.returncode:raise Invalid('Attached tmux pane is unavailable; no replacement is launched')
    return p.stdout.decode('utf-8',errors='replace')


def verify_pane(config,timeout=10):
    value=tmux(config,'display-message','-p','-t',config['pane'],
               '#{pane_id}|#{pane_pid}|#{pane_current_command}|#{pane_dead}',timeout=timeout).strip().split('|')
    if len(value)!=4 or value[0]!=config['pane'] or value[1]!=str(config['pane_pid']) or value[3]!='0':
        raise Invalid('Attached pane identity changed')
    if config.get('pane_command') and value[2]!=config['pane_command']:
        raise Invalid('Another program is foreground in the attached pane')
    # Pin the actual Codex process too: pane IDs can outlive a process/command.
    pid=config['codex_pid']
    try:
        process=Path('/proc')/str(pid)
        stat=process.joinpath('stat').read_text().rsplit(')',1)[1].split()
        args=process.joinpath('cmdline').read_bytes().split(b'\0')
        uid=process.stat().st_uid
    except OSError:raise Invalid('Attached Codex process ended') from None
    if (uid!=os.getuid() or stat[19]!=config['codex_start'] or not any(b'codex' in x for x in args)
            or stat[0] not in {'R','S','I'} or stat[2]!=stat[5] or stat[5]=='-1'):
        raise Invalid('Attached Codex process identity changed')
    env=process.joinpath('environ').read_bytes().split(b'\0')
    thread_env=[x for x in env if x.startswith(b'CODEX_THREAD_ID=')]
    if thread_env and thread_env!=[('CODEX_THREAD_ID='+config['thread_id']).encode()]:
        raise Invalid('Attached process has another conversation identity')
    # The launcher may assign the ID after startup. In all cases verify native
    # process ancestry and the pinned rollout; never target a background shell.
    cursor=pid
    for _ in range(20):
        if cursor==config['pane_pid']:break
        cursor=int(Path('/proc',str(cursor),'stat').read_text().rsplit(')',1)[1].split()[1])
    else:raise Invalid('Attached Codex is outside the pinned pane')
    return True


def prompt_is_empty(text):
    # Fail closed for user drafts, menus, approval screens, unknown UI versions.
    ansi=re.compile(r'\x1b\[([0-9;]*)m')
    prompts=[line for line in text.splitlines() if re.match(r'^\s*[›❯>]',ansi.sub('',line))]
    if not prompts:return False
    tail=prompts[-1].split('›',1)[-1] if '›' in prompts[-1] else re.split('[❯>]',prompts[-1],maxsplit=1)[-1]
    # This TUI displays its empty-input hint with SGR dim (2). Typed text is
    # not dim. No placeholder strings or personal message content are stored.
    dim=False;cursor=0
    for match in ansi.finditer(tail):
        if tail[cursor:match.start()].strip() and not dim:return False
        codes=[int(x) for x in (match[1] or '0').split(';')]
        for code in codes:
            if code in {0,22}:dim=False
            elif code==2:dim=True
        cursor=match.end()
    return not tail[cursor:].strip() or dim


def empty_prompt(config):
    text=tmux(config,'capture-pane','-e','-p','-t',config['pane'],'-S','-20',timeout=1)
    if not prompt_is_empty(text):
        raise Invalid('Terminal input is not empty; finish/clear the draft in this pane first')


class TerminalBridge:
    def __init__(self,state,tracker,table='terminal_attachments'):
        if table not in {'terminal_attachments','console_views'}:raise Invalid('Unknown terminal observation table')
        self.table=table
        self.state,self.tracker=state,tracker
        self.lock=threading.RLock()
        self.input_lock=threading.Lock()
        self.stop=threading.Event()
        self.last_error=None
        self.state.db.execute('''CREATE TABLE IF NOT EXISTS observation_faults(
            session TEXT PRIMARY KEY,first_at REAL NOT NULL,error_type TEXT NOT NULL,alerted INTEGER NOT NULL DEFAULT 0)''')

    def binding(self,session):
        if not session:return None
        row=self.state.db.execute('SELECT * FROM '+self.table+' WHERE session=?',(session['id'],)).fetchone()
        return dict(row) if row else None

    def config(self,session):
        binding=self.binding(session)
        if not binding:raise Invalid('No terminal attachment')
        config=private_json(binding['config_path'])
        if config.get('thread_id')!=session['thread_id']:raise Invalid('Attached conversation identity changed')
        return config

    def poll(self,session):
        with self.lock:
            binding=self.binding(session);config=self.config(session)
            path=Path(config['rollout'])
            info=path.stat()
            if info.st_uid!=os.getuid() or info.st_ino!=config['rollout_inode'] or info.st_size<binding['cursor']:
                raise Invalid('Transcript replaced/truncated; attachment needs review')
            snapshot=json.loads(binding['snapshot'])
            from . import throughput
            telemetry=throughput.load_view(self.state,session,self.table,binding['cursor'],config['rollout_inode'])
            if not telemetry or telemetry.get('version')!=1:
                telemetry=throughput.bootstrap(path,binding['cursor'])
                if snapshot.get('active') and (telemetry.get('active') or {}).get('turn')!=snapshot['active']:
                    throughput.begin(telemetry,snapshot['active'])
                    telemetry['active']['baseline']=None
            from .quota import normalize, snapshot as quota_snapshot
            quota = quota_snapshot(self.state, session)
            quota_updates = []
            cursor=binding['cursor'];completed=[];commentary=[];starts=[]
            with path.open('rb') as stream:
                stream.seek(cursor)
                for _ in range(2000):
                    line=stream.readline(MAX_LINE+1)
                    if not line:break
                    if len(line)>MAX_LINE:raise Invalid('Transcript record exceeds supported size')
                    if not line.endswith(b'\n'):break
                    value=json.loads(line);payload=value.get('payload',{});kind=payload.get('type')
                    metric=throughput.native(telemetry,value)
                    if value.get('type')=='event_msg':
                        if kind == 'token_count':
                            limits = normalize(payload)
                            if limits is not None:
                                from .speed import observation_time
                                at = observation_time(value.get('timestamp'))
                                if at >= quota[1]:
                                    quota = (limits, at)
                                    quota_updates.append((payload, at))
                        elif kind=='thread_settings_applied':
                            if payload.get('thread_id')!=session['thread_id']:raise Invalid('Transcript thread mismatch')
                            settings=payload.get('thread_settings',{})
                            snapshot['model']=settings.get('model');snapshot['effort']=settings.get('reasoning_effort')
                            if 'service_tier' in settings:
                                from .speed import save_observed,observation_time
                                at=observation_time(value.get('timestamp'))
                                if at>=snapshot.get('service_tier_at',0):
                                    snapshot['service_tier']=settings['service_tier'];snapshot['service_tier_at']=at
                                    save_observed(self.state,session,settings['service_tier'],value.get('timestamp'))
                        elif kind=='task_started':
                            turn=payload.get('turn_id')
                            if not isinstance(turn,str) or not turn:raise Invalid('Unsupported start-event schema')
                            snapshot.update(active=turn,request='',event_seq=None)
                            from .speed import observation_time
                            starts.append((turn,observation_time(value.get('timestamp')) or time.time()))
                        elif kind in {'task_complete','turn_aborted'}:
                            turn=payload.get('turn_id')
                            if not turn or snapshot.get('active')!=turn:raise Invalid('Unsupported completion-event sequence')
                            result=payload.get('last_agent_message') if kind=='task_complete' else 'The terminal turn was interrupted.'
                            if kind=='task_complete' and payload.get('error'):
                                from .turn_outcomes import failed_notice
                                result=failed_notice(payload['error'])
                                kind='task_failed'
                            if not isinstance(result,str) or not result.strip():raise Invalid('Completion has no supported final response')
                            if len(result.encode())>1048576:raise Invalid('Terminal reply is too large')
                            from .speed import observation_time
                            completed.append((turn,snapshot.get('event_seq'),snapshot.get('request',''),result,kind,quota,payload.get('error'),observation_time(value.get('timestamp')) or time.time(),metric))
                            snapshot.update(active=None,request='',event_seq=None)
                    elif value.get('type')=='response_item' and kind=='message' and payload.get('role')=='user':
                        text='\n'.join(x.get('text','') for x in payload.get('content',[]) if x.get('type')=='input_text')
                        pending=snapshot.get('pending')
                        if pending:
                            if pending.get('prompt_sha256'):
                                # Only bytes observed after the durable dispatch
                                # boundary can acknowledge an unchanged message.
                                matched=(cursor >= pending['cursor'] and snapshot.get('active')
                                         and (hashlib.sha256(text.encode()).hexdigest()==pending['prompt_sha256']
                                              or pending.get('ack_sha256')==hashlib.sha256(text.replace('\r\n','\n').strip().encode()).hexdigest()))
                                if cursor >= pending['cursor'] and snapshot.get('active') and not matched:
                                    if native_context_message(payload):
                                        cursor=stream.tell();continue
                                    raise Invalid('Terminal input differed from the dispatched message; reconcile before continuing')
                            else:
                                # Read already-dispatched legacy wrappers after upgrade.
                                matched=bool(pending.get('tag') and pending['tag'] in text)
                            if matched:
                                snapshot['event_seq']=pending['seq'];snapshot['pending']=None
                            elif native_context_message(payload):
                                cursor=stream.tell();continue
                        elif native_context_message(payload):
                            cursor=stream.tell();continue
                        if snapshot.get('active'):
                            prior=snapshot.get('request','')
                            snapshot['request']=((prior+'\n' if prior else '')+text)[-32768:]
                    # Observe public prose independently of tool hooks. Only the
                    # canonical response_item is used, not its agent_message echo.
                    from .progress import native_text
                    text=native_text(value)
                    if text and snapshot.get('active') and cursor>=snapshot.get('progress_after',0) and not (self.table=='console_views' and snapshot.get('event_seq') is not None):
                        commentary.append((snapshot['active'],snapshot.get('event_seq'),payload.get('id') or str(cursor),text))
                    cursor=stream.tell()
            self.state.db.execute('BEGIN IMMEDIATE')
            try:
                from .quota import observe, freeze
                from .auto_recovery import observe as observe_outcome
                for payload, at in quota_updates:
                    observe(self.state, session, payload, observed_at=at)
                from .progress import enqueue,finish
                for turn,seq,item_id,text in commentary:
                    try:enqueue(self.state,session,turn,item_id,text,source='native',event_seq=seq)
                    except Exception:pass
                for turn,seq,request,result,kind,observed_quota,error,at,metric in completed:
                    finish(self.state,session,turn)
                    exists=self.state.db.execute('SELECT 1 FROM terminal_turns WHERE session=? AND turn_id=?',(session['id'],turn)).fetchone()
                    if exists:continue
                    if seq is None:
                        identity=self.state.identity()
                        key='local_terminal_'+hashlib.sha256((session['id']+turn).encode()).hexdigest()
                        self.state.db.execute('''INSERT OR IGNORE INTO events
                            (app_id,message_id,chat_id,sender,role,workspace,session,text,state,result)
                            VALUES(?,?,?,?,'agent',?,?,?,'result_ready',?)''',
                            (identity['app_id'],key,session['chat_id'],identity['owner'],session['workspace'],session['id'],
                             self.tracker.clean(request),self.tracker.clean(result)))
                        seq=self.state.db.execute('SELECT seq FROM events WHERE app_id=? AND message_id=?',(identity['app_id'],key)).fetchone()[0]
                        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone())
                        self.state.ledger.ensure_turn(item,time.time(),'terminal')
                        self.state.ledger.bind_turn(item)
                    self.state.db.execute('INSERT INTO terminal_turns(session,turn_id,event_seq,result,status) VALUES(?,?,?,?,?)',
                                          (session['id'],turn,seq,result,kind))
                    if kind=='task_failed':
                        self.state.db.execute("INSERT OR REPLACE INTO event_failures VALUES(?,'execution_failed',?)",(seq,time.time()))
                    observe_outcome(self.state,session,turn,{'task_complete':'completed','task_failed':'failed','turn_aborted':'interrupted'}[kind],error,seq,at=at)
                    freeze(self.state, seq, observed_quota)
                    throughput.save(self.state,session,seq,metric,'transcript')
                    # An interrupted receiver cannot replay a dispatched turn.
                    # A subsequently observed completion supplies a saved result
                    # for explicit /resolve deliver, never an automatic rerun.
                    self.state.db.execute("UPDATE events SET result=?,state=? WHERE seq=? AND state='uncertain_execution'",
                                          (result,'result_ready' if kind=='task_failed' else 'uncertain_delivery',seq))
                    self.state.db.execute("UPDATE events SET result=?,state='result_ready' WHERE seq=? AND state='awaiting_terminal'",(result,seq))
                for turn,at in starts:observe_outcome(self.state,session,turn,'inProgress',at=at)
                throughput.cache(self.state,session,telemetry['previous'],telemetry['at'])
                throughput.save_view(self.state,session,self.table,cursor,config['rollout_inode'],telemetry)
                self.state.db.execute('UPDATE '+self.table+' SET cursor=?,snapshot=? WHERE session=?',
                                      (cursor,json.dumps(snapshot),session['id']))
                if self.table=='terminal_attachments' and snapshot.get('model') and snapshot.get('effort'):
                    self.state.set_model(session['id'],snapshot['model'],snapshot['effort'])
                self.state.db.execute('COMMIT')
            except BaseException:self.state.db.execute('ROLLBACK');raise
            for turn,seq,request,result,kind,observed_quota,error,at,metric in completed:
                found=self.state.db.execute('SELECT event_seq FROM terminal_turns WHERE session=? AND turn_id=?',(session['id'],turn)).fetchone()
                if found:
                    self.state.ledger.evidence(found['event_seq'],'Observed bound Codex transcript '+kind+' for turn '+turn)
                    item=self.state.db.execute('SELECT * FROM events WHERE seq=?',(found['event_seq'],)).fetchone()
                    if item:self.state.ledger.update(dict(item),activity={'turn_aborted':'stopped','task_failed':'failed'}.get(kind,'completed'))
            return snapshot

    def run(self,session,text,event_seq,resource_context=''):
        config=self.config(session)
        deadline=turn_deadline(self.state.config())
        dispatched=False
        while not self.stop.is_set() and time.monotonic()<deadline:
            with self.lock:
                try:snapshot=self.poll(session)
                except Invalid:
                    if dispatched:raise RuntimeError('Dispatched terminal result needs review') from None
                    raise
                if not dispatched and not snapshot.get('active') and not snapshot.get('pending'):
                    if self.state.ledger.stopped(event_seq):return 'Cancelled before execution.'
                    if self.state.session(session['id'])['state']!='ready':raise Invalid('Attached session is paused')
                    from .prompt_context import publish
                    publish(self.tracker,session,resource_context)
                    # No prompt in argv, shell command, tmux buffer listing or logs.
                    buffer='ia-debug-'+str(event_seq)
                    # This transport-only lock orders Enter before Escape.
                    # Git and transcript scanning are outside it.
                    with self.input_lock:
                        if self.state.ledger.stopped(event_seq):return 'Cancelled before execution.'
                        verify_pane(config,timeout=1);empty_prompt(config)
                        snapshot['pending']={'seq':event_seq,'prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),
                                             'ack_sha256':hashlib.sha256(text.replace('\r\n','\n').strip().encode()).hexdigest(),
                                             'cursor':self.binding(session)['cursor']}
                        self.state.db.execute('UPDATE terminal_attachments SET snapshot=? WHERE session=?',(json.dumps(snapshot),session['id']))
                        try:
                            tmux(config,'load-buffer','-b',buffer,'-',data=text.encode(),timeout=1)
                            verify_pane(config,timeout=1)
                            tmux(config,'paste-buffer','-p','-d','-b',buffer,'-t',config['pane'],timeout=1)
                            tmux(config,'send-keys','-t',config['pane'],'Enter',timeout=1)
                        except Exception:
                            raise RuntimeError('Terminal dispatch needs explicit reconciliation') from None
                        finally:
                            subprocess.run(['/usr/bin/tmux','-S',config['tmux_socket'],'delete-buffer','-b',buffer],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1)
                        dispatched=True
                result=self.state.db.execute('SELECT result FROM terminal_turns WHERE session=? AND event_seq=?',(session['id'],event_seq)).fetchone()
                if result:return result['result']
            self.stop.wait(0.3)
        raise RuntimeError('Attached terminal task needs reconciliation; no automatic replay')

    def interrupt(self,session):
        # Do not acquire the watcher/dispatch lock or scan the transcript here.
        # Dispatch can be waiting for Git; stopping must bypass that work.
        with self.input_lock:
            binding=self.binding(session)
            snapshot=json.loads(binding['snapshot'])
            if not snapshot.get('active') and not snapshot.get('pending'):raise Invalid('Attached terminal has no active turn')
            config=self.config(session);verify_pane(config,timeout=1)
            tmux(config,'send-keys','-t',config['pane'],'Escape',timeout=1)

    def selection(self,session):
        row=self.binding(session);snapshot=json.loads(row['snapshot'])
        return {'model':snapshot.get('model') or 'terminal-controlled','effort':snapshot.get('effort') or 'terminal-controlled'}

    def wait_idle(self,session):
        snapshot=self.poll(session)
        if snapshot.get('active') or snapshot.get('pending'):
            raise RuntimeError('Waiting for terminal completion before Git snapshot; saved reply will retry')

    def poll_all(self):
        error_type=None
        for row in self.state.db.execute('SELECT session FROM '+self.table).fetchall():
            session=self.state.session(row['session'])
            try:
                self.poll(session)
                self.state.db.execute('DELETE FROM observation_faults WHERE session=?',(row['session'],))
            except Exception as error:
                error_type=type(error).__name__
                try:self.observation_fault(session,error_type)
                except Exception:pass
        # One healthy console must not hide another broken observation path.
        self.last_error=error_type

    def observation_fault(self,session,error_type):
        if not session:return
        now=time.time()
        self.state.db.execute('''INSERT INTO observation_faults(session,first_at,error_type) VALUES(?,?,?)
            ON CONFLICT(session) DO UPDATE SET error_type=excluded.error_type''',(session['id'],now,error_type))
        fault=self.state.db.execute('SELECT * FROM observation_faults WHERE session=?',(session['id'],)).fetchone()
        if fault['alerted'] or now-fault['first_at']<30:return
        binding=self.binding(session)
        snapshot=json.loads(binding['snapshot']) if binding else {}
        from .progress import enabled,enqueue
        if not enabled(self.state,session['id']):return
        if snapshot.get('active'):
            enqueue(self.state,session,snapshot['active'],'relay-observation-error',
                'The relay could not read this Codex console. The session was not interrupted. '
                'Use /status or /doctor to inspect the observation blocker; no request will be rerun automatically.',
                source='native',event_seq=snapshot.get('event_seq'))
        else:
            seq=(snapshot.get('pending') or {}).get('seq')
            if seq is None:return
            self.state.db.execute("INSERT OR REPLACE INTO progress_notices VALUES(?,'observation_failed','pending',?)",(seq,now))
        self.state.db.execute('UPDATE observation_faults SET alerted=1 WHERE session=?',(session['id'],))

    def loop(self):
        try:
            while not self.stop.is_set():
                self.poll_all()
                self.stop.wait(0.5)
        finally:self.state.close()


def attach(state,tracker,session,config):
    if state.db.execute('SELECT 1 FROM terminal_attachments WHERE session=?',(session['id'],)).fetchone():
        raise Invalid('This session already has an attachment')
    if session['thread_id']!=config['thread_id']:raise Invalid('Thread binding mismatch')
    verify_pane(config)
    # Skip historical transcript. Start at the latest started turn, including
    # its settings and user prompt; never upload the previous conversation.
    path=Path(config['rollout']);cursor=0;start=0;settings={}
    with path.open('rb') as stream:
        while line:=stream.readline(MAX_LINE+1):
            if len(line)>MAX_LINE:raise Invalid('Unsupported transcript record size')
            value=json.loads(line);p=value.get('payload',{})
            if value.get('type')=='event_msg':
                if p.get('type')=='thread_settings_applied':settings=p.get('thread_settings',{})
                if p.get('type')=='task_started':start=cursor
            cursor=stream.tell()
    config['rollout_inode']=path.stat().st_ino
    target=state.runtime/('terminal-'+session['id']+'.json');write_private(target,config)
    snapshot={'model':settings.get('model'),'effort':settings.get('reasoning_effort'),'progress_after':cursor}
    if 'service_tier' in settings:snapshot['service_tier']=settings['service_tier']
    state.db.execute('INSERT INTO terminal_attachments(session,config_path,cursor,snapshot) VALUES(?,?,?,?)',
                     (session['id'],str(target),start,json.dumps(snapshot)))
    return target


def current_terminal():
    """Called from the authorized running TUI tool environment, never a daemon."""
    thread=os.environ.get('CODEX_THREAD_ID','')
    if not re.fullmatch(r'[0-9a-f-]{36}',thread) or not os.environ.get('TMUX_PANE') or not os.environ.get('TMUX'):
        raise Invalid('Run attachment from the existing Codex tmux conversation')
    config={'thread_id':thread,'tmux_socket':os.environ['TMUX'].split(',')[0],'pane':os.environ['TMUX_PANE']}
    config['pane_pid']=int(tmux(config,'display-message','-p','-t',config['pane'],'#{pane_pid}').strip())
    config['pane_command']=tmux(config,'display-message','-p','-t',config['pane'],'#{pane_current_command}').strip()
    candidates=[];queue=[config['pane_pid']]
    while queue:
        pid=queue.pop(0);process=Path('/proc')/str(pid)
        try:
            if process.joinpath('comm').read_text().strip()=='codex':candidates.append(pid)
            queue.extend(map(int,process.joinpath('task',str(pid),'children').read_text().split()))
        except OSError:continue
        if len(queue)>100:raise Invalid('Ambiguous tmux process tree')
    if len(candidates)!=1:raise Invalid('Pane must contain exactly one native Codex process')
    config['codex_pid']=candidates[0]
    config['codex_start']=Path('/proc',str(candidates[0]),'stat').read_text().rsplit(')',1)[1].split()[19]
    codex_home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    paths=list((codex_home/'sessions').glob('*/*/*/*'+thread+'*.jsonl'))
    if len(paths)!=1:raise Invalid('Could not find the exact current conversation transcript')
    config['rollout']=str(paths[0].resolve())
    verify_pane(config)
    return config
