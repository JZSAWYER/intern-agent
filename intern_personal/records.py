"""Versioned Markdown records: fixed metadata, quoted content, linked history."""
import json
from pathlib import Path
import re
from datetime import datetime

from .core import Invalid

SCHEMA = 'intern-agent.record.v2'
TASK_STATES = {'planned','active','paused','blocked','completed','cancelled'}
ACTIVITIES = {'waiting','running','awaiting_approval','stop_requested','stopped','completed','failed','unknown'}
SYNC_STATES = {'pending','synced','blocked'}
PHASES = {'received','running','result_ready','preparing_reply','reply_ready','complete','sync_pending','record_blocked',
          'uncertain_execution','uncertain_delivery','awaiting_terminal','cancelled','cancelled_policy_changed',
          'stop_requested','evidence','code_snapshot','synced','created','selected','renamed','paused','blocked',
          'completed','planned','active','waiting','stopped','failed','Ready','Model updated','Paused','memory'}
KINDS = {'workspace','conversation','task','turn','history','knowledge','team','handoff','feature'}
MARKER = '<!-- intern-agent.record.v2\n'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}')
SHA = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def render(title, metadata, sections):
    metadata = dict(metadata, schema=SCHEMA)
    encoded=json.dumps(metadata,ensure_ascii=True,sort_keys=True,indent=2)
    encoded=encoded.replace('<',r'\u003c').replace('>',r'\u003e')
    body='# '+title+'\n\n'+MARKER+encoded+'\n-->\n'
    for heading,text in sections:
        body+='\n## '+heading+'\n\n'+text.rstrip()+'\n'
    return body


def quote(text):
    return '\n'.join('> '+line for line in str(text or '(none)').splitlines())


def message_section(text,section):
    body=text.split('## '+section+'\n',1)[1].split('\n## ',1)[0].strip('\n')
    lines=body.rstrip().splitlines()
    if any(line!='>' and not line.startswith('> ') for line in lines):raise Invalid('Turn content must remain quoted')
    return '\n'.join('' if line=='>' else line[2:] for line in lines)


def parse(text):
    match=re.match(r'^# [^\n]+\n\n<!-- intern-agent.record.v2\n(.*?)\n-->\n',text,re.S)
    if not match:raise Invalid('Record header is missing or malformed')
    try:value=json.loads(match[1])
    except (ValueError,TypeError):raise Invalid('Record metadata is not valid JSON') from None
    if not isinstance(value,dict):raise Invalid('Record metadata must be an object')
    return value


