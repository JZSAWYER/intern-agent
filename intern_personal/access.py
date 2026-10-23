"""Workspace-scoped collaborators. Owner management remains exclusive."""
from .core import Invalid,WORKSPACE


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS workspace_access(sender TEXT NOT NULL,workspace TEXT NOT NULL,
        role TEXT NOT NULL,PRIMARY KEY(sender,workspace))''')


def role(state,sender,workspace):
    if sender==state.identity()['owner']:return 'owner'
    row=state.db.execute('SELECT role FROM workspace_access WHERE sender=? AND workspace=?',(sender,workspace)).fetchone()
    return row[0] if row else None


def allows(state,item):
    identity=state.identity()
    if item['sender']==identity['owner']:return True
    if item['chat_id']==identity['management_chat']:return False
    session=state.session(item.get('session'))
    if not session:return False
    permission=role(state,item['sender'],session['workspace'])
    if not permission:return False
    if item['role']=='agent':return permission=='operator'
    tokens=item['text'].strip().split()
    if not tokens:return False
    command=tokens[0].lower()
    if command in {'/help','/status','/usage','/cost','/models','/unsupported'}:return True
    if command in {'/model','/reasoning'}:return permission=='operator' or len(tokens)==1
    if command == '/fast':return tokens[1:] == ['status']
    if permission=='viewer':return False
    # Administrative, team, export, archival and instruction-rule changes stay owner-only.
    if command=='/memory':return len(tokens)<2 or tokens[1] in {'show','add','error'}
    return command in {'/task','/chat','/stop','/cancel','/pause','/resume','/approve','/deny','/answer'}


def command(state,tokens):
    from .live_state import identifier
    if len(tokens) not in {3,4} or tokens[0] not in {'grant','revoke'}:raise Invalid('Use /access grant OPEN_ID WORKSPACE operator|viewer or /access revoke OPEN_ID WORKSPACE')
    action,sender,workspace=tokens[:3]
    if not identifier(sender,'ou') or not WORKSPACE.fullmatch(workspace) or not state.workspace(workspace):raise Invalid('Choose an account identifier and registered workspace')
    if sender==state.identity()['owner']:raise Invalid('Owner authority cannot be changed through collaborator commands')
    if action=='grant':
        if len(tokens)!=4 or tokens[3] not in {'operator','viewer'}:raise Invalid('Choose operator or viewer')
        state.db.execute('INSERT OR REPLACE INTO workspace_access VALUES(?,?,?)',(sender,workspace,tokens[3]))
    else:state.db.execute('DELETE FROM workspace_access WHERE sender=? AND workspace=?',(sender,workspace))
    return 'Workspace access '+('granted' if action=='grant' else 'revoked')+'. Feishu app availability and group membership must be managed separately by the owner.'
