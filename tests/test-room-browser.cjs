const assert = require('node:assert/strict');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const readline = require('node:readline');
const {once} = require('node:events');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright-core');

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'hut-test-room-'));
  const keyFile = path.join(temp, 'credential');
  fs.writeFileSync(keyFile, 'synthetic');
  const rows = [];
  let sends = 0;
  function row(from, text) {
    const seq = rows.length + 1;
    rows.push({序:seq, 从:from, 文:text, 时:1700000000+seq, message_id:String(seq)});
  }
  const server = http.createServer(async (req,res) => {
    let result;
    if (req.url === '/__state') result = {ok:true,peerId:300,connections:1};
    if (req.url === '/api/status' && req.headers['x-console-token'] === 'synthetic') result = {
      mode:'closed-agent',ownerQQ:300,allowPrivate:[300],allowGroups:[],dshReady:true,snowluma:{connected:true}};
    if (req.url === '/__history') result = {ok:true,current:rows.length,history_id:'fixture',条:rows};
    if (req.url === '/__inject' && req.method === 'POST') {
      const parts=[]; for await (const p of req) parts.push(p);
      const msg=JSON.parse(Buffer.concat(parts));
      row('我',msg.text); sends++; result={ok:true,message_id:String(rows.length),记录序:rows.length};
      row('她','隔离回复');
    }
    res.writeHead(result ? 200 : 404, {'Content-Type':'application/json'});
    res.end(JSON.stringify(result || {}));
  });
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  const route = 'http://127.0.0.1:'+server.address().port;
  const app=spawn(process.env.PYTHON || 'python',['-B','panel/panel.py','--data-dir',path.join(temp,'home'),'--no-browser','--test-window'],{
    cwd:path.resolve(__dirname,'..'),windowsHide:true,env:{...process.env, HUT_TEST_FEED:route,HUT_TEST_BRIDGE:route,
      HUT_TEST_PEER:'300',HUT_TEST_BRIDGE_TOKEN_FILE:keyFile,PYTHONIOENCODING:'utf-8'}});
  let browser;
  try {
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('startup timed out')),15000);
      readline.createInterface({input:app.stdout}).on('line',l=>{try{const r=JSON.parse(l);if(r.url){clearTimeout(timer);resolve(r.url);}}catch{}});
      app.once('exit',()=>{clearTimeout(timer);reject(Error('early exit'));});
    });
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage();
    await page.goto(url);
    const field=page.getByRole('textbox',{name:'测试消息'});
    await field.fill('隔离消息');
    await page.locator('#trialTestSend').click();
    await page.locator('#trialTestStream').getByText('隔离回复',{exact:true}).waitFor();
    await field.fill('尚未发出的草稿');
    await page.locator('[data-chat="yu"]').click();
    assert(!(await field.isVisible()));
    assert(!(await page.locator('#stream').textContent()).includes('隔离回复'));
    await page.locator('[data-chat="test"]').click();
    assert.equal(await field.inputValue(),'尚未发出的草稿');
    assert.equal(sends,1);
    await page.reload();
    await page.locator('#trialTestStream').getByText('隔离回复',{exact:true}).waitFor();
    assert.equal(sends,1);
    console.log('TEST_ROOM_UI=PASS selected_on_open=true isolated=true submissions=1 restored=1');
  } finally {
    if(browser) await browser.close();
    if(app.exitCode===null){const end=once(app,'exit');app.kill();await end;}
    server.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
