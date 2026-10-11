import assert from 'node:assert/strict';
import test from 'node:test';
import {
  backendApi,
  getWsUrl,
  mapBackendStatus,
  mapCommandResult,
  parseTelemetryPacket,
} from './backendApi.ts';

const statusFixture = (overrides = {}) => ({
  paused: false,
  emergency_stop: false,
  cursor_control_enabled: false,
  camera: { status: 'stopped', available: false },
  tracking: { active: false, reason: 'stopped', quality: 0, faces: 0 },
  calibration: { calibrated: false, state: 'idle' },
  cursor: { nx: 0.5, ny: 0.5, valid: false },
  voice: { mode: 'idle', last_command: null },
  browser: { status: 'not_started' },
  browser_status: { available: true, started: false },
  vision: { available: false, running: false },
  server_time: '2026-10-11T00:00:00Z',
  last_updated: '2026-10-11T00:00:00Z',
  ...overrides,
});

test('maps the real status snapshot and preserves unavailable/stopped states', () => {
  const paused = mapBackendStatus(statusFixture({ paused: true, emergency_stop: true }));
  assert.equal(paused.paused, true);
  assert.equal(paused.emergency_stopped, true);
  assert.equal(paused.cursor_control_enabled, false);
  assert.equal(paused.voice_status, 'unavailable');
  assert.equal(paused.vision_status, 'unavailable');
  assert.equal(paused.browser_status, 'stopped');
  assert.equal(paused.tracking_status, 'unavailable');

  const active = mapBackendStatus(statusFixture({
    camera: { status: 'running', available: true },
    vision: { available: true, running: true },
    tracking: { active: true, reason: 'ok', quality: 0.87, faces: 1 },
    browser: { status: 'running' },
    browser_status: { available: true, started: true },
  }));
  assert.equal(active.vision_status, 'available');
  assert.equal(active.browser_status, 'available');
  assert.equal(active.tracking_status, 'tracking');
  assert.equal(active.tracking_confidence, 0.87);
  assert.equal(active.cursor_control_enabled, false);
});

test('maps completed, safety-blocked, and failed Runtime.handle_voice results', () => {
  assert.equal(mapCommandResult({
    transcript: 'open example.com', kind: 'navigate', executed: true,
    action_result: { ok: true, action: 'goto', url: 'https://example.com' },
  }).status, 'completed');
  const blocked = mapCommandResult({
    transcript: 'open example.com', kind: 'navigate', executed: false,
    action_result: { ok: false, reason: 'emergency_stop', detail: 'Emergency stop active' },
  });
  assert.equal(blocked.status, 'blocked');
  assert.equal(blocked.reason_code, 'emergency_stop');
  assert.match(blocked.message, /Emergency stop active/);
  assert.equal(mapCommandResult({
    transcript: 'open example.com', kind: 'navigate', executed: false,
    action_result: { ok: false, reason: 'browser_unavailable', detail: 'Browser unavailable' },
  }).status, 'failed');
});

test('uses backend pause/resume and emergency-stop latch endpoints without auto-resuming', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init) => {
    const body = JSON.parse(String(init?.body || '{}'));
    calls.push({ url: String(input), body });
    const raw = statusFixture({
      paused: body.paused === true || body.active === true || body.active === false,
      emergency_stop: body.active === true,
      cursor_control_enabled: false,
    });
    return new Response(JSON.stringify(raw), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  await backendApi.pauseSession(true);
  await backendApi.resumeSession();
  await backendApi.emergencyStop(true);
  const recovered = await backendApi.recoverSession();

  assert.deepEqual(calls.map(({ url }) => new URL(url).pathname), [
    '/api/control/pause', '/api/control/pause',
    '/api/control/emergency-stop', '/api/control/emergency-stop',
  ]);
  assert.deepEqual(calls.map(({ body }) => body), [
    { paused: true }, { paused: false }, { active: true }, { active: false },
  ]);
  assert.equal(recovered.state.emergency_stopped, false);
  assert.equal(recovered.state.paused, true);
  assert.equal(recovered.state.cursor_control_enabled, false);
});

test('uses the actual health, status, and voice command routes and request body', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ url: String(input), method: init?.method || 'GET', body: init?.body ? JSON.parse(String(init.body)) : null });
    if (String(input).endsWith('/health')) {
      return new Response(JSON.stringify({ status: 'ok', timestamp: '2026-10-11T00:00:00Z' }), { status: 200 });
    }
    if (String(input).endsWith('/api/status')) {
      return new Response(JSON.stringify(statusFixture()), { status: 200 });
    }
    if (String(input).endsWith('/api/vision/start')) {
      return new Response(JSON.stringify({
        available: true, running: true, fps: 0, inference_ms: 0, model: 'face_landmarker.task',
        synthetic_source: false, camera: { status: 'starting' }, tracking: { active: false, reason: 'not_started' },
      }), { status: 200 });
    }
    return new Response(JSON.stringify({
      transcript: 'open example.com', kind: 'navigate', executed: false,
      action_result: { ok: false, reason: 'paused', detail: 'System paused' },
    }), { status: 200 });
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  assert.equal((await backendApi.getHealth()).status, 'ok');
  assert.equal((await backendApi.getSession()).lifecycle, 'stopped');
  assert.equal((await backendApi.sendVoiceCommand('open example.com')).status, 'blocked');
  assert.equal((await backendApi.startVision()).running, true);
  assert.deepEqual(calls.map(({ url }) => new URL(url).pathname), [
    '/health', '/api/status', '/api/voice/command', '/api/vision/start',
  ]);
  assert.deepEqual(calls[2].body, { text: 'open example.com' });
  assert.equal(calls[3].method, 'POST');
});

test('accepts actual initial history and live telemetry websocket packets', () => {
  assert.equal(getWsUrl(), 'ws://127.0.0.1:8000/api/ws/telemetry');
  assert.deepEqual(parseTelemetryPacket({ type: 'history', events: [{ id: 1, ts: 'now', kind: 'safety', message: 'paused', data: {} }] }), {
    type: 'history', events: [{ id: 1, ts: 'now', kind: 'safety', message: 'paused', data: {} }],
  });
  assert.deepEqual(parseTelemetryPacket({
    type: 'event', event: { id: 2, ts: 'now', kind: 'browser', message: 'browser_action_rejected', data: { reason: 'paused' } },
  }), { type: 'event', event: { id: 2, ts: 'now', kind: 'browser', message: 'browser_action_rejected', data: { reason: 'paused' } } });
  assert.equal(parseTelemetryPacket({ type: 'event', event: null }), null);
  assert.equal(parseTelemetryPacket({ type: 'history', events: null })?.type, 'history');
});
