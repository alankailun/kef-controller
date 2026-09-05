from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for the isolated JavaScript behavior check")
class WebScanUITests(unittest.TestCase):
    def test_scan_events_are_scoped_and_close_requests_cancellation(self):
        html = (Path(__file__).parents[1] / "kef_app/ui/web/index.html").read_text(encoding="utf-8")
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

    def test_inline_application_script_parses(self):
        html = (Path(__file__).parents[1] / "kef_app/ui/web/index.html").read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(
            [shutil.which("node"), "--check"], input=script,
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
