const assert = require('node:assert/strict');
const M = require('../Model.js');
const laptop = {name:'eDP-1', x:0,y:0,width:1920,height:1080,logicalWidth:1920,logicalHeight:1080,scale:1,transform:0,mirrorOf:'none'};
const external = {...laptop,name:'DP-8',x:1920,refreshRate:179.998};
let cases = 0;
function check(name, fn) { fn(); cases++; console.log('PASS '+name); }
check('above retains mode and provides upward crossing', () => {
  const next=M.preset([laptop,external],'DP-8','eDP-1','above');
  assert.equal(next[1].x,0);assert.equal(next[1].y,-1080);assert.equal(next[1].refreshRate,179.998);
  assert.equal(M.validate(next),'');assert.deepEqual(laptop,{...next[0]});
});
check('fractional scaling uses logical size', () => {
  const scaled={...external,logicalWidth:1280,logicalHeight:720,scale:1.5};
  const next=M.preset([laptop,scaled],'DP-8','eDP-1','above');assert.equal(next[1].y,-720);assert.equal(next[1].x,320);assert.equal(M.validate(next),'');
});
check('portrait logical dimensions',()=>{
  const portrait={...external,logicalWidth:1080,logicalHeight:1920,transform:1};
  const next=M.preset([laptop,portrait],'DP-8','eDP-1','right');assert.equal(next[1].x,1920);assert.equal(next[1].y,-420);assert.equal(M.validate(next),'');
});
check('shared corners alone cannot cross',()=>assert.match(M.validate([laptop,{...external,x:1920,y:1080}]),/Join/));
check('gap cannot cross',()=>assert.match(M.validate([laptop,{...external,x:1930}]),/Join/));
check('overlap rejected',()=>assert.match(M.validate([laptop,{...external,x:1800}]),/overlap/));
check('edge snap',()=>{const next=M.snap([laptop,external],'DP-8',3,-1077,20);assert.equal(next[1].x,0);assert.equal(next[1].y,-1080);});
check('topology ignores draft positions but detects scale change',()=>{assert.equal(M.topology([laptop,external]),M.topology(M.preset([laptop,external],'DP-8','eDP-1','above')));assert.notEqual(M.topology([external]),M.topology([{...external,scale:2}]));});
check('connected three-display topology',()=>assert.equal(M.validate([laptop,external,{...laptop,name:'HDMI-A-1',x:0,y:-1080}]),''));
check('bounds support negative positions',()=>assert.deepEqual(M.bounds([laptop,{...external,x:0,y:-1080}]),{x:0,y:-1080,width:1920,height:2160}));
check('active excludes disabled and mirrored outputs',()=>assert.deepEqual(M.active([laptop,{...external,disabled:true},{...external,mirrorOf:'eDP-1'}]),[laptop]));
console.log(cases+' geometry checks passed');
