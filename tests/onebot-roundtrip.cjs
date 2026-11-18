/* Real local-service round trip using Node's built-in framework WebSocket. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'panel-onebot-'));
const secret = crypto.randomBytes(24).toString('hex');
const processes = [];
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function until(test) {
  for (let index = 0; index < 100; index++) {
    const result = await test();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('等待本地连接超时');
}

(async () => {
  let ws;
  let errorOutput = '';
  try {
    const httpPort = await freePort();
    const wsPort = await freePort();
    const config = path.join(temp, 'config.json');
    fs.writeFileSync(config, JSON.stringify({mode: 'onebot', onebot: {
      enabled: true, base_url: 'http://127.0.0.1:' + httpPort, token: secret
    }}));
    const panel = spawn(process.env.PYTHON || 'python', ['-B', path.join(root, 'panel/panel.py'),
      '--no-browser', '--config', config, '--data-dir', path.join(temp, 'panel')], {
      cwd: temp, windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'}
    });
    processes.push(panel);
    panel.stderr.on('data', data => { errorOutput += data; });
    const address = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('面板启动超时')), 30000);
      readline.createInterface({input: panel.stdout}).on('line', line => {
        try {
          const info = JSON.parse(line);
          if (info.url) { clearTimeout(timer); resolve(new URL(info.url).origin); }
        } catch (_) {}
      });
      panel.once('error', error => { clearTimeout(timer); reject(error); });
    });
    const service = spawn(process.execPath, [path.join(root, 'server/onebot-local.mjs'),
      '--data-dir', path.join(temp, 'onebot')], {cwd: temp, windowsHide: true,
      env: {...process.env, ONEBOT_HTTP_PORT: String(httpPort), ONEBOT_WS_PORT: String(wsPort),
        ONEBOT_TOKEN: secret, ONEBOT_WINDOW: address + '/say', ONEBOT_LISTEN: '127.0.0.1'}
    });
    processes.push(service);
    service.stderr.on('data', data => { errorOutput += data; });
    service.stdout.resume();
    const serverUrl = 'http://127.0.0.1:' + httpPort;
    const auth = {Authorization: 'Bearer ' + secret};
    await until(async () => {
      try { return (await fetch(serverUrl + '/__state', {headers: auth})).ok; } catch (_) { return false; }
    });
    assert.equal((await fetch(serverUrl + '/__state')).status, 401);
    const denied = new WebSocket('ws://127.0.0.1:' + wsPort);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('缺令牌连接未被拒绝')), 5000);
      denied.onerror = () => { clearTimeout(timer); resolve(); };
      denied.onopen = () => { clearTimeout(timer); denied.close(); reject(new Error('缺令牌连接被接受')); };
    });
    ws = new WebSocket('ws://127.0.0.1:' + wsPort + '/?access_token=' + secret);
    const events = [];
    ws.onmessage = event => { events.push(JSON.parse(event.data)); };
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    const bootstrap = await (await fetch(address + '/api/bootstrap')).json();
    const call = async (method, args = []) => (await fetch(address + '/api/' + method, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Panel-Token': bootstrap.session_token},
      body: JSON.stringify(args)
    })).json();
    assert.equal((await call('chat_ready')).通, true);
    assert.equal((await call('chat_send', ['连接验收'])).ok, true);
    await until(() => events.find(event => event.post_type === 'message'));
    assert.equal(events.find(event => event.post_type === 'message').raw_message, '连接验收');
    ws.send(JSON.stringify({action: 'send_private_msg', params: {message: '框架验收回复'}, echo: 'test'}));
    await until(() => events.find(event => event.echo === 'test'));
    assert.equal(events.find(event => event.echo === 'test').retcode, 0);
    const history = await until(async () => {
      const page = await call('chat_history');
      return page.总 === 2 && page;
    });
    assert.deepEqual(history.消息.map(row => row.谁), ['我', '她']);
    assert.equal(history.消息[1].段[0].text, '框架验收回复');
    assert.equal((await fetch(address + '/say', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{"text":"拒绝"}'
    })).status, 403);
    console.log('ONEBOT_ROUNDTRIP=PASS authentication=HTTP+WebSocket+callback messages=2');
  } finally {
    if (ws) ws.close();
    for (const child of processes) {
      if (child.exitCode === null) {
        const closed = new Promise(resolve => child.once('close', resolve));
        child.kill();
        await closed;
      }
    }
    assert.equal(path.dirname(temp), path.resolve(os.tmpdir()));
    fs.rmSync(temp, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
