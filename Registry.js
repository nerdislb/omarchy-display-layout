.pragma library
var instances = [];
// Hyprland geometry changes can destroy/recreate the bar's output instance.
// Keep the user's in-flight preview intent across that QML lifetime change.
var previewInProgress = false;
var previewScreen = "";
var previewToken = "";
function beginPreview(item) { previewInProgress = true; previewScreen = item.screenName; previewToken = ""; }
function cancelPreview(token) {
  if (token && previewToken && token !== previewToken) return;
  previewInProgress = false; previewToken = "";
}
function recoverPreview(item, token) {
  if (!previewInProgress || (previewToken && previewToken !== token)) return;
  // Never fall back to another output while the intended one is rebuilding.
  if (item.screenName !== previewScreen) return;
  var other = anyOpen();
  if (other && other !== item) return;
  item.ownedToken = token;
  if (!item.opened) item.open();
  if (item.opened) previewToken = token;
  else item.ownedToken = "";
}
function register(item) { instances.push(item); elect(); }
function unregister(item) {
  instances = instances.filter(function(v) { return v !== item; }); elect();
  if (previewInProgress) instances.forEach(function(v) { if (v.screenName === previewScreen && typeof v.refresh === "function") v.refresh(); });
}
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
  var owner = instances.filter(function(v) { return v.ownsPreview; })[0];
  var chosen = owner || onScreen(previewInProgress ? previewScreen : name);
  instances.forEach(function(v) { if (v !== chosen && v.opened) v.close(); });
  if (chosen) chosen.open();
  return chosen;
}
