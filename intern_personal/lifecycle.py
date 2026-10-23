"""Private transactional task/turn ledger; Markdown is its published projection."""
from datetime import datetime,timezone
import json
import re
import time
import uuid

from .core import Invalid
from .records import PHASES,TASK_STATES


def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z') if value is not None else None


class Lifecycle:
    def __init__(self,state):self.state=state

    @property
    def db(self):return self.state.db

    def workspace_for(self,item):
        session=self.state.session(item.get('session'))
        if session:return session['workspace'],session['id']
        try:name=self.state.config().get('records_workspace')
        except (OSError,Invalid):name=None
        if name and self.db.execute('SELECT 1 FROM tracked_workspaces WHERE workspace=? AND published=1',(name,)).fetchone():
            return name,'controller'
        return None,'controller'

    def revision(self,workspace,kind,identity,phase,*,conversation=None,task=None,turn=None):
        if phase not in PHASES:raise Invalid('Invalid ledger phase')
        cursor=self.db.execute('''INSERT INTO record_events(workspace,kind,record_id,phase,at,conversation,task_id,turn_id)
            VALUES(?,?,?,?,?,?,?,?)''',(workspace,kind,identity,phase,time.time(),conversation,task,turn))
        return cursor.lastrowid

    def ensure_turn(self,item,received_at=None,source='legacy'):
        found=self.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
        if found:return dict(found)
        workspace,conversation=self.workspace_for(item)
        if not workspace:
            tokens=item.get('text','').strip().split(maxsplit=1)
            if item.get('role')=='control' and tokens and tokens[0].casefold()=='/init':
                self.db.execute('INSERT OR IGNORE INTO initialization_receipts VALUES(?,?,?,NULL)',(item['seq'],received_at,source))
            return None
        # Selection is captured by claim(), after preceding task commands.
        task=None
        cursor=self.db.execute('''INSERT OR IGNORE INTO turn_records
            (seq,workspace,conversation,task_id,source,received_at,phase,activity,sync,revision)
            VALUES(?,?,?,?,?,?,'received','waiting','pending',0)''',
            (item['seq'],workspace,conversation,task,source,received_at))
        if not cursor.rowcount:return dict(self.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())
        rev=self.revision(workspace,'turn',str(item['seq']),'received',conversation=conversation,task=task,turn='turn_'+str(item['seq']))
        self.db.execute('UPDATE turn_records SET revision=? WHERE seq=?',(rev,item['seq']))
        return dict(self.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())

    def bind_turn(self,item):
        record=self.ensure_turn(item)
        if not record or record['phase']!='received':return
        selected=self.selection(record['conversation'])
        from .peer_mail import binding
        if binding(self.state,item['seq'])[1]=='request':selected=None
        task=selected['id'] if selected else None
        revision=self.revision(record['workspace'],'turn',str(item['seq']),'running',conversation=record['conversation'],task=task,turn='turn_'+str(item['seq']))
        self.db.execute("UPDATE turn_records SET task_id=?,phase='running',revision=? WHERE seq=?",(task,revision,item['seq']))

    def update(self,item,phase=None,*,event_phase=None,**values):
        if set(values)-{'activity','sync','code_start','code_end','baseline','completed_at','evidence','stop_requested'}:raise Invalid('Unknown turn update')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            record=self.ensure_turn(item)
            if not record:self.db.execute('COMMIT');return None
            if record['phase']=='complete':self.db.execute('COMMIT');return record
            phase=phase or record['phase']
            if phase not in PHASES:raise Invalid('Unknown turn phase')
            if phase==record['phase'] and all(record[k]==v for k,v in values.items()):
                self.db.execute('COMMIT');return record
            if values.get('activity') in {'completed','failed','stopped'} and not record['completed_at'] and record['source']!='legacy':
                values.setdefault('completed_at',time.time())
            rev=self.revision(record['workspace'],'turn',str(item['seq']),event_phase or phase,
                              conversation=record['conversation'],task=record['task_id'],turn='turn_'+str(item['seq']))
            values.update(phase=phase,revision=rev)
            self.db.execute('UPDATE turn_records SET '+','.join(k+'=?' for k in values)+' WHERE seq=?',(*values.values(),item['seq']))
            self.db.execute('COMMIT')
            return dict(self.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())
        except BaseException:self.db.execute('ROLLBACK');raise

    def observe(self,item,phase):
        mapped={'Running':'running','Reply ready':'reply_ready'}.get(phase,phase)
        if mapped not in PHASES:return self.ensure_turn(item)
        current=self.ensure_turn(item)
        if not current:return None
        # The durable event is newer than a caller's snapshot. Never project a
        # stale completion/running state over the current event.
        actual=self.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()
        if actual and actual['state'] in PHASES:
            if actual['state']=='complete':mapped='complete';activity='completed'
            elif mapped not in {'reply_ready','Ready','Model updated','Paused'}:mapped=actual['state']
        activity={'running':'running','stop_requested':'stop_requested','complete':'completed','cancelled':'stopped',
                  'cancelled_policy_changed':'stopped','uncertain_execution':'unknown','record_blocked':'failed'}.get(mapped)
        if activity=='running' and current['stop_requested']:activity='stop_requested'
        elif activity=='running' and current['activity']=='awaiting_approval':activity='awaiting_approval'
        values={'activity':activity} if activity else {}
        if mapped=='record_blocked' and current['activity'] in {'completed','stopped','failed'}:
            values['activity']=current['activity']
        if mapped=='complete':
            evidence=json.loads(current['evidence'])
            if any('interrupted' in e or 'turn_aborted' in e for e in evidence):values['activity']='stopped'
            elif any('task_failed' in e or 'turn/completed status failed' in e for e in evidence):values['activity']='failed'
            timestamp=current['completed_at'] or (time.time() if current['source']!='legacy' else None)
            values.update(completed_at=timestamp,sync='synced')
        return self.update(item,mapped,**values)

    def evidence(self,seq,evidence):
        item=self.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()
        if not item:return
        record=self.ensure_turn(dict(item));values=json.loads(record['evidence']) if record else []
        if evidence not in values and len(values)<100:
            values.append(evidence);self.update(dict(item),event_phase='evidence',evidence=json.dumps(values))

    def tasks(self,workspace):
        return [dict(r) for r in self.db.execute('SELECT * FROM named_tasks WHERE workspace=? ORDER BY created_at,id',(workspace,))]

    def selection(self,session):
        row=self.db.execute('''SELECT t.* FROM named_tasks t JOIN conversation_tasks c ON c.task_id=t.id WHERE c.session=?''',(session,)).fetchone()
        return dict(row) if row else None

    def busy(self,session):
        if self.db.execute("SELECT 1 FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal','preparing_reply','sending')",(session,)).fetchone():return True
        # Direct terminal work must protect task/branch selection too. It has
        # no incoming Feishu event until its transcript completion is recorded.
        for table in ['terminal_attachments','console_views']:
            row=self.db.execute('SELECT snapshot FROM '+table+' WHERE session=?',(session,)).fetchone()
            if row:
                snapshot=json.loads(row['snapshot'])
                if snapshot.get('active') or snapshot.get('pending'):return True
        return False

    def task(self,session,action,argument=None,*,acceptance=None):
        if not session:raise Invalid('Use a workspace chat, or /task SESSION ACTION in management')
        workspace=session['workspace']
        if action in {'status','list'}:
            rows=self.tasks(workspace)
            selected=self.selection(session['id'])
            return ('Mode: '+('working' if selected else 'idle')+'\nSelected task: '+(selected['name']+' | '+selected['id'] if selected else 'none')
                    +'\n\n'+'\n'.join(r['id']+' | '+r['name']+' | '+r['state'] for r in rows))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.busy(session['id']):raise Invalid('Stop or finish the current agent turn before changing its task selection')
            selected=self.selection(session['id']);now=time.time()
            if action in {'start','rename'}:
                name=' '.join((argument or '').split())
                if not name or len(name)>120 or any(ord(c)<32 for c in name):raise Invalid('Supply a task name of 1–120 characters')
            if action=='start':
                if selected:raise Invalid('Pause or finish the selected task first')
                identity='nt_'+uuid.uuid4().hex[:16]
                rev=self.revision(workspace,'task',identity,'created',conversation=session['id'],task=identity)
                self.db.execute('INSERT INTO named_tasks(id,workspace,name,state,session,created_at,updated_at,revision) VALUES(?,?,?,\'active\',?,?,?,?)',
                                (identity,workspace,name,session['id'],now,now,rev))
                self.db.execute('INSERT OR REPLACE INTO conversation_tasks(session,task_id) VALUES(?,?)',(session['id'],identity))
            elif action=='use':
                row=self.db.execute('SELECT * FROM named_tasks WHERE id=? AND workspace=?',(argument,workspace)).fetchone()
                if not row or row['state'] in {'completed','cancelled'}:raise Invalid('Choose an unfinished task ID from /task list')
                other=self.db.execute('SELECT session FROM conversation_tasks WHERE task_id=? AND session!=?',(argument,session['id'])).fetchone()
                if other:raise Invalid('Task is assigned to another conversation; pause it there first')
                if selected and selected['id']!=argument:raise Invalid('Pause the current task before switching')
                identity=argument;rev=self.revision(workspace,'task',identity,'selected',conversation=session['id'],task=identity)
                self.db.execute("UPDATE named_tasks SET session=?,state='active',updated_at=?,revision=? WHERE id=?",(session['id'],now,rev,identity))
                self.db.execute('INSERT OR REPLACE INTO conversation_tasks(session,task_id) VALUES(?,?)',(session['id'],identity))
            elif action in {'rename','pause','finish','cancel','block','chat','criteria'}:
                if not selected:
                    if action=='chat':self.db.execute('COMMIT');return 'Mode: idle. Conversation history continues to be recorded.'
                    raise Invalid('No task selected. Use /task start NAME or /task use TASK_ID')
                identity=selected['id'];state={'pause':'paused','chat':'paused','finish':'completed','cancel':'cancelled','block':'blocked'}.get(action,selected['state'])
                if action=='finish':
                    delivered=self.db.execute("SELECT 1 FROM turn_records WHERE task_id=? AND phase='complete' AND activity='completed' AND sync='synced'",(identity,)).fetchone()
                    if not delivered:raise Invalid('Finish requires a completed, synced task turn; cancelled or unfinished work cannot count as delivery')
                if action=='criteria' and (not argument or len(argument)>8000):raise Invalid('Supply acceptance criteria of 1–8000 characters')
                rev=self.revision(workspace,'task',identity,'renamed' if action in {'rename','criteria'} else state,conversation=session['id'],task=identity)
                self.db.execute('UPDATE named_tasks SET name=?,state=?,updated_at=?,revision=? WHERE id=?',
                                (name if action=='rename' else selected['name'],state,now,rev,identity))
                if action in {'pause','chat','finish','cancel'}:self.db.execute('DELETE FROM conversation_tasks WHERE session=?',(session['id'],))
                if action=='criteria':self.db.execute('UPDATE named_tasks SET criteria=? WHERE id=?',(argument,identity))
                if action=='finish' and acceptance:
                    self.db.execute('UPDATE named_tasks SET acceptance=? WHERE id=?',
                                    (json.dumps(acceptance),identity))
            else:raise Invalid('Use /task start NAME | use ID | rename NAME | pause | finish | cancel | block | status | list')
            self.db.execute('COMMIT')
            selected=self.selection(session['id'])
            return 'Task: '+identity+'\nMode: '+('working' if selected else 'idle')+'\n'+(selected['name'] if selected else 'Task history preserved. Ordinary conversation is still recorded.')
        except BaseException:self.db.execute('ROLLBACK');raise

    def stop(self,session):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            rows=self.db.execute("SELECT * FROM events WHERE session=? AND role='agent' AND state IN ('queued','running','awaiting_terminal','sync_pending')",(session['id'],)).fetchall()
            for row in rows:
                item=dict(row);record=self.ensure_turn(item)
                if record:
                    rev=self.revision(record['workspace'],'turn',str(item['seq']),'stop_requested',conversation=record['conversation'],task=record['task_id'],turn='turn_'+str(item['seq']))
                    self.db.execute("UPDATE turn_records SET stop_requested=1,activity='stop_requested',revision=? WHERE seq=?",(rev,item['seq']))
                if item['state']=='queued':self.db.execute("UPDATE events SET state='cancelled',result='Cancelled before execution.' WHERE seq=?",(item['seq'],))
            self.db.execute('COMMIT');return len(rows)
        except BaseException:self.db.execute('ROLLBACK');raise

    def stopped(self,seq):
        row=self.db.execute('SELECT stop_requested FROM turn_records WHERE seq=?',(seq,)).fetchone()
        return bool(row and row[0])

    def session_stopped(self,session):
        return bool(self.db.execute("SELECT 1 FROM turn_records r JOIN events e ON e.seq=r.seq WHERE e.session=? AND e.state IN ('running','awaiting_terminal') AND r.stop_requested=1",(session,)).fetchone())