def validate_document(name,text):
    required_v2=any(part in {'conversations','turns'} for part in Path(name).parts)
    if MARKER not in text:
        if required_v2:raise Invalid('Conversation records require the versioned Markdown schema')
        return None  # Preserved legacy documents are not silently reclassified.
    value=parse(text)
    if value.get('schema')!=SCHEMA or value.get('kind') not in KINDS:raise Invalid('Unknown record schema/kind')
    if not isinstance(value.get('workspace'),str) or not ID.fullmatch(value['workspace']):raise Invalid('Invalid record workspace')
    if type(value.get('revision')) is not int or value['revision']<0:raise Invalid('Invalid record revision')
    kind=value['kind']
    if kind=='knowledge' and 'peer' in value:
        from .peer_mail import validate_record
        validate_record(value['peer'])
    if kind=='feature':
        from .feature_state import validate_data
        feature=value.get('feature')
        if not isinstance(feature,dict) or feature.get('workspace')!=value['workspace'] or feature.get('revision')!=value['revision']:raise Invalid('Malformed feature document')
        validate_data(feature.get('kind'),feature.get('data'))
    if kind=='team':
        team=value.get('team')
        if not isinstance(team,dict) or not isinstance(value.get('members'),list) or not isinstance(value.get('assignments'),list):raise Invalid('Malformed team document')
        if any(not isinstance(m,dict) or not isinstance(m.get('session'),str) for m in value['members']) or any(not isinstance(a,dict) or not isinstance(a.get('task'),str) for a in value['assignments']):raise Invalid('Malformed team references')
        from .teams import validate
        validate(dict(teams=[team],members=value['members'],assignments=value['assignments'],mail=[]),value['workspace'],
                 {m.get('session') for m in value['members']},{a.get('task') for a in value['assignments']})
    if kind=='handoff':
        mail=value.get('mail')
        if not isinstance(mail,dict) or mail.get('state') not in {'unread','read'} or not isinstance(mail.get('body'),str):raise Invalid('Malformed handoff document')
    for key in {'conversation_id','turn_id','task_id'} & value.keys():
        if value[key] is not None and (not isinstance(value[key],str) or not ID.fullmatch(value[key])):
            raise Invalid('Invalid record reference')
    for key in ('received_at','completed_at','updated_at'):
        if key in value and value[key] is not None:
            if not isinstance(value[key],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z',value[key]):
                raise Invalid('Invalid UTC timestamp')
            try:datetime.fromisoformat(value[key].replace('Z','+00:00'))
            except ValueError:raise Invalid('Invalid calendar timestamp') from None
    if kind in {'workspace','conversation'}:
        if value.get('mode') not in {'idle','working'}:raise Invalid('Unknown conversation mode')
        if kind=='conversation':
            if not value.get('conversation_id') or value.get('activity') not in ACTIVITIES:raise Invalid('Invalid conversation activity')
            if (value['mode']=='working')!=(value.get('task_id') is not None):raise Invalid('Conversation mode disagrees with selected task')
        elif type(value.get('working_conversations')) is not int or (value['mode']=='working')!=(value['working_conversations']>0):
            raise Invalid('Workspace aggregate disagrees with conversation modes')
    if kind=='task':
        if not value.get('task_id') or value.get('state') not in TASK_STATES:raise Invalid('Unknown task state')
        if not isinstance(value.get('name'),str) or not value['name'].strip() or len(value['name'])>120:
            raise Invalid('Task requires a user-designated name')
        if value.get('code_branch'):
            from .core import valid_branch
            if not valid_branch(value['code_branch']):raise Invalid('Invalid task code branch')
        if value.get('acceptance'):
            acceptance=value['acceptance']
            if (not isinstance(acceptance,dict) or not isinstance(acceptance.get('note'),str)
                    or not acceptance.get('accepted_at') or not SHA.fullmatch(acceptance.get('delivery_commit',''))):
                raise Invalid('Task acceptance requires an owner note, timestamp and delivered commit')
    if kind=='turn':
        required={'turn_id','conversation_id','phase','activity','sync','source','received_at','completed_at','code_start','code_end','evidence'}
        if required-value.keys():raise Invalid('Turn is missing required fields')
        if value['phase'] not in PHASES or value['activity'] not in ACTIVITIES or value['sync'] not in SYNC_STATES:
            raise Invalid('Unknown turn state')
        if value['source'] not in {'feishu','terminal','local','legacy'}:raise Invalid('Unknown turn source')
        if value['source']!='legacy' and value['received_at'] is None:raise Invalid('New turn requires a receive timestamp')
        if value['phase']=='complete' and value['completed_at'] is None and value['source']!='legacy':raise Invalid('Completed turn requires timestamp')
        if not isinstance(value['evidence'],list):raise Invalid('Invalid evidence list')
        for key in ('code_start','code_end'):
            if value[key] is not None and (not isinstance(value[key],str) or not SHA.fullmatch(value[key])):raise Invalid('Invalid code commit reference')
        if '## Request\n' not in text or '## Response\n' not in text:raise Invalid('Turn requires request/response sections')
    if kind=='history':
        entries=value.get('entries')
        if not isinstance(entries,list):raise Invalid('History entries missing')
        previous=-1;seen=set()
        for entry in entries:
            if not isinstance(entry,dict) or type(entry.get('revision')) is not int or entry['revision']<=previous:
                raise Invalid('History revisions must increase')
            previous=entry['revision'];key=entry.get('event_id')
            if not isinstance(key,str) or not ID.fullmatch(key) or key in seen or entry.get('phase') not in PHASES:raise Invalid('Duplicate or invalid history event')
            if {'at','task_id','turn_id'}-entry.keys():raise Invalid('History references/timestamp missing')
            try:
                timestamp=entry['at']
                if not isinstance(timestamp,str) or not timestamp.endswith('Z'):raise ValueError()
                datetime.fromisoformat(timestamp.replace('Z','+00:00'))
            except (ValueError,TypeError):raise Invalid('Invalid history timestamp') from None
            seen.add(key)
    return value


def validate_tree(root,loader=None):
    root=Path(root);records=[]
    paths=sorted(root.rglob('*.md')) if loader is None else loader
    for item in paths:
        if loader is None:
            name=str(item.relative_to(root));text=item.read_text()
        else:name,text=item
        value=validate_document(name,text)
        if value:records.append(value)
    def identities(kind,key):
        values=[r[key] for r in records if r['kind']==kind]
        if len(values)!=len(set(values)):raise Invalid('Duplicate record identity')
        return {r[key]:r for r in records if r['kind']==kind}
    conversations=identities('conversation','conversation_id')
    tasks=identities('task','task_id')
    turns=identities('turn','turn_id')
    from .feature_state import validate as validate_features
    for workspace in {r['workspace'] for r in records if r['kind']=='feature'}:
        validate_features([r['feature'] for r in records if r['kind']=='feature' and r['workspace']==workspace],workspace,
            {k for k,r in conversations.items() if r['workspace']==workspace},{k for k,r in tasks.items() if r['workspace']==workspace})
    team_records=[r for r in records if r['kind']=='team']
    if team_records or any(r['kind']=='handoff' for r in records):
        from .teams import validate
        for workspace in {r['workspace'] for r in records if r['kind'] in {'team','handoff'}}:
            matching=[r for r in team_records if r['workspace']==workspace]
            validate(dict(teams=[r['team'] for r in matching],members=[m for r in matching for m in r['members']],
                assignments=[a for r in matching for a in r['assignments']],
                mail=[r['mail'] for r in records if r['kind']=='handoff' and r['workspace']==workspace]),workspace,
                {k for k,r in conversations.items() if r['workspace']==workspace},{k for k,r in tasks.items() if r['workspace']==workspace})
    for record in records:
        if record.get('task_id') and record['task_id'] not in tasks:raise Invalid('Record references an unknown named task')
        if record.get('conversation_id') and record['conversation_id'] not in conversations:raise Invalid('Record references an unknown conversation')
        for key,lookup in (('conversation_id',conversations),('task_id',tasks)):
            if record.get(key) and lookup[record[key]]['workspace']!=record['workspace']:raise Invalid('Cross-workspace record reference')
        if record['kind']=='conversation' and record.get('task_id') and tasks[record['task_id']]['state'] in {'completed','cancelled'}:
            raise Invalid('Conversation selected a finished task')
        if record['kind']=='history':
            for entry in record['entries']:
                if entry.get('turn_id') and entry['turn_id'] not in turns:raise Invalid('History references an unknown turn')
                if entry.get('task_id') and entry['task_id'] not in tasks:raise Invalid('History references an unknown task')
    return records


def validate_evolution(before,after):
    old=validate_document('record.md',before);new=validate_document('record.md',after)
    if not old:return
    if not new or old['kind']!=new['kind'] or new['revision']<old['revision']:raise Invalid('Record revision regressed')
    if 'peer' in old:
        from .peer_mail import validate_evolution as peer_evolution
        if 'peer' not in new:raise Invalid('Peer correspondence cannot be removed')
        peer_evolution(old['peer'],new['peer'])
    if old['kind']=='history' and new['entries'][:len(old['entries'])]!=old['entries']:
        raise Invalid('Existing history entries must remain unchanged')
    if old['kind']=='task':
        if old['task_id']!=new['task_id']:raise Invalid('Task identity is immutable')
        if old['state'] in {'completed','cancelled'} and old!=new:raise Invalid('Finished task cannot be changed')
    if old['kind']=='feature':
        a,b=old['feature'],new['feature']
        if (a['workspace'],a['kind'],a['key'])!=(b['workspace'],b['kind'],b['key']):raise Invalid('Feature identity is immutable')
        if a['kind']=='task_brief' and b['data']['reports'][:len(a['data']['reports'])]!=a['data']['reports']:raise Invalid('Verification reports are append-only; record a correction')
        if a['kind']=='skill_catalog' and any(b['data']['entries'].get(k)!=v for k,v in a['data']['entries'].items()):raise Invalid('Skill version pins are immutable; add a new version key')
        if a['kind']=='research_watch':
            if any(a['data'][k]!=b['data'][k] for k in ['conversation','watch','goal','evidence','created_at','expires_at','max_checks','interval_seconds']):raise Invalid('Research plan and budgets are immutable')
            if b['data']['checks']<a['data']['checks'] or b['data']['history'][:len(a['data']['history'])]!=a['data']['history']:raise Invalid('Research history is append-only')
    if old['kind']=='handoff':
        for key in ['id','team','sender','recipient','task','body','created_at']:
            if old['mail'].get(key)!=new['mail'].get(key):raise Invalid('Handoff identity and content are immutable')
        if old['mail']['state']=='read' and new['mail']['state']!='read':raise Invalid('Handoff receipt cannot regress')
    if old['kind']=='team':
        if old['team']['id']!=new['team']['id']:raise Invalid('Team identity is immutable')
        accepted={a['task']:a for a in old['assignments'] if a['state']=='accepted'}
        current={a['task']:a for a in new['assignments']}
        if any(current.get(k)!=v for k,v in accepted.items()):raise Invalid('Accepted team reviews are immutable')
    if old['kind']=='turn':
        for key in ('turn_id','conversation_id','source','received_at'):
            if old.get(key)!=new.get(key):raise Invalid('Turn identity/baseline is immutable')
        if old.get('task_id')!=new.get('task_id') and old['phase']!='received':raise Invalid('Dispatched turn task is immutable')
        if old.get('code_start')!=new.get('code_start') and old.get('code_start') is not None:raise Invalid('Code baseline is immutable')
        request=lambda text:text.split('## Request\n',1)[1].split('\n## ',1)[0]
        if request(before)!=request(after):raise Invalid('Recorded request cannot be overwritten')
        if old['phase']=='complete' and old!=new:raise Invalid('Completed turn metadata is immutable')
        if old['phase']=='complete' and message_section(before,'Response')!=message_section(after,'Response'):
            raise Invalid('Completed response cannot be overwritten; append a correction instead')
