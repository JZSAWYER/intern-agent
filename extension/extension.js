'use strict';

const vscode = require('vscode');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const os = require('node:os');
const { execFile } = require('node:child_process');

function workspaceName(folderPath) {
  const basename = path.basename(folderPath).replace(/[^A-Za-z0-9_-]/g, '-').replace(/^[^A-Za-z0-9]+/, '') || 'workspace';
  return basename.slice(0, 55) + '-' + crypto.createHash('sha256').update(folderPath).digest('hex').slice(0, 8);
}

function activate(context) {
  const output = vscode.window.createOutputChannel('Intern Agent (Public)');
  const connecting = new Map();
  context.subscriptions.push(output);
  const configuration = () => vscode.workspace.getConfiguration('internAgentPublic');
  const source = path.join(context.extensionPath, 'backend');

  function statePath() {
    const value = configuration().get('stateDirectory', '');
    if (!path.isAbsolute(value) || /[<>\0\n]/.test(value)) {
      throw new Error('Set an absolute private Intern Agent state directory on the Linux host.');
    }
    return value;
  }

  function backend(args, privateInput) {
    if (process.platform !== 'linux') {
      throw new Error('Run Intern Agent on a Linux workspace host, for example through Remote SSH.');
    }
    const state = statePath();
    const executable = configuration().get('pythonExecutable', 'python3');
    if (!executable || /[\0\n]/.test(executable)) throw new Error('Invalid Python executable.');
    return new Promise((resolve, reject) => {
      const child = execFile(executable, ['-m', 'intern_personal.feishu_live', '--state', state, ...args], {
        cwd: source, env: { ...process.env, PYTHONPATH: source }, timeout: ['attach','project-init','recover-repository'].includes(args[0]) ? 240000 : 45000, maxBuffer: 1024 * 1024
      }, (error, stdout) => {
        let result;
        try { result = JSON.parse(stdout.trim()); } catch (_) { /* Never display arbitrary process output. */ }
        if (error || result?.error) {
          reject(new Error(result?.error || 'Backend command failed. Check Python dependencies, private state and permissions.'));
        } else if (!result) {
          reject(new Error('Backend returned an invalid response.'));
        } else {
          resolve(result);
        }
      });
      if (privateInput !== undefined) {
        if (!child?.stdin) { reject(new Error('Private credential handoff is unavailable.')); return; }
        child.stdin.on?.('error',() => reject(new Error('Private credential handoff failed. No secret was placed in command arguments.')));
        child.stdin.end(JSON.stringify(privateInput));
      }
    });
  }

  async function input(title, prompt, password = false) {
    return vscode.window.showInputBox({ title, prompt, password, ignoreFocusOut: true });
  }

  async function setup() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before setup.');
    if (process.platform !== 'linux') throw new Error('Connect to the Linux machine through Remote SSH first.');
    if (await reuseHostProfile()) {
      vscode.window.showInformationMessage('Connected to this Linux host\'s existing Intern Agent state. Sessions and progress are shared with your other SSH editor clients.');
      await refresh(); return;
    }
    const directory = await input('Intern Agent setup', 'Absolute private state directory on the Linux host (use your data volume)');
    if (!directory) return;
    if (!path.isAbsolute(directory) || /[<>\0\n]/.test(directory)) throw new Error('Enter an absolute directory path.');
    const python = await input('Python runtime', 'Python executable on the Linux host with lark-oapi and websockets installed (for example an absolute environment path)');
    if (!python) return;
    await configuration().update('pythonExecutable', python, vscode.ConfigurationTarget.Global);
    await configuration().update('stateDirectory', directory, vscode.ConfigurationTarget.Global);
    let exists = false;
    try { await fs.access(path.join(directory, 'live.json')); exists = true; } catch (_) { /* New setup. */ }
    if (!exists) {
      const authentication = await vscode.window.showQuickPick(['GitHub PAT (recommended)','Existing SSH/Git authentication (advanced)'],{title:'Connect this host to your GitHub projects'});
      if (!authentication) return;
      if (authentication.startsWith('GitHub PAT')) {
        let pat = await input('GitHub authentication','PAT with access to your projects. The account is detected automatically; Codex must be installed and signed in on this host.',true);
        if (!pat) return;
        try { await backend(['bootstrap'],{pat}); } finally { pat = undefined; }
      } else {
        const owner = await input('GitHub account', 'Your GitHub username. Stored only in private relay configuration.');
        if (!owner) return;
        const model = await input('Codex model', 'Model available in your Codex account');
        if (!model) return;
        const executable = await input('Codex executable', 'Absolute path to the Codex executable on the Linux host');
        if (!executable) return;
        await backend(['init', '--github-owner', owner, '--model', model, '--codex-executable', executable]);
      }
    }
    await backend(['host-profile','register']);
    vscode.window.showInformationMessage('Private relay setup ready. Set Feishu credentials, start the relay, then pair your management group.');
  }

  async function reuseHostProfile() {
    if (process.platform !== 'linux' || !vscode.workspace.isTrusted) return false;
    const file = path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(), '.local', 'state'), 'intern-agent', 'host.json');
    let info;
    try { info = await fs.lstat(file); } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
    if (!info.isFile() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & 0o077)) throw new Error('The host relay profile must be an owner-private regular file.');
    const profile = JSON.parse(await fs.readFile(file, 'utf8'));
    if (profile.schema !== 'intern-agent.host-profile.v1' || ![profile.state_directory,profile.python_executable].every(value => typeof value === 'string' && path.isAbsolute(value) && !/[<>\0\r\n]/.test(value))) throw new Error('Invalid host relay profile.');
    const root = await fs.lstat(profile.state_directory);
    if (!root.isDirectory() || root.isSymbolicLink() || root.uid !== process.getuid() || (root.mode & 0o077)) throw new Error('The existing host relay state is unavailable or not private.');
    const configured = configuration().get('stateDirectory','');
    if (configured && path.resolve(configured) !== path.resolve(profile.state_directory)) return false;
    await configuration().update('stateDirectory',profile.state_directory,vscode.ConfigurationTarget.Global);
    await configuration().update('pythonExecutable',profile.python_executable,vscode.ConfigurationTarget.Global);
    return true;
  }

  async function githubCredentials() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before setting GitHub authentication.');
    let pat = await input('GitHub authentication', 'GitHub PAT with access to your project repositories. Saved as an owner-private file on this Linux host; never put in Git, logs or command arguments.', true);
    if (!pat) return;
    try {
      const result = await backend(['github-auth'],{pat});
      vscode.window.showInformationMessage('GitHub account verified: ' + result.login + '. Access to each repository is checked when it is used.');
    } finally { pat = undefined; }
  }

  async function recoverRepository() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before recovery.');
    const name = await input('Recover a GitHub workspace', 'Repository name, owner/repository, or credential-free GitHub URL. Uploaded histories are restored; old prompts and jobs are not replayed.');
    if (!name) return;
    const plan = await backend(['recover-repository','preview',name]);
    if (plan.state === 'already_registered') {
      const data = await backend(['dashboard']);
      const selected = await vscode.window.showQuickPick((data.conversations || []).filter(s => s.workspace === plan.workspace).map(session => ({
        label:session.name || session.id,description:session.state,session
      })),{title:'Reconnect an existing workspace conversation'});
      if (selected) await backend(['control','/reconnect '+selected.session.id+(selected.session.name === 'Intern Agent Control' ? ' controller' : '')]);
      vscode.window.showInformationMessage('Reusing the existing host workspace and conversations. No duplicate clone or relay was created.');
      return;
    }
    const detail = ['Repository: '+plan.repo_url,'Workspace: '+plan.workspace,'Archive branch: '+plan.branch,
      'Saved turns: '+plan.turns+' · Tasks: '+plan.tasks+' · Conversations: '+plan.conversations,
      'Recovery plan: '+plan.plan_id,'Existing work is preserved. Codex/Feishu authentication is configured separately when missing.'].join('\n');
    const choice = await vscode.window.showWarningMessage('Recover saved workspace?',{modal:true,detail},'Recover workspace');
    if (choice !== 'Recover workspace') return;
    await backend(['recover-repository','apply',plan.plan_id]);
    vscode.window.showInformationMessage('History, tasks and saved preferences recovered. Pair the owner if needed, then use Reconnect Restored Conversation.');
    await refresh();
  }

  async function reconnectRestored() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before reconnecting a conversation.');
    const saved = await backend(['recover-repository','status']);
    const selected = await vscode.window.showQuickPick((saved.restored || []).map(session => ({label:session.intern_name,description:session.workspace+' · '+session.state,detail:session.code_branch,session})),{title:'Reconnect a recovered conversation'});
    if (selected) {
      await backend(['control','/reconnect '+selected.session.id+(selected.session.role === 'controller' ? ' controller' : '')]);
      vscode.window.showInformationMessage('Reconnection queued in the existing relay. The dashboard and management group will show its chat.');
    }
  }

  async function credentials() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before storing credentials.');
    const directory = statePath();
    const info = await fs.lstat(directory);
    if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o077) || info.uid !== process.getuid()) {
      throw new Error('Run setup first; the state directory must be a real private directory with permission 0700.');
    }
    const appId = await input('Feishu Intern Agent app', 'App ID of your separate Intern Agent self-built app');
    if (!appId) return;
    if (!/^cli_[A-Za-z0-9_]+$/.test(appId)) throw new Error('Invalid Feishu App ID.');
    let appSecret = await input('Feishu credentials', 'App Secret. Saved privately on the Linux host; never sent as a command argument.', true);
    if (!appSecret) return;
    if (appSecret.length < 8 || /[<>\s]/.test(appSecret)) throw new Error('Enter the actual App Secret.');
    const temporary = path.join(directory, '.credential-handoff-' + crypto.randomBytes(12).toString('hex'));
    let handle;
    try {
      handle = await fs.open(temporary, 'wx', 0o600);
      await handle.writeFile(JSON.stringify({ app_id: appId, app_secret: appSecret }) + '\n');
      await handle.sync();
      await handle.close(); handle = undefined;
      await fs.rename(temporary, path.join(directory, 'credentials.json'));
    } finally {
      appSecret = undefined;
      if (handle) await handle.close();
      await fs.unlink(temporary).catch(() => {});
    }
    vscode.window.showInformationMessage('Feishu credentials stored privately. Start the relay next.');
  }

  async function pair() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before pairing.');
    const result = await backend(['pair-code']);
    let code = (await fs.readFile(result.pairing_code_file, 'utf8')).trim();
    try {
      // Display only the explicit one-time pairing capability to its local owner.
      // The App Secret is never displayed here or added to the output channel.
      await vscode.window.showInputBox({ title: 'Pair your management group', value: '/pair ' + code,
        prompt: 'Copy this one-time command into your Feishu management group containing the bot. Expires in 15 minutes.',
        ignoreFocusOut: true });
    } finally { code = undefined; }
  }

  async function connect(folder, newSession = false) {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before connecting it to Intern Agent.');
    if (folder.uri.scheme !== 'file') throw new Error('Only filesystem workspace folders are supported.');
    const key = folder.uri.fsPath;
    if (connecting.has(key)) return connecting.get(key);
    const operation = (async () => {
      let status = await backend(['status']);
      if (!status.paired) throw new Error('Pair your Feishu management group before connecting workspaces.');
      if (!status.receiver?.fresh || !status.receiver?.connected) {
        await backend(['launch']);
        for (let attempt = 0; attempt < 15; attempt++) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          status = await backend(['status']);
          if (status.receiver?.fresh && status.receiver?.connected) break;
        }
        if (!status.receiver?.fresh || !status.receiver?.connected) {
          throw new Error('Receiver is not connected yet. Check app credentials, availability and network in Show Status.');
        }
      }
      const canonical = await fs.realpath(key).catch(() => path.resolve(key));
      const registered = (status.workspaces || []).find(item => item.tracked && item.cwd === canonical);
      const intern = (status.intern_worktrees || []).find(item => item.code_path === canonical);
      const name = registered?.id || intern?.workspace;
      if (!name) throw new Error('Register this clean GitHub checkout using Attach Workspace to GitHub first.');
      if (intern && !newSession) await backend(['control','/reconnect '+intern.session]);
      else await backend(['open', name, ...(newSession ? ['--new-session'] : [])]);
      output.appendLine('Workspace chat requested: ' + folder.name + '. Check the management group for its link.');
    })();
    connecting.set(key, operation);
    try { return await operation; } finally { connecting.delete(key); }
  }

  async function chooseFolder() {
    const folders = vscode.workspace.workspaceFolders || [];
    if (!folders.length) throw new Error('Open a workspace folder first.');
    if (folders.length === 1) return folders[0];
    const selected = await vscode.window.showQuickPick(folders.map(folder => ({ label: folder.name, folder })),
      { title: 'Choose an Intern Agent workspace' });
    return selected?.folder;
  }

  async function attachWorkspace() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before attaching it.');
    const folder = await chooseFolder();
    if (!folder || folder.uri.scheme !== 'file') return;
    const canonical = await fs.realpath(folder.uri.fsPath);
    const status = await backend(['status']);
    const existing = (status.workspaces || []).find(item => item.tracked && item.cwd === canonical);
    if (existing) {
      vscode.window.showInformationMessage('Workspace already attached. New bots use one branch for code and history. Use Connect Workspace to Feishu.');
      return;
    }
    const repository = await input('GitHub workspace', 'GitHub repository URL for this clean checkout');
    if (!repository) return;
    const name = workspaceName(canonical);
    const answer = await vscode.window.showInformationMessage('Register this GitHub workspace? Each new bot will upload its code and Markdown history to one permanent branch.', 'Attach workspace');
    if (answer !== 'Attach workspace') return;
    await backend(['attach', name, folder.uri.fsPath, '--repo', repository]);
    vscode.window.showInformationMessage('Workspace attached. Connect it to Feishu to create the bot branch.');
  }

  async function reviewInitialization(plan) {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before initializing a project.');
    if (plan.phase === 'complete') {
      vscode.window.showInformationMessage('Project already initialized. Its existing bot and history are preserved.');
      return;
    }
    const detail = ['Repository: ' + plan.repo_url + ' (private)', 'Storage: ' + plan.source_path,
      ...(plan.steps || []).map((step, index) => (index + 1) + '. ' + step),
      'Existing repositories and branches are preserved. This creates no PR or merge.',
      ...(plan.error_message ? ['Previous blocker: ' + plan.error_message, 'The same plan resumes saved steps; ambiguous creation requires inspection.'] : [])].join('\n');
    const choice = await vscode.window.showWarningMessage('Initialize project ' + plan.workspace + '?',
      { modal: true, detail }, 'Apply reviewed plan');
    if (choice !== 'Apply reviewed plan') return;
    await backend(['project-init', 'apply', plan.plan_id]);
    vscode.window.showInformationMessage('Initialization queued. Follow its stages in the dashboard or /init status in Feishu.');
    await refresh();
  }

  async function initializeProject() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before initializing a project.');
    const name = await input('Initialize a new project', 'Workspace name for a new private GitHub project. Existing repositories are left untouched.');
    if (!name) return;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(name) || ['apply', 'status'].includes(name.toLowerCase())) throw new Error('Use letters, numbers, hyphens or underscores; apply and status are reserved.');
    const repository = await vscode.window.showInputBox({ title: 'New GitHub repository', value: name,
      prompt: 'Repository name under your configured personal GitHub account. It must not already exist. Visibility: private.', ignoreFocusOut: true });
    if (!repository) return;
    if (!/^[A-Za-z0-9_.-]{1,100}$/.test(repository) || ['.', '..'].includes(repository) || repository.toLowerCase().endsWith('.git')) throw new Error('Enter a repository name without a URL, path or .git suffix.');
    const plan = await backend(['project-init', 'preview', name, '--repo', repository]);
    await refresh();
    await reviewInitialization(plan);
  }

  async function selectInitialization(node) {
    if (node?.initialization) return node.initialization;
    const data = await backend(['project-init', 'status']);
    const choice = await vscode.window.showQuickPick((data.initializations || []).map(plan => ({
      label: plan.workspace, description: plan.phase, detail: plan.repo_url, plan })), { title: 'Project initialization' });
    return choice?.plan;
  }

  function command(name, action) {
    context.subscriptions.push(vscode.commands.registerCommand(name, async (...args) => {
      try { await action(...args); } catch (error) { vscode.window.showErrorMessage('Intern Agent: ' + error.message); }
    }));
  }

  command('internAgentPublic.setup', setup);
  command('internAgentPublic.credentials', credentials);
  command('internAgentPublic.githubCredentials',githubCredentials);
  command('internAgentPublic.recoverRepository',recoverRepository);
  command('internAgentPublic.reconnectRestored',reconnectRestored);
  command('internAgentPublic.recoveryStatus',async () => { output.appendLine(JSON.stringify(await backend(['recover-repository','status']),null,2)); output.show(true); });
  command('internAgentPublic.releaseStatus',async () => { output.appendLine(JSON.stringify(await backend(['release-sync','status']),null,2)); output.show(true); });
  command('internAgentPublic.installSkills',async () => {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before installing Codex skills.');
    const destination = await input('Install bundled Codex skills','Absolute Codex skills directory on this Linux host. Existing modified skills are preserved.');
    if (!destination) return;
    const result = await backend(['install-skills',destination]);
    vscode.window.showInformationMessage('Installed Codex skills: '+result.installed.join(', ')+'. Recovery instructions are also included in the release bundle.');
  });
  command('internAgentPublic.pair', pair);
  command('internAgentPublic.attach', attachWorkspace);
  command('internAgentPublic.initProject', initializeProject);
  command('internAgentPublic.initApply', async node => {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before initializing a project.');
    const selected = await selectInitialization(node); if (!selected) return;
    const current = await backend(['project-init', 'status', selected.plan_id]);
    await reviewInitialization(current);
  });
  command('internAgentPublic.initStatus', async node => {
    const selected = await selectInitialization(node); if (!selected) return;
    const current = await backend(['project-init', 'status', selected.plan_id]);
    output.appendLine(JSON.stringify(current, null, 2)); output.show(true);
  });
  command('internAgentPublic.initOpenWorkspace', async node => {
    const selected = await selectInitialization(node); if (!selected) return;
    const plan = await backend(['project-init', 'status', selected.plan_id]);
    if (plan.phase !== 'complete' || !plan.working_directory || !path.isAbsolute(plan.working_directory)) throw new Error('Wait for initialization to complete before opening its bot workspace.');
    const template = context.extensionUri || vscode.workspace.workspaceFolders?.[0]?.uri;
    const uri = template?.with ? template.with({ path: plan.working_directory, query: '', fragment: '' }) : vscode.Uri.file(plan.working_directory);
    await vscode.commands.executeCommand('vscode.openFolder', uri, { forceNewWindow: true });
  });
  command('internAgentPublic.start', async () => { if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before starting the relay.'); await backend(['launch']); vscode.window.showInformationMessage('Relay launched. Check status and pair your management group.'); });
  command('internAgentPublic.open', async () => { const folder = await chooseFolder(); if (folder) await connect(folder); });
  command('internAgentPublic.newSession', async () => { const folder = await chooseFolder(); if (folder) await connect(folder, true); });
  command('internAgentPublic.status', async () => {
    const result = await backend(['status']);
    output.appendLine(JSON.stringify(result, null, 2));
    output.show();
    if (result.paired && !configuration().get('autoConnect', false)) {
      const answer = await vscode.window.showInformationMessage('Owner paired. Automatically connect trusted workspaces when opened?', 'Enable');
      if (answer === 'Enable') await configuration().update('autoConnect', true, vscode.ConfigurationTarget.Global);
    }
  });

  const changed = new vscode.EventEmitter();
  let snapshot;
  let refreshing;
  const provider = {
    onDidChangeTreeData: changed.event,
    getTreeItem: node => node,
    async getChildren(node) {
      if (node) return node.children || [];
      if (!snapshot) await refresh();
      if (!snapshot) return [new vscode.TreeItem('Run Set Up Personal Relay, then Refresh')];
      const roots = (snapshot.workspaces || []).map(workspace => {
        const root = new vscode.TreeItem(workspace.id, vscode.TreeItemCollapsibleState.Expanded);
        root.description = 'uploads: ' + snapshot.uploads_pending;
        root.tooltip = workspace.repo + (workspace.management_branch ? '\nLegacy management: ' + workspace.management_branch : '\nOne code/history branch per bot');
        root.children = (snapshot.conversations || []).filter(s => s.workspace === workspace.id).map(s => {
          const item = new vscode.TreeItem((s.name || s.id) + (s.attached_terminal ? ' (attached terminal)' : ''), vscode.TreeItemCollapsibleState.Collapsed);
          item.session = s;
          item.contextValue = 'internAgentPublicConversation';
          item.description = [s.mode, s.activity, s.sync, ...(s.goal ? ['goal ' + s.goal.status] : []), ...(s.pending_questions ? [s.pending_questions + ' question(s)'] : [])].join(' · ');
          item.tooltip = 'Branch: ' + (s.branch || '(unbound)') + '\nTask: ' + (s.task?.name || '(idle conversation)');
          item.children = ['Task: ' + (s.task?.name || '(none)'), 'Branch: ' + (s.branch || '(none)'), 'State: ' + s.state,
            'Git: ' + s.sync, 'Model: ' + (s.model || 'configured default'),
            'Feishu delivery: ' + (s.reply_delivery || 'not ready'),
            ...Object.entries(s.file_transfers || {}).map(([state,count]) => 'Files ' + state + ': ' + count),
            ...Object.entries(s.peer_messages || {}).map(([state,count]) => 'Bot messages ' + state + ': ' + count),
            'Recovery: ' + (s.recovery?.enabled ? 'enabled' : 'disabled'),
            ...(s.research_watches || []).filter(w => ['waiting','queued','running','paused','review_needed'].includes(w.state)).map(w => 'Research ' + w.state + ': ' + w.next_check_utc + ' · ' + w.checks + '/' + w.max_checks + ' checks'),
            ...(s.recovery?.episodes || []).slice(0,3).map(ep => 'Recovery ' + ep.id + ': ' + ep.state + ' · attempts ' + ep.attempts)].map(label => new vscode.TreeItem(label));
          return item;
        });
        for (const team of (snapshot.teams || []).filter(t => t.workspace === workspace.id)) {
          const item = new vscode.TreeItem('Team: ' + team.name, vscode.TreeItemCollapsibleState.Collapsed);
          item.description = team.state + ' · up to ' + team.max_workers + ' workers';
          item.children = ['Team: ' + team.id, 'Lead: ' + team.lead, 'Assignments: use /team status'].map(label => new vscode.TreeItem(label));
          root.children.push(item);
        }
        return root;
      });
      if (snapshot.initializations?.length) {
        const root = new vscode.TreeItem('Project initialization', vscode.TreeItemCollapsibleState.Expanded);
        root.children = snapshot.initializations.map(plan => {
          const item = new vscode.TreeItem(plan.workspace, vscode.TreeItemCollapsibleState.Collapsed);
          item.initialization = plan; item.contextValue = plan.phase === 'complete' ? 'internAgentPublicInitializationComplete' : 'internAgentPublicInitialization';
          item.description = plan.phase + (plan.error_type ? ' · needs review' : '');
          item.tooltip = plan.repo_url + ' (private)\n' + plan.source_path + (plan.error_message ? '\n' + plan.error_message : '');
          item.children = ['Repository: ' + plan.repo_url, 'Visibility: private', 'Stage: ' + plan.phase,
            'Storage: ' + plan.source_path, ...(plan.error_message ? ['Blocker: ' + plan.error_message] : [])].map(label => new vscode.TreeItem(label));
          if (plan.phase === 'complete' && plan.working_directory) {
            const folder = new vscode.TreeItem('Open bot workspace in VS Code');
            folder.command = { command: 'internAgentPublic.initOpenWorkspace', title: 'Open bot workspace', arguments: [{ initialization: plan }] };
            item.children.push(new vscode.TreeItem('Branch: ' + plan.code_branch), folder);
          }
          if (plan.chat_url) {
            const chat = new vscode.TreeItem('Open project Feishu chat');
            chat.command = { command: 'internAgentPublic.openChat', title: 'Open project Feishu chat', arguments: [{ initialization: plan }] };
            item.children.push(chat);
          }
          return item;
        });
        roots.push(root);
      }
      return roots;
    }
  };
  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => { snapshot = await backend(['dashboard']); changed.fire(); })();
    try { await refreshing; } finally { refreshing = undefined; }
  }
  const tree = vscode.window.createTreeView('internAgentPublic.sessions', { treeDataProvider: provider });
  context.subscriptions.push(tree, changed);
  // Refresh only when visible. A closed tree never starts services or creates chats.
  const timer = setInterval(() => { if (tree.visible && configuration().get('stateDirectory', '')) void refresh().catch(() => {}); }, 10000);
  timer.unref?.();
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
  command('internAgentPublic.refresh', refresh);
  command('internAgentPublic.doctor', async () => { const report = await backend(['doctor']); output.appendLine(JSON.stringify(report, null, 2)); output.show(); });
  command('internAgentPublic.repair', async () => {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before reviewing local repairs.');
    const plan = await backend(['repair', 'preview']);
    output.appendLine(JSON.stringify(plan, null, 2)); output.show();
    if (!plan.actions?.length) { vscode.window.showInformationMessage('No automatic local repairs available. Review diagnostics for manual setup steps.'); return; }
    const description = plan.actions.map(a => a.kind + ': ' + a.path).join('\n');
    const answer = await vscode.window.showWarningMessage('Apply these local repairs?\n' + description + '\nNo services or accounts are changed. A rollback receipt will be saved.', { modal: true }, 'Apply reviewed plan');
    if (answer !== 'Apply reviewed plan') return;
    const result = await backend(['repair', 'apply', plan.plan_id]);
    output.appendLine(JSON.stringify(result, null, 2)); output.show();
    const rollback = await vscode.window.showInformationMessage('Repairs applied. Rollback ID: ' + result.rollback_id, 'Roll back');
    if (rollback === 'Roll back') await backend(['repair', 'rollback', result.rollback_id]);
    await refresh();
  });
  command('internAgentPublic.openChat', async node => {
    const s = node?.session || node?.initialization;
    if (!s?.chat_url || !/^https:\/\/applink\.feishu\.cn\//.test(s.chat_url)) throw new Error('Choose a ready conversation with a Feishu link.');
    await vscode.env.openExternal(vscode.Uri.parse(s.chat_url));
  });
  async function control(text, session) {
    if (!vscode.workspace.isTrusted) throw new Error('Trust the workspace before controlling an agent.');
    if (!/^\/[a-z]/i.test(text) || text.length > 16000) throw new Error('Enter a supported slash command.');
    await backend(['control', text, ...(session ? ['--session', session] : [])]);
    vscode.window.showInformationMessage('Command queued. The result appears in Feishu.');
    await refresh();
  }
  async function selectSession(node) {
    if (node?.session) return node.session;
    const data = await backend(['dashboard']);
    const choice = await vscode.window.showQuickPick((data.conversations || []).map(s => ({ label: s.name || s.id, description: s.workspace + ' · ' + s.state, session: s })), { title: 'Choose conversation' });
    return choice?.session;
  }
  for (const [name, values] of [['trigger',['all','mentions']], ['progress',['summary','messages','off']]]) {
    command('internAgentPublic.' + name, async node => {
      const s = await selectSession(node); if (!s) return;
      const mode = await vscode.window.showQuickPick(values, { title: name + ' mode' });
      if (mode) await control('/' + name + ' ' + mode,s.id);
    });
  }
  command('internAgentPublic.skills', async node => {
    const s = await selectSession(node); if (!s) return;
    const text = await input('Pinned reference skills', '/skills list, add KEY GITHUB_URL COMMIT DIRECTORY, enable KEY, disable KEY, inspect KEY, refresh KEY');
    if (text && /^\/skills(\s|$)/i.test(text)) await control(text,s.id);
  });
  command('internAgentPublic.recovery', async node => {
    const s = await selectSession(node); if (!s) return;
    const action = await vscode.window.showQuickPick(['status','inbox','on','off'], { title: 'Automatic recovery' });
    if (action) await control('/recovery ' + action,s.id);
  });
  command('internAgentPublic.mail', async node => {
    const s = await selectSession(node); if (!s) return;
    const text = await input('Bot communication', '/mail list, ask TARGET QUESTION, note TARGET TEXT, status ID, read ID, ack ID, reply ID ANSWER, or cancel ID');
    if (text && /^\/mail(\s|$)/i.test(text)) await control(text,s.id);
  });
  command('internAgentPublic.research', async node => {
    const s = await selectSession(node); if (!s) return;
    const text = await input('Research wake-ups', '/research status, start 10h "Check existing experiment" --every 1h --checks 12 --for 48h, pause ID, resume ID, finish ID, or cancel ID');
    if (text && /^\/research(\s|$)/i.test(text)) await control(text,s.id);
  });
  command('internAgentPublic.goal', async node => {
    const s = await selectSession(node); if (!s) return;
    const action = await vscode.window.showQuickPick(['status','start','pause','resume','clear','auto on','auto off'], { title: 'Persistent goal' });
    if (!action) return;
    if (action === 'start') {
      const objective = await input('Goal objective', 'Outcome, verification and constraints. The goal continues after each Git upload.');
      if (objective) await control('/goal ' + objective,s.id);
    } else await control('/goal' + (action === 'status' ? '' : ' ' + action),s.id);
  });
  command('internAgentPublic.questions', async node => {
    const s = await selectSession(node); if (!s) return;
    await control('/questions',s.id);
  });
  for (const [name, text] of [['stop','/stop'], ['pause','/pause'], ['resume','/resume'], ['compact','/compact'], ['usage','/usage']]) {
    command('internAgentPublic.' + name, async node => { const s = await selectSession(node); if (s) await control(text,s.id); });
  }
  for (const [name, text] of [['restart','/restart'], ['archive','/archive']]) {
    command('internAgentPublic.' + name, async node => {
      const s = await selectSession(node); if (!s) return;
      const choice = await vscode.window.showInformationMessage(name + ' conversation ' + s.id + '? Git history and branches are preserved.', 'Confirm');
      if (choice === 'Confirm') await control(text,s.id);
    });
  }
  command('internAgentPublic.task', async node => {
    const s = await selectSession(node); if (!s) return;
    const text = await input('Task command', 'For example: /task start Fix parser, /task pause, /task status, or /task finish Accepted');
    if (text && /^\/(task|chat)(\s|$)/i.test(text)) await control(text,s.id);
  });
  command('internAgentPublic.team', async () => {
    const text = await input('Team management', 'Use /team create "NAME" WORKSPACE LEAD_SESSION [1-3], add, assign, run, mail, accept, changes, or status. No agents launch automatically.');
    if (text && /^\/team(\s|$)/i.test(text)) await control(text);
  });

  async function autoConnect(folders) {
    if (!configuration().get('autoConnect', false) || !configuration().get('stateDirectory', '') || !vscode.workspace.isTrusted) return;
    for (const folder of folders) {
      try { await connect(folder); } catch (error) { output.appendLine('Automatic connection pending: ' + error.message); }
    }
  }
  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(event => { void autoConnect(event.added); }));
  if (!configuration().get('stateDirectory','') && vscode.workspace.isTrusted) {
    void reuseHostProfile().then(() => autoConnect(vscode.workspace.workspaceFolders || [])).catch(error => output.appendLine('Host connection pending: '+error.message));
  } else void autoConnect(vscode.workspace.workspaceFolders || []);
  return { connect, backend, provider, refresh, control, reuseHostProfile };
}

exports.activate = activate;
exports.deactivate = () => {};
exports.workspaceName = workspaceName;
