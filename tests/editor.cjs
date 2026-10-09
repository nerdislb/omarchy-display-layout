// Exercise the real QML controller functions with two simulated panel hosts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const M = require('../Model.js');
const source = fs.readFileSync(require.resolve('../Editor.qml'), 'utf8');
const controller = source.slice(source.indexOf('  function focusedInstance()'), source.indexOf('  ShellIpc {'));
const laptop = {name:'eDP-1', x:0,y:1080,width:1920,height:1080,logicalWidth:1920,logicalHeight:1080,scale:1,transform:0,refreshRate:60};
const external = {...laptop,name:'DP-8',x:0,y:0,refreshRate:180};
function host(opened) {
  const later = [];
  const c = {Model:M, Registry:{recoverPreview(){},cancelPreview(){},beginPreview(){}}, Hyprland:{}, live:[external,laptop], draft:M.copy([external,laptop]),
    pending:null, ownedToken:'', selected:'DP-8', anchorName:'eDP-1', stale:false, baselineRevision:'r1',
    frozenBounds:null, opened, errorMessage:'', notice:'', helper:'helper.py', action:'',
    actionProc:{running:false,command:[]},queryProc:{running:false}, Qt:{callLater:fn=>later.push(fn)}};
  c.root = c;
  Object.defineProperties(c,{dirty:{get:()=>M.changed(c.draft,c.live)},
    busy:{get:()=>c.actionProc.running}, ownsPreview:{get:()=>!!c.pending && c.pending.token===c.ownedToken}});
  vm.createContext(c); vm.runInContext(controller,c);
  c.flush=()=>{while(later.length) later.shift()();};
  return c;
}
const token='a'.repeat(32);
const data={ok:true,monitors:[external,laptop],pending:{token,remainingSeconds:19},revision:'r1'};
let cases=0;
function check(name,fn){fn();cases++;console.log('PASS '+name);}
check('closed non-owner never reverts another output preview',()=>{
  const a=host(true),b=host(false);a.ownedToken=token;
  a.receive(data,true);b.receive(data,false);a.flush();b.flush();
  assert.equal(a.actionProc.running,false);assert.equal(b.actionProc.running,false);
});
check('owner closed during in-flight Apply reverts after process settles',()=>{
  const a=host(false);a.ownedToken=token;a.actionProc.running=true;
  a.receive(data,true);a.actionProc.running=false;a.flush();
  assert.equal(a.action,'revert');assert.equal(a.actionProc.command[3],token);
});
check('expired or kept preview clears ownership',()=>{
  const a=host(true);a.ownedToken=token;a.receive({...data,pending:null},true);
  assert.equal(a.ownedToken,'');assert.equal(a.ownsPreview,false);
});
check('external configuration change makes an existing draft stale',()=>{
  const a=host(true);a.draft=M.preset(a.draft,'DP-8','eDP-1','right');
  a.receive({...data,pending:null,revision:'new-config'},false);assert.equal(a.stale,true);
});
check('presets do not apply runtime mutations',()=>{
  const a=host(true);a.arrange('right');assert.equal(a.dirty,true);assert.equal(a.actionProc.running,false);
});
check('registry transfers preview to recreated output instance',()=>{
  const registry={};vm.createContext(registry);
  vm.runInContext(fs.readFileSync(require.resolve('../Registry.js'),'utf8').replace('.pragma library',''),registry);
  const old={screenName:'DP-8',opened:true};registry.register(old);registry.beginPreview(old);registry.unregister(old);
  const hidden={screenName:'eDP-1',opened:false,open(){this.opened=true;}};
  const next={screenName:'DP-8',opened:false,open(){this.opened=true;}};
  registry.register(hidden);registry.register(next);registry.recoverPreview(hidden,token);
  assert.equal(hidden.opened,false);registry.recoverPreview(next,token);
  assert.equal(next.opened,true);assert.equal(next.ownedToken,token);
  registry.cancelPreview();next.opened=false;registry.recoverPreview(next,token);assert.equal(next.opened,false);
});
check('closing an idle owner reverts but closing a non-owner does not',()=>{
  const a=host(false);a.ownedToken=token;a.pending=data.pending;a.handleClosed();
  assert.equal(a.action,'revert');
  const b=host(false);b.pending=data.pending;b.handleClosed();assert.equal(b.actionProc.running,false);
});
check('IPC reopen on another output routes to active preview owner',()=>{
  const registry={};vm.createContext(registry);
  vm.runInContext(fs.readFileSync(require.resolve('../Registry.js'),'utf8').replace('.pragma library',''),registry);
  const a={screenName:'DP-8',opened:true,ownsPreview:true,open(){this.opened=true;},close(){throw Error('owner must not close');}};
  const b={screenName:'eDP-1',opened:false,ownsPreview:false,open(){this.opened=true;}};
  registry.register(a);registry.register(b);registry.open('eDP-1');
  assert.equal(a.opened,true);assert.equal(b.opened,false);
});
check('recovery waits for exact output instead of claiming the fallback',()=>{
  const registry={};vm.createContext(registry);
  vm.runInContext(fs.readFileSync(require.resolve('../Registry.js'),'utf8').replace('.pragma library',''),registry);
  registry.beginPreview({screenName:'DP-8'});
  const wrong={screenName:'eDP-1',opened:false,open(){this.opened=true;}};
  registry.register(wrong);registry.recoverPreview(wrong,token);assert.equal(wrong.opened,false);assert.equal(wrong.ownedToken,undefined);
  const next={screenName:'DP-8',opened:false,open(){this.opened=true;}};
  registry.register(next);registry.recoverPreview(next,token);assert.equal(next.opened,true);
  registry.cancelPreview('b'.repeat(32));assert.equal(registry.previewInProgress,true);
});
check('stale action label on a closed non-owner cannot cancel active intent',()=>{
  const a=host(false);let cancelled=false;a.Registry.cancelPreview=()=>{cancelled=true;};
  a.action='preview';a.handleClosed();assert.equal(cancelled,false);
});
console.log(cases+' editor lifecycle checks passed');
