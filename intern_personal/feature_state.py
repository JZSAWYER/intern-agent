"""Portable, format-checked feature documents; never credentials or executable queues."""
import json

from .core import Invalid
from .records import ID,render,quote


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS feature_documents(workspace TEXT NOT NULL,kind TEXT NOT NULL,
        key TEXT NOT NULL,data TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(workspace,kind,key))''')


def get(state,workspace,kind,key,default=None):
    row=state.db.execute('SELECT data FROM feature_documents WHERE workspace=? AND kind=? AND key=?',(workspace,kind,key)).fetchone()
    return json.loads(row[0]) if row else default


def save(tracker,session,kind,key,data):
    validate_data(kind,data)
    state=tracker.state
    if state.ledger.busy(session['id']):raise Invalid('Wait for the current turn before changing its reference documents')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        revision=state.ledger.revision(session['workspace'],'memory',key,'memory',conversation=session['id'],task=key if kind=='task_brief' else None)
        state.db.execute('INSERT OR REPLACE INTO feature_documents VALUES(?,?,?,?,?)',(session['workspace'],kind,key,json.dumps(data,sort_keys=True),revision))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    return tracker.queue_record(session,phase='Ready')


def snapshot(state,workspace):
    return [dict(r,data=json.loads(r['data'])) for r in state.db.execute('SELECT * FROM feature_documents WHERE workspace=? ORDER BY kind,key',(workspace,))]


def validate_data(kind,data):
    if kind=='task_brief':
        if not isinstance(data,dict) or set(data)!={'fields','reports'}:raise Invalid('Malformed task brief')
        allowed={'background','goal','scope','verification','risks'}
        if not isinstance(data['fields'],dict) or set(data['fields'])-allowed:raise Invalid('Unknown task brief field')
        if any(not isinstance(v,str) or not 1<=len(v)<=8000 for v in data['fields'].values()):raise Invalid('Invalid task brief text')
        if not isinstance(data['reports'],list) or len(data['reports'])>100:raise Invalid('Invalid verification reports')
        for r in data['reports']:
            if (not isinstance(r,dict) or set(r)!={'command','result','limits','conversation','at'}
                    or any(not isinstance(r[k],str) or not 1<=len(r[k])<=4000 for k in ['command','result','limits'])
                    or not isinstance(r['conversation'],str) or not ID.fullmatch(r['conversation'])
                    or not isinstance(r['at'],(int,float)) or r['at']<0):raise Invalid('Invalid verification report')
    elif kind=='research_watch':
        from .research import validate_record
        validate_record(data)
    elif kind=='skill_catalog':
        from .skills import validate_catalog
        validate_catalog(data)
    else:raise Invalid('Unknown feature document')


def validate(rows,workspace,conversations,tasks):
    seen=set()
    if not isinstance(rows,list):raise Invalid('Invalid feature archive')
    for r in rows:
        if (not isinstance(r,dict) or set(r)!={'workspace','kind','key','data','revision'} or r['workspace']!=workspace
                or not isinstance(r['key'],str) or not ID.fullmatch(r['key']) or (r['kind'],r['key']) in seen
                or type(r['revision']) is not int or r['revision']<1):raise Invalid('Malformed feature record')
        seen.add((r['kind'],r['key']));validate_data(r['kind'],r['data'])
        if r['kind']=='task_brief':
            if r['key'] not in tasks or any(p['conversation'] not in conversations for p in r['data']['reports']):raise Invalid('Task brief crosses workspace references')
        elif r['kind']=='research_watch':
            if r['key']!=r['data']['watch'] or r['data']['conversation'] not in conversations:raise Invalid('Research history crosses workspace references')
        elif r['key']!='catalog' or any(s not in conversations for s in r['data']['enabled']):raise Invalid('Skill catalog crosses workspace references')
    return rows


def project(tracker,binding):
    for row in snapshot(tracker.state,binding['workspace']):
        data=row['data']
        sections=[('Reference data',quote(json.dumps(data,ensure_ascii=False,indent=2))),
                  ('Policy','Reported evidence is attributed, not an independent certification. Skills require explicit enablement and a verified local cache. No new authorization.')]
        if row['kind']=='task_brief':
            sections=[(field.title(),quote(body)) for field,body in data['fields'].items()]+[
                ('Verification reports','\n\n'.join(quote('Command: '+r['command']+'\nResult: '+r['result']+'\nLimitations: '+r['limits']+'\nReported by conversation: '+r['conversation']) for r in data['reports']) or '(none)'),sections[-1]]
        tracker.write(binding,'.intern_workspace/features/'+row['kind']+'/'+row['key']+'.md',render('Intern Agent '+row['kind'],dict(kind='feature',workspace=row['workspace'],revision=row['revision'],feature=row),sections))
