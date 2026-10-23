'use strict';
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const realFs = require('node:fs/promises');
const extensionRoot = process.env.INTERN_AGENT_TEST_EXTENSION_ROOT || path.resolve(__dirname, '../extension');
const manifest = require(path.join(extensionRoot, 'package.json'));
const namespace = manifest.contributes.commands[0].command.split('.')[0];

const commands = new Map();
const calls = [];
let folderCallback;
let paired = true;
let receiverConnected = true;
let workspaceAttached = true;
let repairApproved = false;
let repairPrompts = 0;
let initApproved = false;
let initPrompts = 0;
let inputs = [];
let initVisible = false;
let openedWorkspace;
let recoverApproved = false;
let internBound = false;
let privateStdin;
let profileVisible = false;
let profileUnsafe = false;
let missingPrivateStdin = false;
let alreadyRegistered = false;
const configurationNamespaces = new Set();
const recoveryPlan = {schema:'intern-agent.repository-recovery.v1',plan_id:'rp_0123456789abcdef',repo_url:'https://github.com/example-owner/project.git',workspace:'recovered-project',branch:'intern-agent/project/bot',tasks:1,turns:3,conversations:2};
const initPlan = { plan_id:'init_0123456789abcdef',workspace:'new-project',repo_url:'https://github.com/example-owner/new-project.git',
  phase:'preview',visibility:'private',source_path:'/data/private-state/runtime/projects/new-project/source',
  steps:['Create the new private GitHub repository','Push one empty initial commit to main','Create one bot code/history branch'] };
