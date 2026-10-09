import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('backend', Path(__file__).parents[1] / 'display_layout.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def monitors():
    return b.normalize([
        dict(name='DP-8', description='Panel CR245D serial001', width=1920, height=1080, refreshRate=179.998, x=1920, y=0, scale=1, transform=0, disabled=False, mirrorOf='none'),
        dict(name='eDP-1', description='Internal', width=1920, height=1080, refreshRate=60.001, x=0, y=0, scale=1, transform=0, disabled=False, mirrorOf='none'),
    ])


class FakeBackend(b.Backend):
    def __init__(self, root):
        super().__init__(home=root, state=Path(root) / 'state')
        self.config.mkdir(parents=True)
        self.original = b'-- user Lua; preserve byte-for-byte\r\nhl.monitor({ output = "eDP-1", mode = "preferred", scale = 1, transform = 0, vrr = 2 })\r\nhl.monitor({ output = "desc:Panel CR245D serial001", mode = "1920x1080@180", scale = 1, bitdepth = 10 })'
        self.monitors_file.write_bytes(self.original)
        self.m = monitors()
        self.events = []
        self.now = 1000
        self.clock = lambda: self.now
        self.fail_arm = False
        self.fail_apply = False
        self.errors = ''
        self.reload_hook = None

    def live(self):
        return copy.deepcopy(self.m)

    def arm(self, pending):
        self.events.append('armed')
        self.assert_pending = self.pending_file.is_file()
        if self.fail_arm:
            raise b.LayoutError('systemd unavailable')

    def apply(self, layout, selectors):
        self.events.append(('apply', copy.deepcopy(layout)))
        if self.fail_apply:
            raise b.LayoutError('compositor failure')
        for pos in layout:
            next(m for m in self.m if m['name'] == pos['name']).update(pos)

    def restore_live_settings(self, pending):
        self.events.append('restore-settings')
        self.apply(pending['before'], pending['selectors'])
        self.m = copy.deepcopy(pending['liveBefore'])

    def hypr(self, *args):
        self.events.append(args)
        if args[0] == 'reload' and self.reload_hook:
            self.reload_hook()
        if args[0] == 'configerrors':
            errors, self.errors = self.errors, ''
            return errors
        return ''


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.be = FakeBackend(self.tmp.name)
        self.target = [{'name': 'DP-8', 'x': -1920, 'y': 0}, {'name': 'eDP-1', 'x': 0, 'y': 0}]

    def preview(self):
        return self.be.dispatch('preview', json.dumps(self.target))['pending']['token']

    def test_preview_watchdog_before_mutation_no_config_edits(self):
        token = self.preview()
        self.assertTrue(self.be.assert_pending)
        self.assertLess(self.be.events.index('armed'), next(i for i, e in enumerate(self.be.events) if isinstance(e, tuple) and e[0] == 'apply'))
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)
        self.assertFalse(self.be.layout_file.exists())
        status = self.be.dispatch('status')
        self.assertEqual(status['pending']['token'], token)
        self.assertEqual(status['pending']['remainingSeconds'], 20)

    def test_failed_watchdog_does_not_mutate(self):
        self.be.fail_arm = True
        with self.assertRaisesRegex(b.LayoutError, 'systemd'):
            self.preview()
        self.assertEqual(self.be.events, [('configerrors',), 'armed'])
        self.assertIsNone(self.be.load())

    def test_timeout_reverts_without_panel(self):
        token = self.preview()
        # A different helper instance represents the independent systemd service.
        watchdog = b.Backend(home=self.tmp.name, state=self.be.state_dir)
        watchdog.live, watchdog.apply = self.be.live, self.be.apply
        result = watchdog.dispatch('_watchdog', token)
        self.assertTrue(result['ok'])
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))
        self.assertIsNone(self.be.load())
        self.assertEqual(watchdog.dispatch('_watchdog', token)['action'], 'already-ended')

    def test_status_expiry_recovery(self):
        self.preview()
        self.be.now += 21
        self.assertIsNone(self.be.dispatch('status')['pending'])
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))

    def test_keep_scoped_and_position_only(self):
        token = self.preview()
        self.assertTrue(self.be.dispatch('keep', token)['ok'])
        generated = self.be.layout_file.read_text()
        self.assertIn('desc:Panel CR245D serial001', generated)
        self.assertNotIn('mode =', generated)
        self.assertNotIn('scale =', generated)
        self.assertNotIn('transform =', generated)
        current = self.be.monitors_file.read_bytes()
        self.assertTrue(current.startswith(self.be.original))
        self.assertEqual(b.strip_marker(current), self.be.original)
        self.assertEqual(current.count(b.BEGIN), 1)
        self.assertIn(('reload',), self.be.events)
        self.assertIn(('configerrors',), self.be.events)
        self.assertIsNone(self.be.load())
        # A second keep replaces its own file and keeps one scoped marker.
        self.target = b.positions(monitors())
        self.be.dispatch('keep', self.preview())
        self.assertEqual(self.be.monitors_file.read_bytes().count(b.BEGIN), 1)
        self.assertEqual(b.strip_marker(self.be.monitors_file.read_bytes()), self.be.original)

    def test_configuration_error_restores_files_and_positions(self):
        token = self.preview()
        self.be.errors = 'bad user Lua'
        with self.assertRaisesRegex(b.LayoutError, 'validation failed'):
            self.be.dispatch('keep', token)
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)
        self.assertFalse(self.be.layout_file.exists())
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))
        self.assertIsNone(self.be.load())

    def test_concurrent_file_change_never_overwritten(self):
        token = self.preview()
        self.be.monitors_file.write_bytes(self.be.original + b'\n-- concurrent edit')
        with self.assertRaisesRegex(b.LayoutError, 'changed outside'):
            self.be.dispatch('keep', token)
        self.assertTrue(self.be.monitors_file.read_bytes().endswith(b'-- concurrent edit'))
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))
        self.assertIsNone(self.be.load())

    def test_hotplug_or_scale_change_not_overridden(self):
        token = self.preview()
        self.be.m[0]['scale'] = 1.25
        with self.assertRaisesRegex(b.LayoutError, 'changed outside'):
            self.be.dispatch('revert', token)
        self.assertEqual(self.be.m[0]['scale'], 1.25)
        self.assertEqual(len([e for e in self.be.events if isinstance(e, tuple) and e[0] == 'apply']), 1)

    def test_other_position_change_not_overridden(self):
        token = self.preview()
        self.be.m[0]['x'] = -3000
        with self.assertRaisesRegex(b.LayoutError, 'positions changed'):
            self.be.dispatch('revert', token)
        self.assertEqual(self.be.m[0]['x'], -3000)

    def test_rollback_failure_truthful_and_recoverable(self):
        token = self.preview()
        self.be.fail_apply = True
        with self.assertRaisesRegex(b.LayoutError, 'Rollback failed'):
            self.be.dispatch('revert', token)
        self.assertIn('compositor failure', self.be.load()['rollbackError'])
        self.be.fail_apply = False
        self.assertTrue(self.be.dispatch('revert', token)['ok'])

    def test_parallel_preview_and_wrong_token_rejected(self):
        self.preview()
        with self.assertRaisesRegex(b.LayoutError, 'already pending'):
            self.preview()
        with self.assertRaisesRegex(b.LayoutError, 'another request'):
            self.be.dispatch('revert', 'a' * 32)
        with self.assertRaisesRegex(b.LayoutError, 'Invalid preview token'):
            self.be.dispatch('keep', '; touch /tmp/pwn')

    def test_mocked_systemd_launch_is_independent_and_absolute(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')
        self.be.runner = run
        p = {'token': 'a' * 32}
        with patch.dict(os.environ, {'HYPRLAND_INSTANCE_SIGNATURE': 'test_instance'}):
            b.Backend.arm(self.be, p)
        command = calls[0]
        self.assertEqual(command[0], 'systemd-run')
        self.assertIn('--user', command)
        self.assertIn('--on-active=20s', command)
        self.assertIn('--setenv=HYPRLAND_INSTANCE_SIGNATURE=test_instance', command)
        self.assertTrue(Path(command[-3]).is_absolute())
        self.assertEqual(command[-2:], ['_watchdog', 'a' * 32])
        self.assertIn('is-active', calls[1])

    def test_interrupted_keep_journal_restores_only_owned_writes(self):
        token = self.preview()
        p = self.be.load()
        old = self.be.monitors_file.read_bytes()
        new = b.merge_marker(old, self.be.layout_file)
        p['writes'] = [{'path': str(self.be.monitors_file), 'old': b.base64.b64encode(old).decode(), 'oldHash': b.digest(old), 'newHash': b.digest(new), 'mode': 0o600}]
        p['phase'] = 'committing'
        self.be.save(p)
        self.be.monitors_file.write_bytes(new)
        self.be.dispatch('_watchdog', token)
        self.assertEqual(self.be.monitors_file.read_bytes(), old)

    def test_revision_rejects_stale_pre_apply_before_watchdog(self):
        for change in ('position', 'scale', 'source', 'overlay'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                be = FakeBackend(root)
                baseline = be.dispatch('snapshot')['revision']
                self.assertEqual(len(baseline), 64)
                self.assertEqual(be.dispatch('status')['revision'], baseline)
                if change == 'position':
                    be.m[0]['y'] += 20
                elif change == 'scale':
                    be.m[0]['scale'] = 1.25
                elif change == 'source':
                    be.monitors_file.write_bytes(be.original + b'\n-- changed')
                else:
                    be.layout_file.write_text('-- changed')
                with self.assertRaisesRegex(b.LayoutError, 'changed since it was loaded'):
                    be.dispatch('preview', json.dumps(self.target), baseline)
                self.assertNotIn('armed', be.events)
                self.assertIsNone(be.load())

    def test_read_only_plan_and_matching_revision_preview(self):
        baseline = self.be.dispatch('snapshot')['revision']
        result = self.be.dispatch('plan', json.dumps(self.target), baseline)
        self.assertEqual(result['revision'], baseline)
        self.assertIn('desc:Panel CR245D serial001', result['overlayLua'])
        self.assertIn('dofile(', result['diff'])
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)
        self.assertFalse(self.be.layout_file.exists())
        self.assertIsNone(self.be.load())
        self.assertNotIn('armed', self.be.events)
        preview = self.be.dispatch('preview', json.dumps(self.target), baseline)
        self.assertNotEqual(preview['revision'], baseline)

    def test_keep_reload_divergence_restores_original_live_settings(self):
        self.be.m[0].update(width=2560, height=1440, refreshRate=144.005, scale=1.25, transform=2)
        # Use current normalized dimensions to build a traversable target.
        self.be.m = b.normalize(self.be.m)
        original_live = copy.deepcopy(self.be.m)
        self.target[0]['x'] = -self.be.m[0]['logicalWidth']
        token = self.preview()
        def reload_persisted():
            self.be.m[0].update(width=1920, height=1080, refreshRate=179.998, scale=1, transform=0)
        self.be.reload_hook = reload_persisted
        with self.assertRaisesRegex(b.LayoutError, 'mode, scale, transform'):
            self.be.dispatch('keep', token)
        self.assertEqual(b.hardware_signature(self.be.m), b.hardware_signature(original_live))
        self.assertEqual(b.positions(self.be.m), b.positions(original_live))
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)
        self.assertFalse(self.be.layout_file.exists())
        self.assertIn('restore-settings', self.be.events)
        self.assertIsNone(self.be.load())

    def test_keep_reload_divergence_restoration_failure_is_retained(self):
        token = self.preview()
        def reload_persisted():
            self.be.m[0]['scale'] = 1.25
            self.be.fail_apply = True
        self.be.reload_hook = reload_persisted
        with self.assertRaisesRegex(b.LayoutError, 'Rollback failed'):
            self.be.dispatch('keep', token)
        self.assertIn('compositor failure', self.be.load()['rollbackError'])
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)

    def test_hotplug_during_keep_restores_files_but_not_stale_hardware(self):
        token = self.preview()
        def hotplug():
            self.be.m.pop()
        self.be.reload_hook = hotplug
        with self.assertRaisesRegex(b.LayoutError, 'automatic settings restoration refused'):
            self.be.dispatch('keep', token)
        self.assertNotIn('restore-settings', self.be.events)
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)
        self.assertFalse(self.be.layout_file.exists())
        self.assertIsNotNone(self.be.load())

    def test_interrupted_keep_after_reload_restores_modes_from_journal(self):
        token = self.preview()
        p = self.be.load()
        data = b.merge_marker(self.be.original, self.be.layout_file)
        p['writes'] = [{'path': str(self.be.monitors_file), 'old': b.base64.b64encode(self.be.original).decode(), 'oldHash': b.digest(self.be.original), 'newHash': b.digest(data), 'mode': 0o600}]
        p['phase'], p['reloadStarted'] = 'committing', True
        self.be.save(p)
        self.be.monitors_file.write_bytes(data)
        self.be.m[0]['scale'] = 1.25
        self.be.dispatch('_watchdog', token)
        self.assertEqual(b.hardware_signature(self.be.m), b.hardware_signature(monitors()))
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))

    def test_restoration_lua_carries_exact_prior_settings_only_on_rollback(self):
        token = self.preview()
        calls = []
        self.be.hypr = lambda *args: calls.append(args) or 'ok'
        b.Backend.restore_live_settings(self.be, self.be.load())
        self.assertEqual(calls[0][0], 'eval')
        self.assertIn('1920x1080@179.998', calls[0][1])
        self.assertIn('scale = 1', calls[0][1])
        self.assertIn('transform = 0', calls[0][1])
        self.assertNotIn('mode =', self.be.overlay(self.target, self.be.load()['selectors']).decode())

    def test_flock_serializes_separate_helper_processes(self):
        ready, entered = Path(self.tmp.name) / 'ready', Path(self.tmp.name) / 'entered'
        worker = "import fcntl, pathlib, sys; pathlib.Path(sys.argv[2]).touch(); f=open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); pathlib.Path(sys.argv[3]).touch()"
        with self.be.locked():
            child = subprocess.Popen([b.sys.executable, '-c', worker, str(self.be.state_dir / 'lock'), str(ready), str(entered)])
            try:
                limit = b.time.monotonic() + 3
                while not ready.exists() and b.time.monotonic() < limit:
                    b.time.sleep(.01)
                self.assertTrue(ready.exists())
                b.time.sleep(.03)
                self.assertFalse(entered.exists())
            except Exception:
                child.kill()
                child.wait()
                raise
        self.assertEqual(child.wait(timeout=3), 0)
        self.assertTrue(entered.exists())

    def test_status_detects_stale_preview_before_expiry(self):
        self.preview()
        (self.be.config / 'input.lua').write_text('-- a concurrent edit')
        with self.assertRaisesRegex(b.LayoutError, 'Preview reverted'):
            self.be.dispatch('status')
        self.assertIsNone(self.be.load())
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))
        self.assertEqual((self.be.config / 'input.lua').read_text(), '-- a concurrent edit')

    def test_helper_lost_after_apply_before_phase_save_can_be_kept(self):
        token = self.preview()
        pending = self.be.load()
        pending['phase'] = 'applying'
        self.be.save(pending)
        self.assertIsNotNone(self.be.dispatch('status')['pending'])
        self.assertTrue(self.be.dispatch('keep', token)['ok'])
        self.assertEqual(b.positions(self.be.m), self.target)

    def test_watchdog_after_unrelated_lua_edit_restores_runtime_only(self):
        token = self.preview()
        path = self.be.config / 'input.lua'
        path.write_text('-- a newer unrelated setting')
        self.be.dispatch('_watchdog', token)
        self.assertEqual(b.positions(self.be.m), b.positions(monitors()))
        self.assertEqual(path.read_text(), '-- a newer unrelated setting')
        self.assertEqual(self.be.monitors_file.read_bytes(), self.be.original)
        self.assertIsNone(self.be.load())


    def test_ambiguous_selector_rejected(self):
        self.be.monitors_file.write_bytes(self.be.original + b'\nhl.monitor({output="DP-8"})')
        with self.assertRaisesRegex(b.LayoutError, 'ambiguity'):
            self.preview()

    def test_dynamic_selector_rejected(self):
        self.be.monitors_file.write_text('hl.monitor({output=external_name})')
        with self.assertRaisesRegex(b.LayoutError, 'literal per-display'):
            self.preview()


