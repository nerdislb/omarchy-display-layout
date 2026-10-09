.pragma library
var instances = [];
function register(item) { instances.push(item); elect(); }
function unregister(item) { instances = instances.filter(function(v) { return v !== item; }); elect(); }
function elect() { instances.forEach(function(v, i) { v.ipcLeader = i === 0; }); }
function onScreen(name) {
  for (var i = 0; i < instances.length; i++) if (instances[i].screenName === name) return instances[i];
  return instances.length ? instances[0] : null;
}
function anyOpen() {
  for (var i = 0; i < instances.length; i++) if (instances[i].opened) return instances[i];
  return null;
}
function open(name) {
  var chosen = onScreen(name);
  instances.forEach(function(v) { if (v !== chosen && v.opened) v.close(); });
  if (chosen) chosen.open();
  return chosen;
}