const config = { stateDirectory: '/data/private-state', pythonExecutable: '/data/python', autoConnect: true };
const folder = { name: 'quoted workspace', uri: { scheme: 'file', fsPath: '/data/projects/quoted;$(not-a-command)' } };
const vscode = {
  ConfigurationTarget: { Global: 1 },
  EventEmitter: class { constructor() { this.event = () => {}; } fire() {} dispose() {} },
  TreeItem: class { constructor(label,state) { this.label=label; this.collapsibleState=state; } },
  TreeItemCollapsibleState: { Expanded:2, Collapsed:1 },
  Uri: { parse: x => x, file:x=>x }, env: { openExternal: async () => true },
  workspace: {
    isTrusted: true, workspaceFolders: [folder],
    getConfiguration: name => { configurationNamespaces.add(name);return { get: (key, fallback) => config[key] ?? fallback, update: async (key,value) => { config[key] = value; } }; },
    onDidChangeWorkspaceFolders: callback => { folderCallback = callback; return { dispose() {} }; }
  },
  window: {
    createTreeView: () => ({ visible:false, dispose() {} }),
    createOutputChannel: () => ({ appendLine() {}, show() {}, dispose() {} }),
    showErrorMessage: text => { throw new Error(text); },
    showInformationMessage: async () => undefined,
    showInputBox: async () => inputs.shift(),
    showWarningMessage: async (text,options) => {
      if (text === 'Recover saved workspace?') { assert(options.modal); assert(options.detail.includes(recoveryPlan.repo_url)); return recoverApproved ? 'Recover workspace' : undefined; }
      if (text.startsWith('Initialize project')) {
        initPrompts++;assert(options.modal);assert(options.detail.includes(initPlan.repo_url));
        assert(options.detail.includes('empty initial commit'));return initApproved ? 'Apply reviewed plan' : undefined;
      }
      repairPrompts++; return repairApproved ? 'Apply reviewed plan' : undefined;
    },
    showQuickPick: async choices => choices[0]
  },
  commands: { registerCommand: (name,callback) => { commands.set(name,callback); return { dispose() {} }; },
    executeCommand:async (...args)=>{openedWorkspace=args;} }
};
const original = Module._load;
Module._load = function (name, ...rest) {
  if (name === 'vscode') return vscode;
  if (name === 'node:fs/promises') return {...realFs,
    // Every host-profile read is synthetic; the harness never reads the user's profile.
    lstat:async file => {
      if(file.endsWith('/intern-agent/host.json')) {
        if(!profileVisible)throw Object.assign(new Error('Fixture profile absent'),{code:'ENOENT'});
        return {uid:process.getuid(),mode:profileUnsafe?0o644:0o600,isSymbolicLink:()=>false,isFile:()=>true,isDirectory:()=>false};
      }
      if(file === '/data/shared-state')return {uid:process.getuid(),mode:0o700,isSymbolicLink:()=>false,isFile:()=>false,isDirectory:()=>true};
      return realFs.lstat(file);
    },
    readFile:async (file,...args)=>file.endsWith('/intern-agent/host.json') ?
      JSON.stringify({schema:'intern-agent.host-profile.v1',state_directory:'/data/shared-state',python_executable:'/data/shared-python'}) : realFs.readFile(file,...args)
  };
  if (name === 'node:child_process') return { execFile: (executable,args,options,callback) => {
    calls.push({ executable,args,options });
    if(args.includes('github-auth') || args.includes('bootstrap')) {
      setImmediate(()=>callback(null,JSON.stringify({authenticated:true,initialized:true,login:'example-owner'})));
      return missingPrivateStdin?{}:{stdin:{on:()=>{},end:value=>{privateStdin=value;}}};
    }
    if(args.includes('recover-repository')) {
      const action=args[args.indexOf('recover-repository')+1];
      const value=action==='status'?{restored:[{id:'s_recovered_fixture',intern_name:'recovered-bot',workspace:'recovered-project',state:'restored',code_branch:recoveryPlan.branch,role:'controller'}]}:
        alreadyRegistered?{...recoveryPlan,state:'already_registered',workspace:'registered-project'}:recoveryPlan;
      setImmediate(()=>callback(null,JSON.stringify(value)));return;
    }
    if(args.includes('install-skills')) {setImmediate(()=>callback(null,JSON.stringify({installed:['recover-intern-agent']})));return;}
    if (args.includes('project-init')) {
      const action=args[args.indexOf('project-init')+1];if(action==='preview')initVisible=true;
      const payload=action==='apply'?{queued:true,plan_id:initPlan.plan_id}:action==='status'&&!args.includes(initPlan.plan_id)?{initializations:[initPlan]}:initPlan;
      setImmediate(()=>callback(null,JSON.stringify(payload)));return;
    }
    if(args.includes('dashboard')) {
      const payload={initializations:initVisible?[initPlan]:[],workspaces:[{id:'registered-project',repo:'https://github.com/example-owner/project.git'}],
        conversations:[{id:'controller-fixture',name:'Intern Agent Control',workspace:'registered-project',state:'ready',mode:'idle',activity:'waiting',sync:'synced',goal:{status:'active'},pending_questions:2}]};
      setImmediate(()=>callback(null,JSON.stringify(payload)));return;
    }
    if (args.includes('launch')) receiverConnected = true;
    if (args.includes('repair')) {
      const payload = args.includes('preview') ? { plan_id:'fixture-plan', actions:[{kind:'permissions',path:'/data/private-state/live.json'}] } : { applied:true, rollback_id:'fixture-plan' };
      setImmediate(() => callback(null,JSON.stringify(payload))); return;
    }
    setImmediate(() => callback(null, JSON.stringify(args.includes('status') ? { paired, receiver: { fresh: true, connected: receiverConnected }, workspaces: workspaceAttached ? [{ id: 'registered-project', cwd: folder.uri.fsPath, tracked: 1 }] : [],intern_worktrees:internBound?[{session:'s_exact_worktree_bot',workspace:'registered-project',code_path:folder.uri.fsPath}]:[] } : { queued: true })));
  } };
  return original.call(this, name, ...rest);
};
const extension = require(path.join(extensionRoot, 'extension.js'));
Module._load = original;

