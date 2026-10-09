#!/usr/bin/env python3
"""Position-only Hyprland 0.56 Lua display editor; standard library only.

All mutations require explicit preview/keep/revert. snapshot/status are read-only
except for recovery of an expired preview. The systemd watchdog owns rollback,
not the panel process. Run tests without a compositor using unittest discovery.
"""
from __future__ import annotations

import base64
import contextlib
import difflib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

TIMEOUT = 20
BEGIN = b'\n-- BEGIN OMARCHY DISPLAY LAYOUT (managed)\n'
END = b'-- END OMARCHY DISPLAY LAYOUT (managed)\n'
NAME = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
TOKEN = re.compile(r'[a-f0-9]{32}\Z')
# Hyprland 0.56.2 LuaBindingsConfigRules.cpp merges only EXACT output selectors.
# Never create a connector override of an existing description rule: that loses
# options that are not in the public monitor JSON (ICC, VRR policy, HDR, etc.).



class LayoutError(Exception):
    pass


def lua_string(value):
    """Lua short string; encode bytes so JSON-only escapes never reach Lua."""
    return '"' + ''.join(chr(b) if 32 <= b < 127 and b not in (34, 92)
                         else '\\%03d' % b for b in value.encode('utf-8')) + '"'


def literal_selectors(source):
    """Read literal output keys in hl.monitor table calls, never comment examples.

    This is a lexer, not a Lua evaluator: computed selectors remain unsupported.
    Long strings/comments are skipped as single tokens, so their sample code
    cannot masquerade as actual monitor declarations.
    """
    tokens, i = [], 0
    while i < len(source):
        if source[i].isspace():
            i += 1
            continue
        comment = source.startswith('--', i)
        start = i + 2 if comment else i
        long_open = re.match(r'\[(=*)\[', source[start:])
        if long_open:
            closer = ']' + long_open[1] + ']'
            end = source.find(closer, start + len(long_open[0]))
            if end < 0:
                raise LayoutError('Unterminated Lua long string/comment in monitors.lua.')
            if not comment:
                tokens.append(('string', source[start + len(long_open[0]):end]))
            i = end + len(closer)
            continue
        if comment:
            end = source.find('\n', i)
            i = len(source) if end < 0 else end + 1
            continue
        if source[i] in ('"', "'"):
            quote, j = source[i], i + 1
            while j < len(source):
                if source[j] == '\\':
                    j += 2
                elif source[j] == quote:
                    break
                else:
                    j += 1
            if j >= len(source):
                raise LayoutError('Unterminated Lua string in monitors.lua.')
            value = source[i + 1:j]
            # An escaped selector cannot be confidently compared without a Lua
            # string parser. Refuse it rather than inventing a new rule.
            tokens.append(('string' if '\\' not in value else 'escaped', value))
            i = j + 1
            continue
        word = re.match(r'[A-Za-z_][A-Za-z_0-9]*', source[i:])
        if word:
            tokens.append(('token', word[0]))
            i += len(word[0])
        else:
            tokens.append(('token', source[i]))
            i += 1
    result = set()
    opening = [('token', x) for x in ('hl', '.', 'monitor', '(', '{')]
    for i in range(len(tokens) - 4):
        if tokens[i:i + 5] != opening:
            continue
        depth, j = 1, i + 5
        while j < len(tokens) and depth:
            t = tokens[j]
            if t == ('token', '{'):
                depth += 1
            elif t == ('token', '}'):
                depth -= 1
            elif depth == 1 and tokens[j:j + 2] == [('token', 'output'), ('token', '=')]:
                if j + 2 < len(tokens) and tokens[j + 2][0] == 'string':
                    # A concatenated literal is still a computed selector.
                    if j + 3 < len(tokens) and tokens[j + 3] in (('token', ','), ('token', '}'), ('token', ';')):
                        result.add(tokens[j + 2][1])
            j += 1
    return result


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def file_bytes(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def normalize(monitors):
    if not isinstance(monitors, list):
        raise LayoutError('Hyprland returned invalid monitor data.')
    result, seen = [], set()
    for raw in monitors:
        m = dict(raw)
        name = m.get('name', '')
        if not NAME.fullmatch(name) or name in seen:
            raise LayoutError('Hyprland returned an unsafe or duplicate connector name.')
        seen.add(name)
        if m.get('disabled', False):
            continue
        for field in ('width', 'height', 'scale', 'refreshRate', 'x', 'y', 'transform'):
            v = m.get(field)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise LayoutError('Invalid monitor field: ' + field)
        if min(m['width'], m['height'], m['scale'], m['refreshRate']) <= 0:
            raise LayoutError('Monitor dimensions, refresh and scale must be positive.')
        if int(m['transform']) != m['transform'] or not 0 <= m['transform'] <= 7:
            raise LayoutError('Invalid monitor transform.')
        w, h = m['width'], m['height']
        if m['transform'] % 2:
            w, h = h, w
        # Hyprland rounds the transformed pixel dimensions / scale to logical px.
        m['logicalWidth'] = math.floor(w / m['scale'] + .5)
        m['logicalHeight'] = math.floor(h / m['scale'] + .5)
        m['label'] = m.get('model') or m.get('description') or name
        result.append(m)
    if not result:
        raise LayoutError('No active displays found.')
    return sorted(result, key=lambda m: m['name'])


def mirrored(m):
    return m.get('mirrorOf', 'none') not in (None, '', 'none', -1)


def validate_layout(layout, monitors):
    if not isinstance(layout, list) or len(layout) != len(monitors):
        raise LayoutError('The layout must contain every active display exactly once.')
    by_name = {m['name']: m for m in monitors}
    result = {}
    for p in layout:
        if not isinstance(p, dict) or set(p) != {'name', 'x', 'y'}:
            raise LayoutError('Each layout item must contain only name, x and y.')
        name = p['name']
        if not isinstance(name, str) or name not in by_name or name in result:
            raise LayoutError('The active displays changed; refresh the layout.')
        if mirrored(by_name[name]):
            raise LayoutError('Mirrored displays cannot be edited here.')
        for axis in ('x', 'y'):
            # JSON numbers 1.0 are integral too; booleans and infinities are not.
            v = p[axis]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or int(v) != v or abs(v) > 100000:
                raise LayoutError('Display positions must be finite integers within ±100000.')
        result[name] = {'name': name, 'x': int(p['x']), 'y': int(p['y'])}
    graph = {name: set() for name in result}
    names = sorted(result)
    for i, a in enumerate(names):
        p, m = result[a], by_name[a]
        for b in names[i + 1:]:
            q, n = result[b], by_name[b]
            ix = min(p['x'] + m['logicalWidth'], q['x'] + n['logicalWidth']) - max(p['x'], q['x'])
            iy = min(p['y'] + m['logicalHeight'], q['y'] + n['logicalHeight']) - max(p['y'], q['y'])
            if ix > 0 and iy > 0:
                raise LayoutError('Displays must not overlap.')
            if (ix == 0 and iy > 0) or (iy == 0 and ix > 0):
                graph[a].add(b)
                graph[b].add(a)
    reached, todo = set(), [names[0]]
    while todo:
        n = todo.pop()
        if n not in reached:
            reached.add(n)
            todo.extend(graph[n] - reached)
    if len(reached) != len(names):
        raise LayoutError('Displays must share traversable edges; gaps and corner-only contact are not supported.')
    return [result[n] for n in names]


def hardware_signature(monitors):
    fields = ('name', 'description', 'serial', 'width', 'height', 'refreshRate', 'scale', 'transform', 'disabled', 'mirrorOf')
    return [{f: m.get(f) for f in fields} for m in monitors]


def display_identity(monitors):
    fields = ('name', 'description', 'serial', 'disabled', 'mirrorOf')
    return [{f: m.get(f) for f in fields} for m in monitors]


def revision(monitors, files):
    data = {'hardware': hardware_signature(monitors), 'positions': positions(monitors), 'files': files}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def positions(monitors):
    return [{'name': m['name'], 'x': m['x'], 'y': m['y']} for m in monitors]


def strip_marker(data):
    if data.count(BEGIN) != data.count(END) or data.count(BEGIN) > 1:
        raise LayoutError('The managed monitors.lua marker is malformed; refusing to edit it.')
    if BEGIN not in data:
        return data
    before, rest = data.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before + after


def merge_marker(data, plugin_path):
    clean = strip_marker(data)
    return clean + BEGIN + ('dofile(' + lua_string(str(plugin_path)) + ')\n').encode() + END


class Backend:
    def __init__(self, home=None, state=None, runner=None, clock=None):
        self.home = Path(home or Path.home())
        self.config = self.home / '.config/hypr'
        self.monitors_file = self.config / 'monitors.lua'
        self.layout_file = self.config / 'omarchy-display-layout.lua'
        self.state_dir = Path(state or Path(os.environ.get('XDG_STATE_HOME', str(self.home / '.local/state'))) / 'omarchy-display-layout')
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.pending_file = self.state_dir / 'pending.json'
        self.last_file = self.state_dir / 'last-result.json'
        self.runner = runner or subprocess.run
        self.clock = clock or time.time

    @contextlib.contextmanager
    def locked(self):
        with open(self.state_dir / 'lock', 'a') as lock:
            os.chmod(self.state_dir / 'lock', 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def run(self, argv, timeout=5):
        try:
            p = self.runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LayoutError(f'{argv[0]} failed: {e}') from e
        if p.returncode:
            raise LayoutError(f'{argv[0]} failed: {(p.stderr or p.stdout).strip()[:800]}')
        return p.stdout.strip()

    def hypr(self, *args):
        return self.run(['hyprctl', *args])

    def live(self):
        return normalize(json.loads(self.hypr('monitors', 'all', '-j')))

    def load(self):
        data = file_bytes(self.pending_file)
        return json.loads(data) if data else None

    def save(self, pending):
        atomic_write(self.pending_file, json.dumps(pending, allow_nan=False).encode())

    def clear(self, result):
        atomic_write(self.last_file, json.dumps(result).encode())
        self.pending_file.unlink(missing_ok=True)

    def files(self):
        # Include user Lua modules, so changes to included overrides invalidate
        # the preview instead of being overwritten by a later Keep.
        return {str(p): digest(p.read_bytes()) for p in sorted(self.config.rglob('*.lua')) if p.is_file()}

    def selectors(self, monitors):
        source = strip_marker(self.monitors_file.read_bytes()).decode('utf-8')
        literals = literal_selectors(source)
        chosen = {}
        for m in monitors:
            desc = m.get('description', '')
            unique = bool(desc) and sum(n.get('description') == desc for n in monitors) == 1
            candidates = ['desc:' + desc, m['name']] if unique else [m['name']]
            found = [s for s in candidates if s in literals]
            if not found:
                raise LayoutError(f"{m['name']}: no literal per-display rule in monitors.lua; cannot safely preserve all monitor options.")
            # If both exist Hyprland priority can be surprising. Fail closed.
            if len(found) > 1:
                raise LayoutError(f"{m['name']}: both connector and description rules exist; resolve this ambiguity before editing.")
            chosen[m['name']] = found[0]
        return chosen

    def lua_layout(self, layout, selectors):
        return '\n'.join('hl.monitor({ output = ' + lua_string(selectors[p['name']]) + ', position = ' + lua_string(f"{p['x']}x{p['y']}") + ' })' for p in layout) + '\n'

    def apply(self, layout, selectors):
        output = self.hypr('eval', self.lua_layout(layout, selectors))
        if output and output.lower() not in ('ok', 'ok.', 'nil'):
            # eval often returns an empty string; errors can have exit status 0.
            raise LayoutError('Hyprland rejected the layout: ' + output[:800])

    def restore_live_settings(self, p):
        # Only rollback uses explicit live mode/scale/transform. The persisted
        # overlay remains position-only and never freezes a preferred-mode policy.
        lines = []
        for m in p['liveBefore']:
            mode = f"{int(m['width'])}x{int(m['height'])}@{m['refreshRate']}"
            lines.append('hl.monitor({ output = ' + lua_string(p['selectors'][m['name']]) +
                         ', mode = ' + lua_string(mode) + ', scale = ' + str(m['scale']) +
                         ', transform = ' + str(m['transform']) + ', position = ' +
                         lua_string(f"{m['x']}x{m['y']}") + ' })')
        output = self.hypr('eval', '\n'.join(lines))
        if output and output.lower() not in ('ok', 'ok.', 'nil'):
            raise LayoutError('Hyprland rejected settings restoration: ' + output[:800])

    def config_errors(self):
        errors = self.hypr('configerrors')
        if errors and errors.lower() not in ('ok', 'ok.', 'no errors'):
            raise LayoutError('Hyprland configuration validation failed: ' + errors[:800])

    def verify(self, expected, signature):
        for _ in range(12):
            actual = self.live()
            if hardware_signature(actual) != signature:
                raise LayoutError('Display mode, scale, transform or connections changed during the preview.')
            if positions(actual) == expected:
                return actual
            time.sleep(.1)
        raise LayoutError('Hyprland did not apply the requested display positions.')

    def arm(self, pending):
        unit = 'omarchy-display-layout-' + pending['token']
        argv = ['systemd-run', '--user', '--quiet', '--collect', '--unit=' + unit,
                '--on-active=' + str(TIMEOUT) + 's', '--timer-property=AccuracySec=100ms']
        for key in ('HYPRLAND_INSTANCE_SIGNATURE', 'XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY', 'XDG_STATE_HOME'):
            if os.environ.get(key):
                argv.append('--setenv=' + key + '=' + os.environ[key])
        argv.extend([sys.executable, str(Path(__file__).resolve()), '_watchdog', pending['token']])
        self.run(argv)
        # systemd-run acknowledgement means the timer was accepted; verify it
        # active before a single compositor mutation is allowed.
        self.run(['systemctl', '--user', 'is-active', '--quiet', unit + '.timer'])

    def stale_reason(self, p, live, allow_partial=False):
        if self.files() != p['files']:
            return 'Display configuration changed outside this preview.'
        if hardware_signature(live) != p['hardware']:
            return 'Display connections, mode, scale or transform changed outside this preview.'
        current = positions(live)
        if allow_partial:
            old, new = {m['name']: m for m in p['before']}, {m['name']: m for m in p['target']}
            if all(m in (old[m['name']], new[m['name']]) for m in current):
                return None
        elif current == p['target']:
            return None
        return 'Display positions changed outside this preview.'

    def restore_files(self, p):
        for entry in reversed(p.get('writes', [])):
            path = Path(entry['path'])
            current = file_bytes(path)
            if digest(current) == entry['oldHash']:
                continue
            if digest(current) != entry['newHash']:
                raise LayoutError('Configuration changed during save; refusing to overwrite newer edits: ' + str(path))
            if entry['old'] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, base64.b64decode(entry['old']), entry['mode'])

    def rollback(self, p, partial=False):
        try:
            if p.get('writes'):
                # A Keep reload may itself reset transient mode/scale/transform
                # to persisted values. Do not treat OUR reload as an unrelated
                # change and silently abandon the original live settings.
                current_files = self.files()
                expected_files = dict(p['files'])
                for entry in p['writes']:
                    actual = current_files.get(entry['path'])
                    if actual not in (entry['oldHash'], entry['newHash']):
                        raise LayoutError('Configuration changed during save; refusing to overwrite newer edits.')
                    if actual is None:
                        expected_files.pop(entry['path'], None)
                    else:
                        expected_files[entry['path']] = actual
                if current_files != expected_files:
                    raise LayoutError('Other configuration changed during save; recovery requires a fresh review.')
                identity_unchanged = display_identity(self.live()) == display_identity(p['hardware'])
                self.restore_files(p)
                if not identity_unchanged:
                    raise LayoutError('Display connections or mirroring changed during save; original files restored, automatic settings restoration refused.')
                # Interrupted Keep before reload needs no settings restoration.
                if p.get('reloadStarted'):
                    validation_error = None
                    try:
                        self.hypr('reload')
                        self.config_errors()
                    except LayoutError as e:
                        validation_error = e
                    if self.files() != p['files'] or display_identity(self.live()) != display_identity(p['hardware']):
                        raise LayoutError('Configuration or display connections changed during restoration.')
                    self.restore_live_settings(p)
                    self.verify(p['before'], p['hardware'])
                    if validation_error:
                        raise LayoutError('Live settings restored, but restored configuration validation failed: ' + str(validation_error))
                else:
                    reason = self.stale_reason(p, self.live(), allow_partial=partial)
                    if reason:
                        raise LayoutError(reason)
                    self.apply(p['before'], p['selectors'])
                    self.verify(p['before'], p['hardware'])
            else:
                reason = self.stale_reason(p, self.live(), allow_partial=partial)
                if reason:
                    self.clear({'ok': False, 'error': reason + ' Stale preview discarded without changing current displays.'})
                    raise LayoutError(reason + ' Stale preview discarded without changing current displays.')
                self.apply(p['before'], p['selectors'])
                self.verify(p['before'], p['hardware'])
        except LayoutError as e:
            if self.load():
                p['rollbackError'] = str(e)
                self.save(p)  # retain recovery data for an explicit retry
                raise LayoutError('Rollback failed; recovery state retained: ' + str(e)) from e
            raise
        self.clear({'ok': True, 'action': 'reverted'})
        return {'ok': True, 'action': 'reverted', 'pending': None}

    def pending_summary(self, p):
        if not p:
            return None
        return {key: p[key] for key in ('token', 'deadline')} | {'remainingSeconds': max(0, math.ceil(p['deadline'] - self.clock())), 'rollbackError': p.get('rollbackError')}

    def snapshot(self):
        p = self.load()
        if p and (self.clock() >= p['deadline'] or p.get('phase') == 'committing'):
            self.rollback(p, partial=p.get('phase') == 'applying')
            p = None
        live = self.live()
        if p:
            reason = self.stale_reason(p, live, allow_partial=p.get('phase') == 'applying')
            if reason:
                self.clear({'ok': False, 'error': reason})
                raise LayoutError(reason + ' Stale preview discarded without changing current displays.')
        last = file_bytes(self.last_file)
        return {'ok': True, 'monitors': live, 'revision': revision(live, self.files()), 'pending': self.pending_summary(p), 'lastResult': json.loads(last) if last else None}

    def prepare(self, layout, expected_revision=None):
        if self.load():
            raise LayoutError('A display preview is already pending. Keep or revert it first.')
        if self.monitors_file.is_symlink() or self.layout_file.is_symlink():
            raise LayoutError('Managed monitor files are symlinks; refusing to replace their links.')
        before, files = self.live(), self.files()
        baseline = revision(before, files)
        if expected_revision is not None and expected_revision != baseline:
            raise LayoutError('The display layout or configuration changed since it was loaded. Refresh before applying.')
        target = validate_layout(layout, before)
        if target == positions(before):
            raise LayoutError('The display positions have not changed.')
        selectors = self.selectors(before)
        self.config_errors()
        if self.files() != files:
            raise LayoutError('Configuration changed while preparing the layout. Refresh before applying.')
        return before, files, baseline, target, selectors

    def plan(self, layout, expected_revision=None):
        before, files, baseline, target, selectors = self.prepare(layout, expected_revision)
        overlay = self.overlay(target, selectors)
        old = self.monitors_file.read_bytes()
        source = merge_marker(old, self.layout_file)
        diffs = []
        for path, data in ((self.layout_file, overlay), (self.monitors_file, source)):
            diffs.extend(difflib.unified_diff((file_bytes(path) or b'').decode().splitlines(True),
                                            data.decode().splitlines(True), fromfile=str(path), tofile=str(path)))
        return {'ok': True, 'revision': baseline, 'target': target, 'selectors': selectors,
                'overlayLua': overlay.decode(), 'diff': ''.join(diffs), 'pending': None}

    def overlay(self, target, selectors):
        return ('-- Position-only overrides; other options remain in monitors.lua.\n' + self.lua_layout(target, selectors)).encode()

    def preview(self, layout, expected_revision=None):
        before, files, baseline, target, selectors = self.prepare(layout, expected_revision)
        p = {'token': secrets.token_hex(16), 'deadline': self.clock() + TIMEOUT,
             'before': positions(before), 'liveBefore': before, 'target': target, 'hardware': hardware_signature(before),
             'selectors': selectors, 'files': files, 'phase': 'applying'}
        self.save(p)
        try:
            self.arm(p)
        except LayoutError:
            self.pending_file.unlink(missing_ok=True)
            raise
        try:
            # Recheck immediately after watchdog setup, before touching displays.
            current = self.live()
            if self.files() != p['files'] or hardware_signature(current) != p['hardware'] or positions(current) != p['before']:
                raise LayoutError('Display state changed before preview; refresh and try again.')
            self.apply(target, selectors)
            actual = self.verify(target, p['hardware'])
            p['phase'] = 'preview'
            self.save(p)
            return {'ok': True, 'monitors': actual, 'revision': revision(actual, self.files()), 'pending': self.pending_summary(p)}
        except LayoutError as e:
            try:
                self.rollback(p, partial=True)
            except LayoutError as rollback_error:
                raise LayoutError(str(e) + ' ' + str(rollback_error)) from e
            raise

    def token_pending(self, token):
        if not TOKEN.fullmatch(token):
            raise LayoutError('Invalid preview token.')
        p = self.load()
        if not p or p['token'] != token:
            raise LayoutError('This preview has already ended or belongs to another request.')
        return p

    def keep(self, token):
        p = self.token_pending(token)
        if self.clock() >= p['deadline']:
            self.rollback(p)
            raise LayoutError('The preview expired and was reverted.')
        reason = self.stale_reason(p, self.live())
        if reason:
            self.clear({'ok': False, 'error': reason})
            raise LayoutError(reason + ' Nothing was saved.')
        old = self.monitors_file.read_bytes()
        content = self.overlay(p['target'], p['selectors'])
        updates = [(self.layout_file, content), (self.monitors_file, merge_marker(old, self.layout_file))]
        p['writes'] = []
        for path, new in updates:
            data = file_bytes(path)
            p['writes'].append({'path': str(path), 'old': None if data is None else base64.b64encode(data).decode(),
                                'oldHash': digest(data), 'newHash': digest(new), 'mode': path.stat().st_mode & 0o777 if path.exists() else 0o600})
        p['phase'] = 'committing'
        self.save(p)  # watchdog can undo an interrupted two-file commit
        try:
            for (path, data), entry in zip(updates, p['writes']):
                if digest(file_bytes(path)) != entry['oldHash']:
                    raise LayoutError('Configuration changed while saving; save cancelled.')
                atomic_write(path, data, entry['mode'])
            p['reloadStarted'] = True
            self.save(p)
            self.hypr('reload')
            self.config_errors()
            actual = self.verify(p['target'], p['hardware'])
            # A concurrent writer must not be silently called a successful Keep.
            for entry in p['writes']:
                if digest(file_bytes(entry['path'])) != entry['newHash']:
                    raise LayoutError('Configuration changed while validating the save.')
        except LayoutError as e:
            try:
                self.rollback(p, partial=True)
            except LayoutError as undo:
                raise LayoutError(str(e) + ' ' + str(undo)) from e
            raise
        self.clear({'ok': True, 'action': 'kept'})
        return {'ok': True, 'action': 'kept', 'monitors': actual, 'revision': revision(actual, self.files()), 'pending': None}

    def dispatch(self, command, arg=None, expected_revision=None):
        with self.locked():
            if command in ('snapshot', 'status'):
                return self.snapshot()
            if command == 'preview':
                return self.preview(json.loads(arg), expected_revision)
            if command == 'plan':
                return self.plan(json.loads(arg), expected_revision)
            if command == 'keep':
                return self.keep(arg)
            if command == 'revert':
                return self.rollback(self.token_pending(arg), partial=True)
            if command == '_watchdog':
                p = self.load()
                if not p or p['token'] != arg:
                    return {'ok': True, 'action': 'already-ended'}
                return self.rollback(p, partial=p.get('phase') in ('applying', 'committing'))
            raise LayoutError('Usage: display_layout.py snapshot|status|plan JSON [REVISION]|preview JSON [REVISION]|keep TOKEN|revert TOKEN')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        allowed = (1,) if args and args[0] in ('snapshot', 'status') else ((2, 3) if args and args[0] in ('preview', 'plan') else (2,))
        if not args or len(args) not in allowed:
            raise LayoutError('Usage: display_layout.py snapshot|status|plan JSON [REVISION]|preview JSON [REVISION]|keep TOKEN|revert TOKEN')
        result = Backend().dispatch(args[0], args[1] if len(args) >= 2 else None, args[2] if len(args) == 3 else None)
    except (LayoutError, OSError, ValueError, TypeError, KeyError) as e:
        result = {'ok': False, 'error': str(e)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
