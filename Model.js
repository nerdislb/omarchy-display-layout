// Logical display geometry, shared by QML and the headless regression checks.
function copy(list) { return JSON.parse(JSON.stringify(list || [])); }
function active(list) {
  return (list || []).filter(function(d) { return !d.disabled && (!d.mirrorOf || d.mirrorOf === "none" || d.mirrorOf === -1); });
}
function bounds(list) {
  if (!list.length) return { x: 0, y: 0, width: 1920, height: 1080 };
  var x = Infinity, y = Infinity, right = -Infinity, bottom = -Infinity;
  list.forEach(function(d) {
    x = Math.min(x, d.x); y = Math.min(y, d.y);
    right = Math.max(right, d.x + d.logicalWidth); bottom = Math.max(bottom, d.y + d.logicalHeight);
  });
  return { x: x, y: y, width: right - x, height: bottom - y };
}
function changed(a, b) {
  if (a.length !== b.length) return true;
  return a.some(function(d) { var e = b.filter(function(v) { return v.name === d.name; })[0]; return !e || e.x !== d.x || e.y !== d.y; });
}
function topology(list) {
  return JSON.stringify((list || []).map(function(d) {
    return [d.name, d.width, d.height, d.scale, d.transform, d.disabled, d.mirrorOf, d.logicalWidth, d.logicalHeight];
  }).sort(function(a, b) { return a[0].localeCompare(b[0]); }));
}
function position(list, name, x, y) {
  return list.map(function(d) { var e = Object.assign({}, d); if (e.name === name) { e.x = Math.round(x); e.y = Math.round(y); } return e; });
}
function snap(list, name, x, y, threshold) {
  var d = list.filter(function(v) { return v.name === name; })[0];
  if (!d) return copy(list);
  var xs = [], ys = [];
  list.forEach(function(e) {
    if (e.name === name) return;
    xs.push(e.x - d.logicalWidth, e.x + e.logicalWidth, e.x, e.x + e.logicalWidth - d.logicalWidth,
      e.x + (e.logicalWidth - d.logicalWidth) / 2);
    ys.push(e.y - d.logicalHeight, e.y + e.logicalHeight, e.y, e.y + e.logicalHeight - d.logicalHeight,
      e.y + (e.logicalHeight - d.logicalHeight) / 2);
  });
  function nearest(v, candidates) {
    var result = v, distance = threshold;
    candidates.forEach(function(c) { if (Math.abs(v - c) < distance) { result = c; distance = Math.abs(v - c); } });
    return result;
  }
  return position(list, name, nearest(x, xs), nearest(y, ys));
}
function preset(list, name, anchorName, direction) {
  var d = list.filter(function(v) { return v.name === name; })[0];
  var a = list.filter(function(v) { return v.name === anchorName; })[0];
  if (!d || !a || d === a) return copy(list);
  var x = a.x, y = a.y;
  if (direction === "above" || direction === "below") x += Math.round((a.logicalWidth - d.logicalWidth) / 2);
  else y += Math.round((a.logicalHeight - d.logicalHeight) / 2);
  if (direction === "above") y -= d.logicalHeight;
  else if (direction === "below") y += a.logicalHeight;
  else if (direction === "left") x -= d.logicalWidth;
  else if (direction === "right") x += a.logicalWidth;
  return position(list, name, x, y);
}
function validate(list) {
  if (!list.length) return "No active displays.";
  var graph = list.map(function() { return []; });
  for (var i = 0; i < list.length; i++) {
    var a = list[i];
    if (!isFinite(a.x) || !isFinite(a.y) || a.logicalWidth <= 0 || a.logicalHeight <= 0) return "Invalid display geometry.";
    for (var j = i + 1; j < list.length; j++) {
      var b = list[j];
      var ox = Math.min(a.x + a.logicalWidth, b.x + b.logicalWidth) - Math.max(a.x, b.x);
      var oy = Math.min(a.y + a.logicalHeight, b.y + b.logicalHeight) - Math.max(a.y, b.y);
      if (ox > 0 && oy > 0) return "Displays overlap. Move their edges together.";
      if ((ox === 0 && oy > 0) || (oy === 0 && ox > 0)) { graph[i].push(j); graph[j].push(i); }
    }
  }
  var seen = { 0: true }, queue = [0];
  while (queue.length) graph[queue.shift()].forEach(function(n) { if (!seen[n]) { seen[n] = true; queue.push(n); } });
  return Object.keys(seen).length === list.length ? "" : "Join the display edges so the pointer can cross.";
}
if (typeof module !== "undefined") module.exports = { copy: copy, active: active, bounds: bounds, changed: changed,
  topology: topology, position: position, snap: snap, preset: preset, validate: validate };
