"""Durable owner goals; continuations share ordinary dispatch, Git and delivery."""
import hashlib
import json
import secrets
import time

from .core import Invalid

STATUSES={'active','paused','complete','blocked','budget_limited','waiting','yielded','cleared'}
MISSING=object()


class Deferred(RuntimeError):
    def __init__(self,due):self.due=due
HELP=('Goal tutorial\n/goal OBJECTIVE — pursue a persistent objective\n'
      '/goal set [TOKEN_BUDGET] OBJECTIVE\n/goal — progress and remaining work\n'
      '/goal pause /goal resume /goal clear\n/goal auto on|off — semantic goal selection\n'
      'Ordinary prompts stay unchanged. Codex selects goals for sustained owner work, not one-off questions or peer notes. '
      'Each continuation waits for the previous Git upload/reply. /stop pauses the goal and interrupts work. '
      'Choices and missing inputs wait for you; background experiments use research alarms. '
      'The native objective is held paused as a publication gate; the relay owns continuation.')


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS goal_plans(
        id TEXT PRIMARY KEY,session TEXT NOT NULL,objective TEXT NOT NULL,status TEXT NOT NULL,
        token_budget INTEGER,task_id TEXT,branch TEXT,tokens_used INTEGER NOT NULL DEFAULT 0,usage_known INTEGER NOT NULL DEFAULT 1,
        iterations INTEGER NOT NULL DEFAULT 0,note TEXT NOT NULL DEFAULT '',next_action TEXT NOT NULL DEFAULT '',
        due_at REAL NOT NULL,source_seq INTEGER,last_seq INTEGER,native_state TEXT,generation INTEGER NOT NULL DEFAULT 0,resume_after INTEGER,
        created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS goal_one_pending ON goal_plans(session) WHERE status NOT IN ('complete','cleared');
        CREATE TABLE IF NOT EXISTS goal_settings(session TEXT PRIMARY KEY,automatic INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS goal_runs(seq INTEGER PRIMARY KEY,goal TEXT NOT NULL,predecessor INTEGER NOT NULL,
        tool_calls INTEGER NOT NULL DEFAULT 0,accounted INTEGER NOT NULL DEFAULT 0,baseline INTEGER,end_total INTEGER,generation INTEGER NOT NULL DEFAULT 0,
        UNIQUE(goal,generation,predecessor));
        CREATE TABLE IF NOT EXISTS goal_usage(session TEXT PRIMARY KEY,total INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS goal_blockers(goal TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,count INTEGER NOT NULL,last_seq INTEGER);''')


def automatic(state,session):
    r=state.db.execute('SELECT automatic FROM goal_settings WHERE session=?',(session['id'],)).fetchone()
    return bool(r[0]) if r else True


def background_scope(db):
    definition=db.execute("SELECT sql FROM sqlite_master WHERE name='background_event_scope'").fetchone()
    if definition and 'goal_runs' not in definition[0]:
        db.executescript('''BEGIN IMMEDIATE;
            DROP VIEW background_event_scope;
            CREATE VIEW background_event_scope AS
              SELECT seq FROM peer_event_scope UNION SELECT seq FROM research_event_scope
              UNION SELECT seq FROM goal_runs WHERE predecessor>=0;
            COMMIT;''')


def current(state,session):
    if not session:return None
    r=state.db.execute('SELECT * FROM goal_plans WHERE session=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(session['id'],)).fetchone()
    return dict(r) if r and r['status']!='cleared' else None


def visible(row):
    return {k:row[k] for k in ['id','objective','status','task_id','branch','token_budget','tokens_used','usage_known','iterations','note','next_action','due_at']} if row else None


def saved_status(state,session):
    row=current(state,session)
    if row:return visible(row)
    for raw in state.db.execute("SELECT data FROM feature_documents WHERE workspace=? AND kind='goal_plan' ORDER BY revision DESC",(session['workspace'],)):
        data=json.loads(raw[0])
        if data['conversation']!=session['id']:continue
        if data['status']=='cleared':return None
        return dict(visible(data),status='complete' if data['status']=='complete' else 'restored_paused')
    return None


def footer(state,item):
    run=state.db.execute('SELECT * FROM goal_runs WHERE seq=?',(item['seq'],)).fetchone()
    if not run:return ''
    row=state.db.execute('SELECT * FROM goal_plans WHERE id=?',(run['goal'],)).fetchone()
    if not row:return ''
    from .workspace_tracking import WorkspaceTracking
    detail=row['note'] if row['status']=='complete' else row['next_action'] or row['note'] or 'Next step has not yet been recorded; use /goal.'
    detail=WorkspaceTracking(state).clean(detail).replace('\n',' ')[:350]
    return 'Goal: '+row['status']+' · iteration '+str(row['iterations']+(0 if run['accounted'] else 1))+'\n'+('Evidence: ' if row['status']=='complete' else 'Remaining: ')+detail


def persist(state,session,row=None):
    row=row or current(state,session)
    if not row:return
    data=dict(conversation=session['id'],**visible(row))
    previous=state.db.execute("SELECT data FROM feature_documents WHERE workspace=? AND kind='goal_plan' AND key=?",(session['workspace'],row['id'])).fetchone()
    body=json.dumps(data,sort_keys=True,ensure_ascii=False)
    if previous and previous[0]==body:return
    rev=state.ledger.revision(session['workspace'],'memory',row['id'],'memory',conversation=session['id'])
    state.db.execute("INSERT OR REPLACE INTO feature_documents VALUES(?,'goal_plan',?,?,?)",(session['workspace'],row['id'],body,rev))


def validate_record(data):
    from .records import ID
    fields={'conversation','id','objective','status','task_id','branch','token_budget','tokens_used','usage_known','iterations','note','next_action','due_at'}
    if (not isinstance(data,dict) or set(data)!=fields or not all(isinstance(data[k],str) and ID.fullmatch(data[k]) for k in ['id','conversation'])
            or data['status'] not in STATUSES or not isinstance(data['objective'],str) or not 1<=len(data['objective'])<=4000
            or data['task_id'] is not None and (not isinstance(data['task_id'],str) or not ID.fullmatch(data['task_id']))
            or not isinstance(data['branch'],str) or not data['branch'] or len(data['branch'])>250
            or any(not isinstance(data[k],str) or len(data[k])>4000 for k in ['note','next_action'])
            or data['token_budget'] is not None and (type(data['token_budget']) is not int or not 1<=data['token_budget']<=10**9)
            or any(type(data[k]) is not int or data[k]<0 for k in ['tokens_used','usage_known','iterations'])
            or data['usage_known'] not in {0,1} or type(data['due_at']) not in {int,float} or not 0<=data['due_at']<10**12):
        raise Invalid('Invalid portable goal record')


def owner_turn(state,session):
    r=state.db.execute("SELECT * FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal') ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone()
    if not r:
        # A directly typed native owner turn normally enters the ledger on
        # completion. Adopt its exact already-observed boundary earlier when
        # it explicitly invokes semantic goal selection; never dispatch it.
        binding=state.db.execute('SELECT snapshot FROM terminal_attachments WHERE session=?',(session['id'],)).fetchone()
        snap=json.loads(binding[0]) if binding else {}
        if snap.get('active') and snap.get('request') and not snap.get('pending'):
            identity=state.identity();key='local_terminal_'+hashlib.sha256((session['id']+snap['active']).encode()).hexdigest()
            from .workspace_tracking import WorkspaceTracking
            text=WorkspaceTracking(state).clean(snap['request'])
            state.db.execute("INSERT OR IGNORE INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES(?,?,?,?,'agent',?,?,?,'awaiting_terminal')",(identity['app_id'],key,session['chat_id'],identity['owner'],session['workspace'],session['id'],text))
            r=state.db.execute('SELECT * FROM events WHERE app_id=? AND message_id=?',(identity['app_id'],key)).fetchone()
            state.ledger.ensure_turn(dict(r),time.time(),'terminal');state.ledger.bind_turn(dict(r))
    if not r or r['sender']!=state.identity()['owner']:raise Invalid('Semantic goal selection requires the current owner request')
    for table in ['goal_runs','research_event_scope','release_jobs']:
        column='event_seq' if table=='release_jobs' else 'seq'
        if state.db.execute('SELECT 1 FROM '+table+' WHERE '+column+'=?',(r['seq'],)).fetchone():raise Invalid('A continuation cannot authorize another goal')
    from .peer_mail import binding
    if binding(state,r['seq'])[1]:raise Invalid('Peer messages cannot authorize goals')
    if state.db.execute('SELECT 1 FROM recovery_runs WHERE seq=?',(r['seq'],)).fetchone():raise Invalid('Recovery cannot authorize goals')
    return dict(r)


def start(state,session,objective,*,budget=None,auto=False,tracker=None):
    if not session or session['state']!='ready':raise Invalid('Choose a ready registered conversation')
    from .workspace_tracking import WorkspaceTracking
    tracker=tracker or WorkspaceTracking(state);tracker.code_directory(session)
    objective=tracker.clean(objective).strip()
    if not objective or len(objective)>4000:raise Invalid('Give an objective of at most 4,000 characters')
    if budget is not None and (type(budget) is not int or not 1<=budget<=10**9):raise Invalid('Give a positive token budget')
    source=owner_turn(state,session) if auto else None
    if auto and not automatic(state,session):raise Invalid('Semantic goal selection is off')
    prior=current(state,session)
    if prior and prior['status'] not in {'complete'}:
        if prior['objective']==objective:return visible(prior)
        raise Invalid('A saved goal already exists; pause/clear it explicitly before replacing it')
    now=time.time();gid='g_'+secrets.token_hex(8)
    task=state.ledger.selection(session['id']);branch=tracker.intern(session)['code_branch']
    changed=state.db.execute('INSERT OR IGNORE INTO goal_plans(id,session,objective,status,token_budget,task_id,branch,due_at,source_seq,last_seq,created_at,updated_at) VALUES(?,?,?,\'active\',?,?,?,?,?,?,?,?)',
        (gid,session['id'],objective,budget,task['id'] if task else None,branch,now,source['seq'] if source else None,source['seq'] if source else None,now,now))
    if not changed.rowcount:
        prior=current(state,session)
        if prior and prior['objective']==objective:return visible(prior)
        raise Invalid('Another goal was already registered; inspect /goal before replacing it')
    if source:bind(state,session,source['seq'],gid,-1)
    persist(state,session);return visible(current(state,session))


def bind(state,session,seq,gid=None,predecessor=-1):
    goal=current(state,session)
    if not goal or goal['status']!='active':return
    count=state.db.execute('SELECT total FROM goal_usage WHERE session=?',(session['id'],)).fetchone()
    if not count:
        old=state.db.execute('SELECT usage FROM conversation_usage WHERE session=?',(session['id'],)).fetchone()
        total=json.loads(old[0]).get('totalTokens') if old else None
        if type(total) is int and total>=0:usage(state,session,total);count=(total,)
    state.db.execute('INSERT OR IGNORE INTO goal_runs(seq,goal,predecessor,baseline,generation) VALUES(?,?,?,?,?)',(seq,gid or goal['id'],predecessor,count[0] if count else None,goal['generation']))
    state.db.execute('UPDATE goal_plans SET last_seq=? WHERE id=?',(seq,goal['id']))


def usage(state,session,value):
    if type(value) is int and 0<=value<2**63:state.db.execute('INSERT OR REPLACE INTO goal_usage VALUES(?,?)',(session['id'],value))


def ended(state,session,seq,total=MISSING):
    if seq is None:return
    if total is MISSING:
        row=state.db.execute('SELECT total FROM goal_usage WHERE session=?',(session['id'],)).fetchone();total=row[0] if row else None
    state.db.execute('UPDATE goal_runs SET end_total=? WHERE seq=? AND accounted=0',(total,seq))


def settle(relay):
    state=relay.state
    for raw in state.db.execute("SELECT g.*,p.session FROM goal_runs g JOIN goal_plans p ON p.id=g.goal JOIN events e ON e.seq=g.seq WHERE g.accounted=0 AND e.state='complete' AND e.receipt IS NOT NULL").fetchall():
        r=dict(raw);session=state.session(r['session'])
        from .turn_uploads import ready
        if not ready(state,r['seq']):continue
        known=r['baseline'] is not None and r['end_total'] is not None and r['end_total']>=r['baseline']
        used=r['end_total']-r['baseline'] if known else 0
        state.db.execute('BEGIN IMMEDIATE')
        try:
            if state.db.execute('UPDATE goal_runs SET accounted=1 WHERE seq=? AND accounted=0',(r['seq'],)).rowcount:
                state.db.execute('UPDATE goal_plans SET tokens_used=tokens_used+?,usage_known=usage_known AND ?,iterations=iterations+1 WHERE id=?',(used,int(known),r['goal']))
            state.db.execute('COMMIT')
        except BaseException:state.db.execute('ROLLBACK');raise
        state.db.execute('DELETE FROM goal_blockers WHERE goal=? AND last_seq<?',(r['goal'],r['seq']))
        row=dict(state.db.execute('SELECT * FROM goal_plans WHERE id=?',(r['goal'],)).fetchone())
        if row['status'] in {'active','waiting'} and row['token_budget'] is not None and (not row['usage_known'] or row['tokens_used']>=row['token_budget']):
            state.db.execute("UPDATE goal_plans SET status='budget_limited',note=? WHERE id=?",('Goal token budget reached' if row['usage_known'] else 'Token measurement unavailable; the budget cannot be verified',row['id']))
            try:relay.notify(session,'Goal paused at its budget boundary. Use /goal for saved progress.')
            except Exception:pass
        elif row['status']=='active' and r['tool_calls']==0 and row['iterations']>1 and row['resume_after']!=r['seq']:
            state.db.execute("UPDATE goal_plans SET status='yielded',note='Continuation made no tool call; automatic spinning suppressed' WHERE id=?",(row['id'],))
            try:relay.notify(session,'Goal yielded without further tool work. Use /goal to inspect remaining work or /goal resume.')
            except Exception:pass
        persist(state,session,dict(state.db.execute('SELECT * FROM goal_plans WHERE id=?',(r['goal'],)).fetchone()))
        relay.tracker.queue_record(session,phase='Ready')


def tool(state,seq):
    if seq is not None:state.db.execute('UPDATE goal_runs SET tool_calls=tool_calls+1 WHERE seq=?',(seq,))


def checkpoint(state,session,action,*,note='',next_action='',after=None):
    row=current(state,session)
    if not row:raise Invalid('This conversation has no saved goal')
    from .workspace_tracking import WorkspaceTracking
    tracker=WorkspaceTracking(state);note=tracker.clean(note);next_action=tracker.clean(next_action)
    note=note.strip();next_action=next_action.strip()
    if action not in {'progress','complete','blocked','wait'}:raise Invalid('Use progress, complete, blocked or wait')
    if not note or len(note)>4000 or len(next_action)>4000:raise Invalid('Record bounded evidence, remaining work and the next step')
    if action=='progress' and note==row['note'] and next_action==row['next_action']:return visible(row)
    status=row['status'];due=time.time()
    if action in {'progress','wait'}:state.db.execute('DELETE FROM goal_blockers WHERE goal=?',(row['id'],))
    if action=='complete':status='complete'
    elif action=='wait':
        from .research import duration
        due+=duration(after or '1h')
        if status=='active':status='waiting'
    elif action=='blocked':
        event=state.db.execute("SELECT seq FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal') ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone()
        seq=event[0] if event else None;fingerprint=hashlib.sha256(note.encode()).hexdigest()
        prior=state.db.execute('SELECT * FROM goal_blockers WHERE goal=?',(row['id'],)).fetchone()
        count=prior['count'] if prior and prior['fingerprint']==fingerprint else 0
        if not prior or prior['last_seq']!=seq:count+=1
        state.db.execute('INSERT OR REPLACE INTO goal_blockers VALUES(?,?,?,?)',(row['id'],fingerprint,count,seq))
        status='blocked' if count>=3 and status in {'active','waiting'} else status
    state.db.execute('UPDATE goal_plans SET status=?,note=?,next_action=?,due_at=?,updated_at=? WHERE id=?',(status,note,next_action,due,time.time(),row['id']))
    persist(state,session);return visible(current(state,session))


def control(state,session,action):
    row=current(state,session)
    if not row:raise Invalid('No saved goal; use /goal OBJECTIVE')
    if action=='pause' and row['status']=='complete':return 'Goal is already complete.'
    if action=='clear':
        state.db.execute("UPDATE goal_plans SET status='cleared',note='Cleared by owner' WHERE id=?",(row['id'],))
        persist(state,session,dict(state.db.execute('SELECT * FROM goal_plans WHERE id=?',(row['id'],)).fetchone()))
    else:
        if action=='resume' and row['status']=='complete':raise Invalid('This goal is complete; clear it and start a new objective')
        if action=='resume' and row['status']=='budget_limited':raise Invalid('The declared budget cannot be extended by resume; inspect it, clear it and explicitly authorize a new goal/budget')
        if action=='resume':
            task=state.ledger.selection(session['id']);intern=state.db.execute('SELECT code_branch FROM tracked_interns WHERE session=?',(session['id'],)).fetchone()
            if (task['id'] if task else None)!=row['task_id'] or not intern or intern['code_branch']!=row['branch']:raise Invalid('Select the saved goal task and branch before resuming')
            if row['status']=='active':return 'Goal is already active.'
            anchor=row['last_seq']
            while anchor:
                prior=state.db.execute('SELECT e.state,g.predecessor FROM events e LEFT JOIN goal_runs g ON g.seq=e.seq WHERE e.seq=?',(anchor,)).fetchone()
                if not prior or prior['state'] not in {'cancelled','cancelled_policy_changed'}:break
                anchor=prior['predecessor'] if prior['predecessor'] and prior['predecessor']>0 else None
            state.db.execute('UPDATE goal_plans SET generation=generation+1,last_seq=?,resume_after=? WHERE id=?',(anchor,anchor,row['id']))
        state.db.execute('UPDATE goal_plans SET status=?,due_at=?,updated_at=? WHERE id=?',('paused' if action=='pause' else 'active',time.time(),time.time(),row['id']))
        persist(state,session)
    if action!='resume':state.db.execute("UPDATE events SET state='cancelled' WHERE state='queued' AND seq IN (SELECT seq FROM goal_runs WHERE goal=?)",(row['id'],))
    return 'Goal '+('cleared' if action=='clear' else 'paused' if action=='pause' else 'resumed')+'.'


def adopt_turn(state,session,seq):
    row=current(state,session)
    if not row or state.db.execute('SELECT 1 FROM goal_runs WHERE seq=?',(seq,)).fetchone():return
    recovery=state.db.execute('SELECT ep.root_seq FROM recovery_runs r JOIN recovery_episodes ep ON ep.id=r.episode WHERE r.seq=?',(seq,)).fetchone()
    if recovery and state.db.execute('SELECT 1 FROM goal_runs WHERE seq=? AND goal=?',(recovery[0],row['id'])).fetchone():
        if row['status']=='yielded' and row['note']=='Awaiting confirmed recovery':state.db.execute("UPDATE goal_plans SET status='active' WHERE id=?",(row['id'],))
        bind(state,session,seq,predecessor=-seq);return
    try:owner_turn(state,session)
    except Invalid:return
    bind(state,session,seq,predecessor=-seq)


def restore_saved(state,session):
    if current(state,session):return
    rows=state.db.execute("SELECT data FROM feature_documents WHERE workspace=? AND kind='goal_plan' ORDER BY revision DESC",(session['workspace'],)).fetchall()
    for raw in rows:
        data=json.loads(raw[0])
        if data['conversation']!=session['id']:continue
        if data['status'] in {'cleared','complete'}:raise Invalid('The saved goal was cleared or completed; start a new objective')
        now=time.time()
        state.db.execute('INSERT INTO goal_plans(id,session,objective,status,token_budget,task_id,branch,tokens_used,usage_known,iterations,note,next_action,due_at,created_at,updated_at) VALUES(?,?,?,\'paused\',?,?,?,?,?,?,?,?,?,?,?)',
            (data['id'],session['id'],data['objective'],data['token_budget'],data['task_id'],data['branch'],data['tokens_used'],data['usage_known'],data['iterations'],data['note'],data['next_action'],now,now,now))
        return
    raise Invalid('No saved goal to resume')


def sync_native(relay,session,clear=False):
    """A native objective is a compatibility mirror, never an untracked dispatcher."""
    row=current(relay.state,session)
    if relay.terminal.binding(session):return {'adapter':'tracked-terminal','native_mutation':False}
    relay.host.ensure_started(session)
    connection=relay.host.connect(session=session)
    try:
        if clear:connection.request('thread/goal/clear',{'threadId':session['thread_id'],'origin':'user'},timeout=5);return None
        if not row:return None
        prior=connection.request('thread/goal/get',{'threadId':session['thread_id']},timeout=5).get('goal')
        if prior and prior.get('objective')!=row['objective'] and prior.get('status') not in {'complete'}:
            raise Invalid('A different native goal exists; inspect and explicitly clear it before starting this relay goal')
        params={'threadId':session['thread_id'],'objective':row['objective'],'status':'complete' if row['status']=='complete' else 'paused','origin':'user'}
        if row['token_budget'] is not None:params['tokenBudget']=row['token_budget']
        data=connection.request('thread/goal/set',params,timeout=5)
        visible_native={k:v for k,v in data.get('goal',{}).items() if k in {'objective','status','tokenBudget','tokensUsed','timeUsedSeconds'}}
        relay.state.db.execute('UPDATE goal_plans SET native_state=? WHERE id=?',(json.dumps(visible_native),row['id']))
        return visible_native
    finally:connection.close()


def command(relay,session,tokens):
    if not session or session['state'] not in {'ready','paused'}:raise Invalid('Choose a registered conversation')
    action=tokens[0].lower() if tokens else 'show'
    if action=='auto':
        if len(tokens)!=2 or tokens[1].lower() not in {'on','off'}:raise Invalid('Use /goal auto on|off')
        relay.state.db.execute('INSERT OR REPLACE INTO goal_settings VALUES(?,?)',(session['id'],int(tokens[1].lower()=='on')))
    elif action in {'pause','resume','clear'}:
        if action=='resume':restore_saved(relay.state,session)
        result=control(relay.state,session,action)
        try:sync_native(relay,session,clear=action=='clear')
        except Invalid as error:
            if action=='resume':
                relay.state.db.execute("UPDATE goal_plans SET status='yielded',note=? WHERE id=?",(str(error),current(relay.state,session)['id']));persist(relay.state,session)
                result='Goal needs attention: '+str(error)
            else:result+=' Native mirror update pending; relay policy is saved.'
        except Exception:result+=' Native mirror update pending; relay policy is saved.'
        relay.tracker.queue_record(session);return result
    elif action not in {'show','status'}:
        body=tokens[1:] if action=='set' else tokens;budget=None
        if action=='set' and body and body[0].isdecimal():budget=int(body.pop(0))
        start(relay.state,session,' '.join(body),budget=budget,tracker=relay.tracker)
        try:sync_native(relay,session)
        except Invalid as error:
            relay.state.db.execute("UPDATE goal_plans SET status='yielded',note=? WHERE id=?",(str(error),current(relay.state,session)['id']));persist(relay.state,session)
        except Exception:pass  # An unavailable optional mirror cannot activate a native dispatcher.
    relay.tracker.queue_record(session)
    return json.dumps(dict(goal=saved_status(relay.state,session),automatic_goal_selection=automatic(relay.state,session),automatic_continuations=True,native_dispatch='paused_at_git_gate',help='/help goal'),ensure_ascii=False)


def guard(relay,item):
    run=relay.state.db.execute('SELECT * FROM goal_runs WHERE seq=?',(item['seq'],)).fetchone()
    if not run:return
    goal=current(relay.state,relay.state.session(item['session']))
    if not goal or goal['id']!=run['goal'] or goal['generation']!=run['generation'] or goal['status'] not in {'active','waiting'}:
        from .auto_recovery import RecoverySkipped
        raise RecoverySkipped('The owner paused, cleared or completed this goal')
    from .user_questions import pending
    if goal['due_at']>time.time():raise Deferred(goal['due_at'])
    if pending(relay.state,relay.state.session(item['session'])):raise Deferred(time.time()+5)
    selection=relay.state.ledger.selection(item['session']);intern=relay.tracker.intern(relay.state.session(item['session']))
    if (selection['id'] if selection else None)!=goal['task_id'] or not intern or intern['code_branch']!=goal['branch']:
        relay.state.db.execute("UPDATE goal_plans SET status='yielded',note='Selected task or branch changed' WHERE id=?",(goal['id'],));persist(relay.state,relay.state.session(item['session']))
        from .auto_recovery import RecoverySkipped
        raise RecoverySkipped('The selected task or branch differs from the saved goal')
    if goal['status']=='waiting':
        relay.state.db.execute("UPDATE goal_plans SET status='active' WHERE id=?",(goal['id'],));persist(relay.state,relay.state.session(item['session']))


def poll(relay,now=None):
    state=relay.state;now=time.time() if now is None else now
    settle(relay)
    for raw in state.db.execute("SELECT * FROM goal_plans WHERE status IN ('active','waiting') AND due_at<=?",(now,)).fetchall():
        row=dict(raw);session=state.session(row['session'])
        if not session or session['state']!='ready':continue
        selection=state.ledger.selection(session['id']);intern=relay.tracker.intern(session)
        if (selection['id'] if selection else None)!=row['task_id'] or not intern or intern['code_branch']!=row['branch']:
            state.db.execute("UPDATE goal_plans SET status='yielded',note='Selected task or branch changed; return to the goal task before resuming' WHERE id=?",(row['id'],));persist(state,session);continue
        last=state.db.execute('SELECT e.*,g.tool_calls,g.accounted,g.baseline FROM events e LEFT JOIN goal_runs g ON e.seq=g.seq WHERE e.seq=?',(row['last_seq'],)).fetchone() if row['last_seq'] else None
        if last:
            from .turn_uploads import ready
            if last['state']!='complete' or not last['receipt'] or not ready(state,last['seq']):continue
            record=state.db.execute('SELECT activity FROM turn_records WHERE seq=?',(last['seq'],)).fetchone()
            if record and record[0] in {'failed','stopped'} and row['resume_after']!=last['seq']:
                state.db.execute("UPDATE goal_plans SET status='yielded',note='Awaiting confirmed recovery' WHERE id=?",(row['id'],));persist(state,session);continue
        from .user_questions import pending
        if pending(state,session) or state.ledger.busy(session['id']):continue
        if state.db.execute('SELECT 1 FROM bot_tracking_pending WHERE session=?',(session['id'],)).fetchone() or state.db.execute('SELECT 1 FROM tracking_pending WHERE workspace=?',(session['workspace'],)).fetchone():continue
        active=False
        for table in ['terminal_attachments','console_views']:
            saved=state.db.execute('SELECT snapshot FROM '+table+' WHERE session=?',(session['id'],)).fetchone()
            snap=json.loads(saved[0]) if saved else {};active=active or bool(snap.get('active') or snap.get('pending'))
        if active:continue
        if state.db.execute("SELECT 1 FROM events WHERE session=? AND state IN ('queued','running','awaiting_terminal','preparing_reply','result_ready','sending','uncertain_execution','uncertain_delivery','record_blocked','sync_pending') AND role!='urgent'",(session['id'],)).fetchone():continue
        predecessor=row['last_seq'] or 0
        if state.db.execute('SELECT 1 FROM goal_runs WHERE goal=? AND predecessor=? AND generation=?',(row['id'],predecessor,row['generation'])).fetchone():continue
        identity=state.identity();body='Continue the saved owner goal. Check current evidence, remaining work and the next useful action. Use the goal checkpoint tool before finishing; do not replay the previous prompt or restart existing jobs.'
        state.db.execute('BEGIN IMMEDIATE')
        try:
            fresh=current(state,session)
            if not fresh or fresh['id']!=row['id'] or fresh['status'] not in {'active','waiting'} or fresh['last_seq']!=row['last_seq']:
                state.db.execute('ROLLBACK');continue
            if state.db.execute("SELECT 1 FROM events WHERE session=? AND state IN ('queued','running','awaiting_terminal','preparing_reply','result_ready','sending','uncertain_execution','uncertain_delivery','record_blocked','sync_pending') AND role!='urgent'",(session['id'],)).fetchone():
                state.db.execute('ROLLBACK');continue
            key='goal:'+row['id']+':'+str(row['generation'])+':'+str(predecessor)
            cursor=state.db.execute("INSERT OR IGNORE INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES(?,?,?,?,'agent',?,?,?,'queued')",(identity['app_id'],key,session['chat_id'],identity['owner'],session['workspace'],session['id'],body))
            if cursor.rowcount:
                seq=cursor.lastrowid;state.db.execute("UPDATE goal_plans SET status='active' WHERE id=?",(row['id'],));bind(state,session,seq,row['id'],predecessor)
                state.ledger.ensure_turn(dict(state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()),now,'goal')
            state.db.execute('COMMIT')
        except BaseException:state.db.execute('ROLLBACK');raise


def loop(relay):
    try:
        while not relay.stop.is_set():
            try:poll(relay);relay.goal_error_type=None
            except Exception as error:relay.goal_error_type=type(error).__name__
            if relay.stop.wait(2):break
    finally:relay.state.close()
