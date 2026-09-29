from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for the isolated JavaScript behavior check")
class WebScanUITests(unittest.TestCase):
    def test_split_web_scripts_load_in_browser_order(self):
        root = Path(__file__).parents[1] / "kef_app/ui/web"
        html = (root / "index.html").read_text(encoding="utf-8")
        scripts = ("texts.js", "controller_core.js", "controller_logs.js", "controller_settings.js", "controller.js")
        self.assertEqual(
            [name for name in scripts if f'<script src="{name}"></script>' in html], list(scripts),
        )
        harness = r'''
const vm = require('node:vm');
const fs = require('node:fs');
const context = vm.createContext({
  window: {location: {search: ''}},
  document: {querySelector: () => ({addEventListener() {}})},
  URLSearchParams, fetch: () => new Promise(() => {}), setTimeout, clearTimeout,
});
for (const name of process.argv.slice(2)) {
  vm.runInContext(fs.readFileSync(name, 'utf8'), context, {filename: name});
}
'''
        result = subprocess.run(
            [shutil.which("node"), "-", *(str(root / name) for name in scripts)], input=harness,
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scan_events_are_scoped_and_close_requests_cancellation(self):
        html = "".join((Path(__file__).parents[1] / "kef_app/ui/web" / name).read_text(encoding="utf-8")
                       for name in ("controller_core.js", "controller_logs.js", "controller_settings.js", "controller.js"))
        script = html.split("  function closeSpeakerDialog()", 1)[1].split("  function syncDialog()", 1)[0]
        script = "function closeSpeakerDialog()" + script
        handler = "function handleToast(msg)" + html.split("  function handleToast(msg)", 1)[1].split(
            "  // ═", 1,
        )[0]
        harness = r'''
const assert = require('node:assert/strict');
let dlg = {scanId: 'new', phase: 'scanning', found: [], checked: 0}, syncs = 0;
let dialogReturnFocus = null;
const $ = () => ({inert: true, remove() {}});
const bridge = { cancelScan(id) { cancelled.push(id); return Promise.resolve(); } };
const cancelled = [];
function syncDialog() { syncs++; }
function t(value) { return value; }
'''
        checks = r'''
handleToast({kind: 'scan', scan_id: 'old', state: 'complete', devices: [{ip: 'old'}]});
assert.equal(dlg.phase, 'scanning');
assert.equal(syncs, 0);
handleToast({kind: 'scan', scan_id: 'new', state: 'candidate', devices: [{ip: 'new'}]});
assert.deepEqual(dlg.found, [{ip: 'new'}]);
handleToast({kind: 'scan', scan_id: 'old', state: 'failed', detail: 'stale'});
assert.equal(dlg.failed, undefined);
handleToast({kind: 'scan', scan_id: 'new', state: 'failed', code: 'scan_busy', detail: 'raw'});
assert.equal(dlg.failed, 'scan_busy');
assert.equal(dlg.phase, 'done');
closeSpeakerDialog();
assert.deepEqual(cancelled, ['new']);
assert.equal(dlg, null);
handleToast({kind: 'scan', scan_id: 'new', state: 'complete', devices: []});
assert.equal(dlg, null);
'''
        result = subprocess.run(
            [shutil.which("node"), "-"], input=harness + script + handler + checks,
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_application_script_parses(self):
        html = "".join((Path(__file__).parents[1] / "kef_app/ui/web" / name).read_text(encoding="utf-8")
                       for name in ("controller_core.js", "controller_logs.js", "controller_settings.js", "controller.js"))
        script = html
        result = subprocess.run(
            [shutil.which("node"), "--check"], input=script,
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