class GeometryTests(unittest.TestCase):
    def test_negative_coordinates_and_topology(self):
        m = monitors()
        valid = [{'name': 'DP-8', 'x': 0, 'y': -1080}, {'name': 'eDP-1', 'x': 0, 'y': 0}]
        self.assertEqual(b.validate_layout(valid, m), valid)
        for x, y, error in [(1919, 0, 'overlap'), (1921, 0, 'edges'), (1920, 1080, 'edges')]:
            valid[0].update(x=x, y=y)
            with self.assertRaisesRegex(b.LayoutError, error):
                b.validate_layout(valid, m)

    def test_fractional_scaling_odd_transform(self):
        m = monitors()
        m[0].update(scale=1.25, transform=1)
        m = b.normalize(m)
        self.assertEqual((m[0]['logicalWidth'], m[0]['logicalHeight']), (864, 1536))
        b.validate_layout([{'name': 'DP-8', 'x': -864, 'y': -100}, {'name': 'eDP-1', 'x': 0, 'y': 0}], m)

    def test_nonfinite_bool_fraction_and_unexpected_field(self):
        for value in (True, float('nan'), float('inf'), 1.5, 100001):
            with self.assertRaises(b.LayoutError):
                b.validate_layout([{'name': 'DP-8', 'x': value, 'y': 0}, {'name': 'eDP-1', 'x': 0, 'y': 0}], monitors())
        with self.assertRaisesRegex(b.LayoutError, 'only name'):
            b.validate_layout([{'name': 'DP-8', 'x': 1920, 'y': 0, 'mode': 'x'}, {'name': 'eDP-1', 'x': 0, 'y': 0}], monitors())

    def test_missing_duplicate_mirrored_and_injection(self):
        m = monitors()
        m[0]['mirrorOf'] = 'eDP-1'
        with self.assertRaisesRegex(b.LayoutError, 'Mirrored'):
            b.validate_layout(b.positions(m), m)
        m[0]['name'] = 'DP-1"; os.execute("oops")'
        with self.assertRaisesRegex(b.LayoutError, 'unsafe'):
            b.normalize(m)
        escaped = b.lua_string('desc:hello"\n\\日本')
        self.assertNotIn('\n', escaped)
        self.assertIn('\\034', escaped)
        self.assertIn('\\010', escaped)
        self.assertNotIn('日本', escaped)

    def test_selector_lexer_ignores_examples_strings_and_computed_values(self):
        source = '''
-- hl.monitor({output="comment"})
--[=[ hl.monitor({output="long-comment"}) ]=]
local example = 'hl.monitor({output="string-example"})'
local example2 = [==[hl.monitor({output="long-string"})]==]
hl.monitor({ output = "DP-8", mode = "preferred" })
hl.monitor({ output = 'eDP-1', scale = 1 })
hl.monitor({ output = "desc:" .. description })
        '''
        self.assertEqual(b.literal_selectors(source), {'DP-8', 'eDP-1'})


    def test_malformed_managed_marker_refused(self):
        with self.assertRaisesRegex(b.LayoutError, 'malformed'):
            b.merge_marker(b'original' + b.BEGIN, Path('/tmp/owned.lua'))


if __name__ == '__main__':
    unittest.main()
