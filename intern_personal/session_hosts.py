"""One private Codex backend and visible tmux console per conversation.

The Feishu transport is shared; conversation processes, sockets and rollouts
are not. Native terminal attachments are deliberately excluded.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time

from .core import Invalid


class SessionHosts:
    def __init__(self, state, config):
        self.state, self.config = state, config
        self.default_socket = Path('/tmp') / ('tmux-' + str(os.getuid())) / 'default'

    def spec(self, session=None):
        if session:
            if self.state.db.execute('SELECT 1 FROM terminal_attachments WHERE session=?', (session['id'],)).fetchone():
                raise Invalid('The original terminal attachment has its own Codex process')
            key = hashlib.sha256(session['id'].encode()).hexdigest()[:16]
            slug = re.sub('[^a-z0-9-]+', '-', session['workspace'].lower()).strip('-')[:36] or 'workspace'
            if self.state.db.execute('SELECT 1 FROM controller_routes WHERE session=?',(session['id'],)).fetchone():
                slug='intern-agent-control'
            name = 'ia-' + slug + '-' + key[:8]
            base = self.state.runtime / 'hosts' / key
            socket = self.state.runtime / ('h-' + key + '.sock')
            tmux_socket = self.default_socket
        else:
            key, name = 'catalog', 'ia-personal-models'
            base = self.state.runtime / 'hosts' / key
            socket = self.state.runtime / 'catalog.sock'
            tmux_socket = self.state.runtime / 'tmux.sock'
        if max(len(os.fsencode(socket)), len(os.fsencode(tmux_socket))) >= 104:
            raise Invalid('Choose a shorter live state path for Unix sockets')
        return dict(key=key, name=name, base=base, socket=socket, tmux_socket=tmux_socket,
                    home=base / 'agent-home', codex_home=base / 'codex-home', tmp=base / 'tmp')

    def prepare(self, session=None):
        spec = self.spec(session)
        for path in [spec['base'].parent, spec['base'], spec['home'], spec['codex_home'],
                     spec['home'] / '.config', spec['tmp']]:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise Invalid('Codex session directories must be private and owned by you')
        auth = spec['codex_home'] / 'auth.json'
        source = Path(self.config['codex_auth_home']).expanduser().resolve() / 'auth.json'
        if not source.is_file():
            raise Invalid('Sign into Codex before starting a conversation')
        if not auth.exists() and not auth.is_symlink():
            auth.symlink_to(source)
        elif not auth.is_symlink() or auth.resolve() != source:
            raise Invalid('Session authentication link does not match the configured login')
        if session:
            tracked=self.state.db.execute('SELECT code_path FROM tracked_interns WHERE session=?',(session['id'],)).fetchone()
            cwd=tracked['code_path'] if tracked else self.state.workspace(session['workspace'])['cwd']
            # Remote TUI onboarding reads its own home as well as the server.
            # Keep this trust decision in this session only, never global config.
            from .execution_policy import write_config
            write_config(spec['codex_home']/'config.toml',self.state.config(),project=cwd)
        else:
            from .execution_policy import write_config
            write_config(spec['codex_home']/'config.toml',self.state.config())
        if session and session.get('thread_id'):
            self.import_rollout(spec, session['thread_id'])
        return spec

    def import_rollout(self, spec, thread):
        # Resume the exact thread. Never fork it or copy another conversation's
        # database, history, credentials or rollout into this home.
        if not re.fullmatch('[A-Za-z0-9_-]{1,120}', thread):
            raise Invalid('Unsupported saved conversation identity')
        destination = spec['codex_home'] / 'sessions'
        existing = list(destination.glob('**/rollout-*' + thread + '.jsonl'))
        if len(existing) == 1:
            return existing[0]
        if existing:
            raise Invalid('Multiple saved rollouts require operator review')
        legacy = self.state.runtime / 'codex-home' / 'sessions'
        sources = list(legacy.glob('**/rollout-*' + thread + '.jsonl'))
        if len(sources) != 1:
            raise Invalid('The exact saved conversation rollout is unavailable; no replacement was started')
        source = sources[0]
        if source.is_symlink() or not source.resolve().is_relative_to(legacy.resolve()):
            raise Invalid('Saved conversation rollout is outside the legacy history directory')
        target = destination / source.relative_to(legacy)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def environment(self, spec):
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
               'HOME': str(Path(self.config['launcher_home']).expanduser().resolve()),
               'CODEX_HOME': str(spec['codex_home']), 'XDG_CONFIG_HOME': str(spec['home'] / '.config'),
               'TMPDIR': str(spec['tmp']), 'LANG': 'C.UTF-8', 'TERM':'screen-256color'}
        proxy = self.config.get('codex_proxy')
        if proxy:
            if proxy != 'http://127.0.0.1:17890':
                raise Invalid('Only the explicitly configured local Codex proxy is supported')
            for key in ['HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy']:
                env[key] = proxy
            env['NO_PROXY'] = env['no_proxy'] = 'localhost,127.0.0.1,::1'
        return env

    def command(self, spec, args):
        env = self.environment(spec)
        return shlex.join(['/usr/bin/env', '-i', *[k + '=' + v for k, v in env.items()],
                           *self.config['codex_command'], *args])

    def tmux(self, spec, *args, check=True):
        parent = spec['tmux_socket'].parent
        if parent.exists():
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise Invalid('Tmux socket directory must be private and owned by you')
        return subprocess.run(['/usr/bin/tmux', '-S', str(spec['tmux_socket']), *args],
                              env={'PATH': '/usr/bin:/bin', 'HOME': self.config['launcher_home'], 'LANG': 'C.UTF-8'},
                              capture_output=True, text=True, timeout=10, check=check)

    def remember(self, session, spec, thread=None):
        if session:
            self.state.db.execute('''INSERT INTO session_hosts(session,tmux_name,tmux_socket,agent_socket,codex_home,view_thread)
                VALUES(?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET
                tmux_name=excluded.tmux_name,tmux_socket=excluded.tmux_socket,agent_socket=excluded.agent_socket,
                codex_home=excluded.codex_home,view_thread=COALESCE(excluded.view_thread,session_hosts.view_thread)''',
                (session['id'], spec['name'], str(spec['tmux_socket']), str(spec['socket']), str(spec['codex_home']), thread))

    def start_backend(self, spec, session=None, cwd=None):
        if self.tmux(spec, 'has-session', '-t', '=' + spec['name'], check=False).returncode == 0:
            raise Invalid('This conversation host exists but is unreachable; inspect its own host window before restarting')
        if spec['socket'].exists() or spec['socket'].is_symlink():
            raise Invalid('A stale conversation socket requires operator review')
        directory = str(cwd or self.state.root)
        from .execution_policy import cli_overrides
        command = self.command(spec, [*cli_overrides(self.state.config()),'-c', 'projects.' + json.dumps(directory) + '.trust_level="trusted"',
                                     'app-server', '--listen', 'unix://' + str(spec['socket'])])
        self.tmux(spec, 'new-session', '-d', '-s', spec['name'], '-n', 'host', '-c', str(cwd or self.state.root), command)
        self.tmux(spec, 'set-option', '-w', '-t', spec['name'] + ':host', 'remain-on-exit', 'on')
        self.remember(session, spec)

    def terminal(self, session, cwd, model, effort, replace=False, on_ready=None):
        spec = self.spec(session)
        thread = session.get('thread_id')
        if not thread:
            raise Invalid('Initialize the saved conversation before opening its Codex terminal')
        row = self.state.db.execute('SELECT * FROM session_hosts WHERE session=?', (session['id'],)).fetchone()
        target = spec['name'] + ':Codex'
        exists = self.tmux(spec, 'list-panes', '-t', target, '-F', '#{pane_dead}', check=False)
        if exists.returncode == 0:
            if not row or row['view_thread'] != thread:
                if not replace or not row:
                    raise Invalid('Close the previous Codex view before displaying a replacement conversation')
                from .terminal_bridge import prompt_is_empty
                capture=self.tmux(spec,'capture-pane','-e','-p','-t',target,'-S','-20').stdout
                if exists.stdout.strip()!='0' or not prompt_is_empty(capture):
                    raise Invalid('Finish or clear the previous console draft before resetting the conversation')
                return self._replace_terminal(session,cwd,model,effort,spec,row,on_ready)
            else:
                if exists.stdout.strip() != '0':
                    raise Invalid('The Codex view exited; inspect and close that window before reopening it')
                if on_ready:on_ready()
                return False
        # The owner already registered this exact workspace. Trust only that
        # directory, so the fresh isolated home does not stall on onboarding.
        # A historical untracked pilot is a read-only history view.
        # A remote resume must inherit the host thread's permission settings;
        # Codex rejects CLI sandbox/approval overrides for remote tasks.
        self._launch_terminal(session,cwd,model,effort,spec,'Codex')
        self.tmux(spec, 'select-window', '-t', target)
        self.remember(session, spec, thread)
        return True

    def _launch_terminal(self,session,cwd,model,effort,spec,name):
        args = ['--remote', 'unix://' + str(spec['socket']), '--no-alt-screen', '-C', str(cwd),
                '-m', model,
                '-c', 'model_reasoning_effort=' + json.dumps(effort),
                '-c', 'projects.' + json.dumps(str(cwd)) + '.trust_level="trusted"', 'resume', session['thread_id']]
        self.tmux(spec, 'new-window', '-d', '-t', spec['name'], '-n', name, '-c', str(cwd), self.command(spec, args))
        self.tmux(spec, 'set-option', '-w', '-t', spec['name']+':'+name, 'remain-on-exit', 'on')

    def _replace_terminal(self,session,cwd,model,effort,spec,original,on_ready):
        """Prepare a hidden candidate before retiring the empty original view."""
        from .terminal_bridge import prompt_is_empty
        token=hashlib.sha256(session['thread_id'].encode()).hexdigest()[:12]
        candidate=spec['name']+':Codex-pending-'+token
        previous=spec['name']+':Codex-previous-'+token
        target=spec['name']+':Codex'
        old_session=dict(session,thread_id=original['view_thread'])
        for window in [candidate,previous]:
            if self.tmux(spec,'list-panes','-t',window,check=False).returncode==0:
                raise Invalid('An unfinished context switch requires review in this conversation console')
        old_renamed=False;new_renamed=False;created=False
        try:
            created=True
            self._launch_terminal(session,cwd,model,effort,spec,candidate.rsplit(':',1)[1])
            self.wait_terminal(session,target=candidate)
            if not self._view_quiet(session):raise Invalid('The candidate context is busy or unverified; the original view is preserved')
            if on_ready:on_ready()
            # The owner may have typed into the original view during startup.
            pane=self.tmux(spec,'list-panes','-t',target,'-F','#{pane_dead}').stdout.strip()
            capture=self.tmux(spec,'capture-pane','-e','-p','-t',target,'-S','-20').stdout
            if pane!='0' or not prompt_is_empty(capture) or not self._view_quiet(old_session):
                raise Invalid('The original console changed during reset; its draft and history are preserved')
            self.tmux(spec,'rename-window','-t',target,previous.rsplit(':',1)[1]);old_renamed=True
            self.tmux(spec,'rename-window','-t',candidate,'Codex');new_renamed=True
            self.remember(session,spec,session['thread_id'])
            self.tmux(spec,'select-window','-t',target)
        except BaseException:
            if new_renamed:self.tmux(spec,'rename-window','-t',target,candidate.rsplit(':',1)[1])
            if old_renamed:self.tmux(spec,'rename-window','-t',previous,'Codex')
            self.state.db.execute('UPDATE session_hosts SET view_thread=? WHERE session=?',
                                 (original['view_thread'],session['id']))
            if created:
                # Never discard a draft or active turn in a failed candidate.
                try:
                    pane=self.tmux(spec,'list-panes','-t',candidate,'-F','#{pane_dead}').stdout.strip()
                    capture=self.tmux(spec,'capture-pane','-e','-p','-t',candidate).stdout
                    if pane=='1' or (prompt_is_empty(capture) and self._view_quiet(session)):
                        self.tmux(spec,'kill-window','-t',candidate)
                except Exception:pass
            raise
        # Cleanup failure keeps a labelled idle previous view; the new context
        # is already committed and must not be rolled back or executed again.
        try:
            if self._view_quiet(old_session):self.tmux(spec,'kill-window','-t',previous)
        except Exception:pass
        return False  # Readiness and observation were committed above.

    def _view_quiet(self,session):
        from .codex_bridge import Connection
        connection=None
        try:
            connection=Connection(self.spec(session)['socket'])
            thread=connection.request('thread/read',dict(threadId=session['thread_id'],includeTurns=False),timeout=2)['thread']
            return thread.get('status',{}).get('type')=='idle'
        except Exception:return False
        finally:
            if connection:connection.close()

    def wait_terminal(self, session, target=None):
        """Confirm the newly created view is usable; never type a model prompt."""
        from .terminal_bridge import prompt_is_empty
        spec=self.spec(session);target=target or spec['name']+':Codex'
        deadline=time.monotonic()+15;trusted=False
        while time.monotonic()<deadline:
            pane=self.tmux(spec,'list-panes','-t',target,'-F','#{pane_dead}').stdout.strip()
            if pane!='0':raise Invalid('The conversation console exited; inspect its Codex window')
            text=self.tmux(spec,'capture-pane','-e','-p','-t',target).stdout
            plain=re.sub(r'\x1b\[[0-9;]*m','',text)
            if '› Error:' in plain or 'error: unexpected argument' in plain:
                raise Invalid('Codex could not reopen the remote console; inspect its Codex window')
            if not trusted and re.search(r'^\s*› 1\. Trust and continue\s*$',plain,re.M):
                # This fresh view was launched only for an owner-registered
                # directory. Complete its workspace notice, never tool/hook
                # approval prompts or an unknown screen.
                self.tmux(spec,'send-keys','-t',target,'Enter');trusted=True
            elif prompt_is_empty(text):return
            time.sleep(.2)
        raise Invalid('The conversation console did not reach its empty prompt; inspect its Codex window')


def console(state, session):
    """Safe discovery metadata; native thread IDs and credentials are omitted."""
    row = state.db.execute('SELECT tmux_name,tmux_socket FROM session_hosts WHERE session=?', (session['id'],)).fetchone()
    if row:
        return dict(name=row['tmux_name'], socket=row['tmux_socket'], window='Codex',
                    attach=shlex.join(['tmux', '-S', row['tmux_socket'], 'attach', '-t', row['tmux_name']]))
    binding = state.db.execute('SELECT config_path FROM terminal_attachments WHERE session=?', (session['id'],)).fetchone()
    if binding:
        from .live_state import private_json
        config = private_json(binding['config_path'])
        name=config.get('tmux_session')
        if not name:
            from .terminal_bridge import tmux
            try:name=tmux(config,'display-message','-p','-t',config['pane'],'#{session_name}',timeout=1).strip()
            except Invalid:pass
        return dict(name=name, socket=config['tmux_socket'], window=config['pane'],
                    attach=shlex.join(['tmux', '-S', config['tmux_socket'], 'attach', '-t', name or config['pane']]))
    return None
