/* Durable queue semantics, including real disconnect/reconnect and disk failure. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '..');
const serviceRoot = process.env.ONEBOT_TEST_SOURCE || root;

async function port() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}
async function until(check) {
  for (let i = 0; i < 100; i++) {
    try { const value = await check(); if (value) return value; } catch {}
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error('condition timed out');
}
(async () => {
  const {writeFrame, dispatchQueued} = await import(pathToFileURL(path.join(root, 'server/onebot-delivery.mjs')));
  let writes = 0;
  const socket = {writable: true, write() { writes++; return false; }};
  const entry = {status: 'queued', event: {message_id: 1}};
  let persisted;
  assert.equal(dispatchQueued(entry, {socket, 活的: true}, () => { persisted = entry.status; }), true);
  assert.equal(persisted, 'framework_unknown');
  assert.equal(writes, 1);
  assert.equal(dispatchQueued(entry, {socket, 活的: true}, () => {}), false);
  assert.equal(writes, 1, 'buffered writes must never be replayed automatically');
  assert.equal(writeFrame({writable: false}, 1, Buffer.from('test')), 'not_written');
  const failed = {status: 'queued', event: {}};
  assert.throws(() => dispatchQueued(failed, {socket, 活的: true}, () => { throw Error('disk'); }));
  assert.equal(failed.status, 'queued');
  assert.equal(writes, 1, 'disk failure must precede any socket write');

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'onebot-delivery-'));
  let service, ws;
  const httpPort = await port(), wsPort = await port();
  const base = 'http://127.0.0.1:' + httpPort;
  const data = path.join(temp, 'data');
  fs.mkdirSync(data);
  const config = path.join(temp, 'isolated.json');
  fs.writeFileSync(config, JSON.stringify({httpPort, wsPort, listen: '127.0.0.1',
    windowUrl: 'http://127.0.0.1:1/say', heartbeatMs: 60000}));
  const stateFile = path.join(data, 'transport-state.json');
  const events = [];
  async function connect() {
    ws = new WebSocket('ws://127.0.0.1:' + wsPort);
    ws.onmessage = event => { const value = JSON.parse(event.data); if (value.post_type === 'message') events.push(value); };
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  }
  async function post(route, body) {
    const response = await fetch(base + route, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    return {status: response.status, body: await response.json()};
  }
  async function start() {
    service = spawn(process.execPath, [path.join(serviceRoot, 'server/onebot-local.mjs'),
      '--config', config, '--data-dir', data], {cwd: temp, windowsHide: true, stdio: 'ignore'});
    await until(async () => (await fetch(base + '/__state')).ok);
  }
  async function stop() {
    if (service && service.exitCode === null) {
      const closed = new Promise(resolve => service.once('close', resolve));
      service.kill(); await closed;
    }
  }
  try {
    await start();
    const message = {text: 'synthetic offline', message_id: 'client-1', event_id: 'event-1', conversation_id: 'test'};
    assert.equal((await post('/__inject', message)).body.accepted, true);
    assert.equal(JSON.parse(fs.readFileSync(stateFile)).pending.length, 1);
    await connect();
    await until(() => events.length === 1);
    const saved = JSON.parse(fs.readFileSync(stateFile));
    assert.equal(saved.pending.length, 1, 'reconnecting must preserve an attempted event');
    assert.equal(saved.pending[0].status, 'framework_unknown');
    ws.close();
    await until(async () => (await (await fetch(base + '/__state')).json()).connections === 0);
    await connect();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(events.length, 1, 'reconnect must not trigger the model again');
    const duplicate = await post('/__inject', message);
    assert.equal(duplicate.body.delivery_status, 'framework_unknown');
    assert.equal(events.length, 1);
    ws.close(); await stop(); await start(); await connect();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(events.length, 1, 'restart must retain unknown result without replay');
    // Fail an atomic state write within this disposable test directory.
    fs.mkdirSync(stateFile + '.part');
    const failure = await post('/__inject', {...message, message_id: 'client-disk', event_id: 'event-disk'});
    assert.equal(failure.status, 503);
    assert.equal(failure.body.ok, false);
    assert.equal(events.length, 1, 'failed persistence must not count as success or send');
    console.log('ONEBOT_DELIVERY=PASS checks=13 queued=durable reconnect_replays=0 save_failure=503');
  } finally {
    if (ws) ws.close();
    await stop();
    assert.equal(path.dirname(temp), path.resolve(os.tmpdir()));
    fs.rmSync(temp, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
