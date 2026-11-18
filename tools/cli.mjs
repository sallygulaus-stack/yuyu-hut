// cli · 不用面板也能聊（node tools/cli.mjs）
//
// 证明一件事：onebot-local 是独立能用的东西，面板只是它的一个客户端。
// 这个脚本干两头：
//   · 你敲一行 → 当成「用户发来一条私聊」推给框架
//   · 框架发出来 → 打在屏幕上
//
// 需要 Node 22 以上（内置 WebSocket）。先起 server，再跑这个。
import readline from "node:readline";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const 根 = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
let 配置 = JSON.parse(fs.readFileSync(path.join(根, "server", "config.example.json"), "utf8"));
try { 配置 = {...配置, ...JSON.parse(fs.readFileSync(path.join(根, "server", "config.json"), "utf8"))}; } catch { /* 用默认 */ }

const 端口 = process.env.ONEBOT_HTTP_PORT || 配置.httpPort;
const ws端口 = process.env.ONEBOT_WS_PORT || 配置.wsPort;
const 令牌 = process.env.ONEBOT_TOKEN || 配置.token || "";
const HTTP = `http://127.0.0.1:${端口}`;
const 头 = { "Content-Type": "application/json", ...(令牌 ? { Authorization: "Bearer " + 令牌 } : {}) };

console.log("连 " + HTTP + " …（Ctrl+C 退出）\n");

const ws地址 = new URL(`ws://127.0.0.1:${ws端口}`);
if (令牌) ws地址.searchParams.set("access_token", 令牌);
const ws = new WebSocket(ws地址);

ws.onopen = () => {
  console.log("连上了。敲一行回车即可发送。\n");
  开输入();
};

ws.onmessage = (e) => {
  let m;
  try { m = JSON.parse(e.data); } catch { return; }
  if (m.post_type === "meta_event") return;          // 心跳不显示
  if (m.post_type === "message") {
    const 片 = (m.message || []).map((段) => {
      const t = 段.type, d = 段.data || {};
      if (t === "text") return d.text;
      if (t === "image") return `[图片 ${d.file || d.url || ""}]`;
      if (t === "record") return `[语音 ${d.file || ""}]`;
      if (t === "face") return `[表情 ${d.id}]`;
      return `[${t}]`;
    });
    console.log("\x1b[35m她 ›\x1b[0m " + 片.join(""));
    return;
  }
  if (m.status === "failed") console.log("\x1b[31m框架报错\x1b[0m " + JSON.stringify(m));
};

ws.onclose = () => {
  console.log("\n断开了。server 还起着吗？");
  process.exit(0);
};
ws.onerror = () => {
  console.log("\x1b[31m连不上\x1b[0m ws://127.0.0.1:" + ws端口 + "，先看看 server 起来没。");
  process.exit(1);
};

function 开输入() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  rl.on("line", async (行) => {
    const 文 = 行.trim();
    if (!文) return;
    try {
      const r = await fetch(HTTP + "/__inject", {
        method: "POST", headers: 头, body: JSON.stringify({ text: 文 }),
      });
      const d = await r.json();
      if (d.ok) console.log("\x1b[36m你 ›\x1b[0m " + 文 + (d.pushed ? "" : "  \x1b[33m(框架没接，先存着了)\x1b[0m"));
      else console.log("\x1b[31m发不出去\x1b[0m " + (d.error || ""));
    } catch (e) {
      console.log("\x1b[31m发不出去\x1b[0m " + e.message);
    }
  });
}