async function main() {
  const runtime = extension.activate({ extensionPath: '/data/preview-extension', subscriptions: [] });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(new Set(commands.keys()),new Set(manifest.contributes.commands.map(item=>item.command)));
  assert.deepEqual(configurationNamespaces,new Set([namespace]));
  assert.equal(calls.length, 2);
  assert.equal(calls[0].executable, '/data/python');
  assert(calls[1].args.includes('open'));
  assert(calls[1].args.includes('registered-project'));
  assert(!calls[1].args.includes('--new-session'));
  assert(!calls.some(call => call.args.includes('register')));
  assert.equal(calls[0].options.env.PYTHONPATH, '/data/preview-extension/backend');
  assert.equal(extension.workspaceName(folder.uri.fsPath), extension.workspaceName(folder.uri.fsPath));
  assert.notEqual(extension.workspaceName('/a/project'), extension.workspaceName('/b/project'));
  assert(/^[A-Za-z0-9]/.test(extension.workspaceName('/data/_private-workspace')));
  assert(/^[A-Za-z0-9][A-Za-z0-9_-]+$/.test(extension.workspaceName(folder.uri.fsPath)));
  calls.length = 0;
  await commands.get(namespace + '.newSession')();
  assert(calls[1].args.includes('--new-session'));
  workspaceAttached = false;
  calls.length = 0;
  await assert.rejects(runtime.connect(folder), /Register this clean GitHub/);
  assert.equal(calls.length, 1);
  workspaceAttached = true;
  calls.length = 0;
  paired = false;
  await assert.rejects(runtime.connect(folder), /Pair your Feishu/);
  assert.equal(calls.length, 1);
  paired = true;
  receiverConnected = false;
  calls.length = 0;
  await runtime.connect(folder);
  assert(calls[1].args.includes('launch'));
  assert(calls[2].args.includes('status'));
  assert(calls[3].args.includes('open'));
  calls.length = 0;
  vscode.workspace.isTrusted = false;
  folderCallback({ added: [folder] });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(calls.length, 0);
  await assert.rejects(runtime.control('/stop','session'), /Trust/);
  await assert.rejects(runtime.connect(folder), /Trust/);
  vscode.workspace.isTrusted = true;
  calls.length=0;
  await runtime.control('/task start Fix;$(never-execute)','session');
  assert(calls[0].args.includes('/task start Fix;$(never-execute)'));
  assert(calls[0].args.includes('--session'));
  assert.equal(calls[0].options.shell,undefined);
  calls.length=0;
  await commands.get(namespace + '.trigger')({session:{id:'session'}});
  assert(calls[0].args.includes('/trigger all'));
  calls.length=0;
  await commands.get(namespace + '.progress')({session:{id:'session'}});
  assert(calls[0].args.includes('/progress summary'));
  calls.length=0;
  await commands.get(namespace + '.recovery')({session:{id:'session'}});
  assert(calls[0].args.includes('/recovery status'));assert(calls[0].args.includes('--session'));
  calls.length=0;inputs=['/mail status'];
  await commands.get(namespace + '.mail')({session:{id:'session'}});
  assert(calls[0].args.includes('/mail status'));assert(calls[0].args.includes('--session'));
  calls.length=0;inputs=['/research start 10h "Check existing experiment" --checks 3'];
  await commands.get(namespace + '.research')({session:{id:'session'}});
  assert(calls[0].args.includes('/research start 10h "Check existing experiment" --checks 3'));
  assert(calls[0].args.includes('--session'));
  calls.length=0;
  await commands.get(namespace + '.goal')({session:{id:'session'}});
  assert(calls[0].args.includes('/goal'));assert(calls[0].args.includes('--session'));
  assert.equal(calls[0].args[calls[0].args.indexOf('--session')+1],'session');
  const previousPick=vscode.window.showQuickPick;
  vscode.window.showQuickPick=async ()=>'start';
  calls.length=0;inputs=['Reduce p95;$(not-a-shell-command)'];
  await commands.get(namespace + '.goal')({session:{id:'session'}});
  assert(calls[0].args.includes('/goal Reduce p95;$(not-a-shell-command)'));
  assert.equal(calls[0].options.shell,undefined);
  for(const action of ['pause','resume','clear','auto on','auto off']) {
    vscode.window.showQuickPick=async ()=>action;calls.length=0;
    await commands.get(namespace + '.goal')({session:{id:'session'}});
    assert(calls[0].args.includes('/goal '+action));
    assert.equal(calls[0].args[calls[0].args.indexOf('--session')+1],'session');
  }
  vscode.window.showQuickPick=async ()=>undefined;calls.length=0;
  await commands.get(namespace + '.goal')({session:{id:'session'}});
  assert.equal(calls.length,0);
  vscode.window.showQuickPick=async ()=>'start';inputs=[undefined];
  await commands.get(namespace + '.goal')({session:{id:'session'}});
  assert.equal(calls.length,0);vscode.window.showQuickPick=previousPick;
  calls.length=0;
  await commands.get(namespace + '.questions')({session:{id:'session'}});
  assert(calls[0].args.includes('/questions'));assert(calls[0].args.includes('--session'));
  assert.equal(calls[0].args[calls[0].args.indexOf('--session')+1],'session');
  calls.length=0;
  await commands.get(namespace + '.repair')();
  assert.equal(repairPrompts,1);assert.equal(calls.length,1);assert(calls[0].args.includes('preview'));
  repairApproved=true;calls.length=0;
  await commands.get(namespace + '.repair')();
  assert(calls[1].args.includes('apply'));assert(calls[1].args.includes('fixture-plan'));
  calls.length=0;vscode.workspace.isTrusted=false;
  await assert.rejects(commands.get(namespace + '.repair')(),/Trust/);assert.equal(calls.length,0);
  vscode.workspace.isTrusted=true;
  calls.length=0;inputs=['new-project','new-project'];initApproved=false;
  await commands.get(namespace + '.initProject')();
  assert(calls[0].args.includes('project-init'));assert(calls[0].args.includes('preview'));
  assert(!calls.some(call=>call.args.includes('apply')));assert.equal(initPrompts,1);
  calls.length=0;initApproved=true;
  await commands.get(namespace + '.initApply')({initialization:initPlan});
  assert(calls[0].args.includes('status'));assert(calls[1].args.includes('apply'));
  assert(calls[1].args.includes(initPlan.plan_id));assert.equal(calls[1].options.shell,undefined);
  const roots=await runtime.provider.getChildren();
  assert.equal(roots[0].children[0].label,'Intern Agent Control');
  assert(roots[0].children[0].description.includes('goal active'));
  assert(roots[0].children[0].description.includes('2 question(s)'));
  assert(roots.some(root=>root.label==='Project initialization'));
  const job=roots.find(root=>root.label==='Project initialization').children[0];
  assert.equal(job.initialization.workspace,'new-project');assert.equal(job.description,'preview');
  calls.length=0;inputs=['../escape'];
  await assert.rejects(commands.get(namespace + '.initProject')(),/workspace name|letters/i);assert.equal(calls.length,0);
  vscode.workspace.isTrusted=false;
  await assert.rejects(commands.get(namespace + '.initProject')(),/Trust/);
  await assert.rejects(commands.get(namespace + '.initApply')({initialization:initPlan}),/Trust/);assert.equal(calls.length,0);
  vscode.workspace.isTrusted=true;
  initPlan.phase='complete';initPlan.working_directory='/data/private-state/runtime/worktrees/new-project/own-bot-branch';
  await commands.get(namespace + '.initOpenWorkspace')({initialization:initPlan});
  assert.equal(openedWorkspace[0],'vscode.openFolder');assert.equal(openedWorkspace[1],initPlan.working_directory);
  assert.notEqual(openedWorkspace[1],initPlan.source_path);assert(openedWorkspace[2].forceNewWindow);
  calls.length=0;await commands.get(namespace + '.initApply')({initialization:initPlan});
  assert(!calls.some(call=>call.args.includes('apply')));
  calls.length=0;inputs=['example-project'];recoverApproved=false;
  await commands.get(namespace + '.recoverRepository')();
  assert(calls[0].args.includes('preview'));assert(!calls.some(call=>call.args.includes('apply')));
  assert.equal(calls[0].options.timeout,240000);
  calls.length=0;inputs=['example-project'];recoverApproved=true;
  await commands.get(namespace + '.recoverRepository')();
  assert(calls[1].args.includes('apply'));assert(calls[1].args.includes(recoveryPlan.plan_id));
  calls.length=0;await commands.get(namespace + '.reconnectRestored')();
  assert(calls[1].args.includes('/reconnect s_recovered_fixture controller'));
  calls.length=0;inputs=['example-project'];alreadyRegistered=true;
  await commands.get(namespace + '.recoverRepository')();
  assert(calls.some(call=>call.args.includes('/reconnect controller-fixture controller')));
  assert(!calls.some(call=>call.args.includes('apply')));alreadyRegistered=false;
  calls.length=0;inputs=['example-memory-only-fixture'];
  await commands.get(namespace + '.githubCredentials')();
  assert.equal(JSON.parse(privateStdin).pat,'example-memory-only-fixture');
  assert(!JSON.stringify(calls).includes('example-memory-only-fixture'));privateStdin=undefined;
  calls.length=0;inputs=['example-memory-only-fixture'];missingPrivateStdin=true;
  await assert.rejects(commands.get(namespace + '.githubCredentials')(),/handoff is unavailable/);
  assert(!JSON.stringify(calls).includes('example-memory-only-fixture'));missingPrivateStdin=false;
  calls.length=0;inputs=['/data/fresh-fixture-state','/data/fresh-python','example-memory-only-fixture'];
  await commands.get(namespace + '.setup')();
  assert(calls.some(call=>call.args.includes('bootstrap')));
  assert(calls.some(call=>call.args.includes('host-profile')&&call.args.includes('register')));
  assert(!calls.some(call=>call.args.includes('launch')));
  assert.equal(JSON.parse(privateStdin).pat,'example-memory-only-fixture');
  assert(!JSON.stringify(calls).includes('example-memory-only-fixture'));privateStdin=undefined;
  config.stateDirectory='/data/private-state';config.pythonExecutable='/data/python';
  calls.length=0;inputs=['/data/fixture-codex-skills'];
  await commands.get(namespace + '.installSkills')();
  assert(calls[0].args.includes('install-skills'));assert(calls[0].args.includes('/data/fixture-codex-skills'));
  calls.length=0;await commands.get(namespace + '.releaseStatus')();
  assert(calls[0].args.includes('release-sync'));assert(calls[0].args.includes('status'));
  calls.length=0;await commands.get(namespace + '.recoveryStatus')();
  assert(calls[0].args.includes('recover-repository'));assert(calls[0].args.includes('status'));
  calls.length=0;internBound=true;workspaceAttached=false;
  await runtime.connect(folder);
  assert(calls[1].args.includes('/reconnect s_exact_worktree_bot'));assert(!calls.some(call=>call.args.includes('open')));
  internBound=false;workspaceAttached=true;
  calls.length=0;config.stateDirectory='';profileVisible=true;
  assert(await runtime.reuseHostProfile());assert.equal(config.stateDirectory,'/data/shared-state');
  assert.equal(config.pythonExecutable,'/data/shared-python');assert.equal(calls.length,0);
  calls.length=0;config.stateDirectory='';
  extension.activate({ extensionPath:'/data/second-client-extension',subscriptions:[] });
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(config.stateDirectory,'/data/shared-state');
  assert.equal(calls[0].executable,'/data/shared-python');assert(calls.some(call=>call.args.includes('open')));
  assert(!calls.some(call=>call.args.includes('init')||call.args.includes('bootstrap')||call.args.includes('launch')));
  calls.length=0;await commands.get(namespace + '.setup')();
  assert(calls.every(call=>call.args.includes('dashboard')));assert(!calls.some(call=>call.args.includes('init')||call.args.includes('bootstrap')||call.args.includes('launch')));
  profileUnsafe=true;
  await assert.rejects(runtime.reuseHostProfile(),/owner-private/);profileUnsafe=false;
  config.stateDirectory='/data/different-existing-state';
  assert.equal(await runtime.reuseHostProfile(),false);assert.equal(config.stateDirectory,'/data/different-existing-state');
  profileVisible=false;
  calls.length=0;vscode.workspace.isTrusted=false;
  await assert.rejects(commands.get(namespace + '.githubCredentials')(),/Trust/);
  await assert.rejects(commands.get(namespace + '.recoverRepository')(),/Trust/);
  await assert.rejects(commands.get(namespace + '.installSkills')(),/Trust/);
  await assert.rejects(commands.get(namespace + '.goal')({session:{id:'session'}}),/Trust/);
  await assert.rejects(commands.get(namespace + '.questions')({session:{id:'session'}}),/Trust/);
  assert.equal(calls.length,0);vscode.workspace.isTrusted=true;
  config.stateDirectory = '<unconfigured>';
  assert.throws(() => runtime.backend(['status']), /absolute private/);
  console.log('Extension tests passed: isolated namespace, routing, literal argv, reviewed init/recovery, exact-console reconnect, private PAT/bootstrap, shared-host reuse, goal/question controls and release/skill commands.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
