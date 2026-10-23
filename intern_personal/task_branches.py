"""Task branches and explicit acceptance of a verified GitHub branch delivery."""
import json
import time

from .core import Invalid
from .lifecycle import utc
from .workspace_tracking import TrackingError


def check_bot_task(tracker,session,task):
    intern=tracker.intern(session);state=tracker.state
    if task and task.get('code_branch') and task['code_branch']!=intern['home_branch']:
        planned=bool(state.db.execute("SELECT 1 FROM tracked_interns WHERE metadata_mode='bot_branch' AND home_branch=?",(task['code_branch'],)).fetchone())
        worked=state.db.execute("SELECT 1 FROM turn_records r JOIN events e ON e.seq=r.seq WHERE r.task_id=? AND e.role='agent'",(task['id'],)).fetchone()
        if not planned or worked:raise Invalid('A task with recorded work belongs to its original bot branch; use an explicit new handoff task')


def command(tracker, session, action, argument=''):
    if not session:raise Invalid('Choose a workspace conversation')
    state=tracker.state
    if state.ledger.busy(session['id']) and action not in {'status','list'}:
        raise Invalid('Stop or finish the current agent turn before changing its task')
    if action in {'brief','report'}:
        from .task_briefs import command as document_command
        return document_command(tracker,session,action,argument)
    if action=='note':
        from .memory import remember
        return remember(tracker,session,'task',argument)
    if action=='use' and tracker.intern(session).get('metadata_mode')=='bot_branch':
        target=state.db.execute('SELECT * FROM named_tasks WHERE id=? AND workspace=?',(argument,session['workspace'])).fetchone()
        if target:check_bot_task(tracker,session,dict(target))
    selected=state.ledger.selection(session['id']);acceptance=None
    if action=='finish' and selected:
        directory=tracker.code_directory(session)
        if tracker.dirty(directory) or tracker.git(directory,'diff','--cached','--name-only'):
            raise Invalid('Commit and publish all task changes before accepting delivery')
        last=state.db.execute('SELECT * FROM turn_records WHERE task_id=? AND source!=\'legacy\' ORDER BY seq DESC LIMIT 1',(selected['id'],)).fetchone()
        head=tracker.git(directory,'rev-parse','HEAD')
        unchanged=bool(last and last['code_end']==head)
        if last and last['code_end'] and tracker.intern(session).get('metadata_mode')=='bot_branch':
            try:
                tracker.git(directory,'merge-base','--is-ancestor',last['code_end'],head)
                unchanged=not any(not n.startswith('.intern_workspace/') for n in tracker.git(directory,'diff','--name-only',last['code_end'],head).splitlines())
            except TrackingError:unchanged=False
        if (not last or last['phase']!='complete' or last['activity']!='completed' or last['sync']!='synced'
                or not unchanged or not any('task_complete' in e or 'turn/completed status completed' in e for e in json.loads(last['evidence']))):
            raise Invalid('Accept only an observed completed, synced task turn at its recorded delivery commit')
        remote=tracker.git(directory,'ls-remote','--heads','origin','refs/heads/'+tracker.intern(session)['code_branch'],network=True)
        if not remote or remote.split()[0]!=head:raise Invalid('Task delivery has not reached GitHub')
        acceptance={'delivery_commit':head,'accepted_at':utc(time.time()),
                    'note':tracker.clean(argument or 'Owner accepted branch delivery via /task finish.')}
    if action in {'pause','finish','cancel','chat'} and selected:switch(tracker,session,None)
    result=state.ledger.task(session,action,argument,acceptance=acceptance)
    if action in {'start','use'}:switch(tracker,session,state.ledger.selection(session['id']))
    return result


def switch(tracker, session, task):
    state=tracker.state;binding=tracker.require_workspace(session['workspace']);intern=tracker.intern(session)
    if intern.get('metadata_mode')=='bot_branch':
        tracker.code_directory(session)
        if intern['code_branch']!=intern['home_branch']:raise Invalid('Restore this bot\'s permanent branch before continuing')
        if task:
            check_bot_task(tracker,session,task)
            state.db.execute('UPDATE named_tasks SET code_branch=? WHERE id=?',(intern['home_branch'],task['id']))
        return
    with tracker.lock(session['workspace']):
        directory=tracker.code_directory(session)
        target=(task.get('code_branch') or 'intern-task/'+session['workspace']+'/'+task['id']) if task else intern['home_branch']
        if target==intern['code_branch']:return
        if tracker.dirty(directory) or tracker.git(directory,'diff','--cached','--name-only'):
            raise Invalid('Branch switching requires a clean worktree; review changes then /task use ID')
        head=tracker.git(directory,'rev-parse','HEAD')
        current=tracker.git(directory,'ls-remote','--heads','origin','refs/heads/'+intern['code_branch'],network=True)
        if not current or current.split()[0]!=head:raise Invalid('Publish the current branch before switching tasks')
        remote=tracker.git(directory,'ls-remote','--heads','origin','refs/heads/'+target,network=True)
        try:local=tracker.git(directory,'rev-parse','--verify','refs/heads/'+target)
        except TrackingError:local=None
        if task and not task.get('code_branch'):
            if remote or (local and local!=head):raise Invalid('Unexpected task branch exists; explicit inspection required')
            if not local:tracker.git(directory,'branch',target,head)
        elif not local:
            if not remote:raise Invalid('Saved task branch is unavailable')
            tracker.git(directory,'fetch','origin','refs/heads/'+target,network=True)
            tracker.git(directory,'branch',target,remote.split()[0])
        if remote and tracker.git(directory,'rev-parse','refs/heads/'+target)!=remote.split()[0]:
            raise Invalid('Task branch diverged; reconcile it explicitly before continuing')
        from .git_hooks import install
        install(state,directory,[intern['code_branch'],target],base_branch=binding['base_branch'])
        tracker.git(directory,'push','origin','refs/heads/'+target+':refs/heads/'+target,network=True)
        tracker.git(directory,'checkout',target)
        state.db.execute('BEGIN IMMEDIATE')
        try:
            state.db.execute('UPDATE tracked_interns SET code_branch=? WHERE session=?',(target,session['id']))
            if task:state.db.execute('UPDATE named_tasks SET code_branch=? WHERE id=?',(target,task['id']))
            state.db.execute('COMMIT')
        except BaseException:state.db.execute('ROLLBACK');raise
