"""Recoverable conversation preferences without machine paths or account grants."""
import re
import time
import hashlib
import json

from .core import Invalid

SCHEMA='intern-agent.portable-settings.v1'


def role(state,sid):
    if state.db.execute('SELECT 1 FROM controller_routes WHERE session=?',(sid,)).fetchone():return 'controller'
    if state.db.execute('SELECT 1 FROM terminal_attachments WHERE session=?',(sid,)).fetchone():return 'native'
    file=state.root/'live.json'
    if file.exists():
        from .auto_recovery import settings
        if settings(state)['incident_session']==sid:return 'recovery_operator'
    row=state.db.execute('SELECT role FROM restored_roles WHERE session=?',(sid,)).fetchone()
    return row['role'] if row else 'agent'


def snapshot(state,workspace,conversations=None):
    rows=[]
    for session in state.sessions():
        if session['workspace']!=workspace or conversations is not None and session['id'] not in conversations:continue
        sid=session['id']
        def one(table,columns):
            row=state.db.execute('SELECT '+columns+' FROM '+table+' WHERE session=?',(sid,)).fetchone()
            return dict(row) if row else None
        from .auto_recovery import enabled
        recovery=one('recovery_session_settings','enabled')
        if recovery is None and (state.root/'live.json').exists():recovery=dict(enabled=int(enabled(state,sid)))
        prior=one('restored_roles','prior_state')
        lifecycle=prior['prior_state'] if session['state']=='restored' and prior else session['state']
        rows.append(dict(conversation=sid,role=role(state,sid),lifecycle=lifecycle,model=one('session_settings','model,effort'),
            fast=one('session_service_tiers','tier'),progress=one('progress_settings','enabled,presentation'),
            trigger=one('trigger_settings','mode'),automatic_recovery=recovery,goal_selection=one('goal_settings','automatic')))
    return dict(schema=SCHEMA,conversations=rows)


def validate(data,conversations):
    if not isinstance(data,dict) or set(data)!={'schema','conversations'} or data['schema']!=SCHEMA or not isinstance(data['conversations'],list):raise Invalid('Invalid portable conversation settings')
    seen=set()
    for row in data['conversations']:
        if (not isinstance(row,dict) or set(row)-{'goal_selection'}!={'conversation','role','lifecycle','model','fast','progress','trigger','automatic_recovery'}
                or not isinstance(row['role'],str) or row['role'] not in {'agent','native','controller','recovery_operator'}
                or not isinstance(row['lifecycle'],str) or row['lifecycle'] not in {'ready','paused','archived','creating','restored'}
                or not isinstance(row['conversation'],str) or row['conversation'] not in conversations or row['conversation'] in seen):raise Invalid('Portable settings cross conversation identities')
        seen.add(row['conversation'])
        model=row['model']
        if model is not None and (not isinstance(model,dict) or set(model)!={'model','effort'} or not isinstance(model['model'],str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',model['model'])
                or not isinstance(model['effort'],str) or not re.fullmatch('[a-z]{1,32}',model['effort'])):raise Invalid('Invalid portable model preference')
        allowed=dict(fast=('tier',{'default','priority','fast'}),trigger=('mode',{'all','mentions'}),automatic_recovery=('enabled',{0,1}))
        for kind,(key,values) in allowed.items():
            item=row[kind]
            if item is not None and (not isinstance(item,dict) or set(item)!={key} or not isinstance(item[key],(str,int)) or item[key] not in values):raise Invalid('Invalid portable '+kind+' preference')
        progress=row['progress']
        goal=row.get('goal_selection')
        if goal is not None and (not isinstance(goal,dict) or set(goal)!={'automatic'} or type(goal['automatic']) is not int or goal['automatic'] not in {0,1}):raise Invalid('Invalid portable semantic goal preference')
        if progress is not None and (not isinstance(progress,dict) or set(progress)!={'enabled','presentation'} or type(progress['enabled']) is not int
                or progress['enabled'] not in {0,1} or progress['presentation'] not in {'editable','messages'}):raise Invalid('Invalid portable progress preference')
    return data


def checkpoint(state,workspace,data,conversation):
    """Called in the projector's snapshot transaction; unchanged data is a no-op."""
    if not data['conversations']:return
    signature=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    previous=state.db.execute('SELECT fingerprint FROM workspace_preference_versions WHERE workspace=?',(workspace,)).fetchone()
    if previous and previous[0]==signature:return
    state.ledger.revision(workspace,'memory','portable_settings','memory',conversation=conversation)
    state.db.execute('INSERT OR REPLACE INTO workspace_preference_versions VALUES(?,?)',(workspace,signature))


def apply(state,data):
    for row in data['conversations']:
        sid=row['conversation']
        state.db.execute('INSERT INTO restored_roles VALUES(?,?,?)',(sid,row['role'],row['lifecycle']))
        if row['lifecycle']=='archived':state.db.execute("UPDATE sessions SET state='archived' WHERE id=?",(sid,))
        if row['model']:
            m=row['model'];state.db.execute('INSERT OR REPLACE INTO session_settings VALUES(?,?,?)',(sid,m['model'],m['effort']))
            # Zero is a recovery baseline, not an executed control event. New
            # explicit owner choices have positive sequence numbers and win.
            state.db.execute('INSERT INTO model_preferences VALUES(?,?,?,0)',(sid,m['model'],m['effort']))
        if row['fast']:state.db.execute('INSERT INTO session_service_tiers VALUES(?,?,?)',(sid,row['fast']['tier'],time.time()))
        if row['progress']:state.db.execute('INSERT INTO progress_settings VALUES(?,?,?)',(sid,row['progress']['enabled'],row['progress']['presentation']))
        if row['trigger']:state.db.execute('INSERT INTO trigger_settings VALUES(?,?)',(sid,row['trigger']['mode']))
        if row['automatic_recovery']:state.db.execute('INSERT INTO recovery_session_settings VALUES(?,?)',(sid,row['automatic_recovery']['enabled']))
        if row.get('goal_selection'):state.db.execute('INSERT OR REPLACE INTO goal_settings VALUES(?,?)',(sid,row['goal_selection']['automatic']))


def activate_role(state,session):
    """Called after owner reconnection establishes new account/thread routing."""
    row=state.db.execute('SELECT role FROM restored_roles WHERE session=?',(session['id'],)).fetchone()
    if not row or row['role']!='recovery_operator':return
    config=state.config()
    from .auto_recovery import validate
    if validate(config)['incident_session'] not in {None,session['id']}:return
    config['auto_recovery']=dict(config.get('auto_recovery',{}),incident_session=session['id'])
    from .live_state import write_private
    write_private(state.root/'live.json',config)


def lifecycle(state,session):
    row=state.db.execute('SELECT prior_state FROM restored_roles WHERE session=?',(session['id'],)).fetchone()
    if row and row['prior_state']=='paused':
        state.set_session(session['id'],state='paused')
    return state.session(session['id'])
