// doctor · 自检（node tools/doctor.mjs）
//
// 一件一件查：配置读到了吗、server 起着吗、框架连上了吗、能不能发一条。
// 出问题会直接说「下一步该看哪儿」，不只是一句 failed。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const 根 = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function 读json(p) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch { return null; }
}

const server配置 = { ...读json(path.join(根, "server", "config.example.json")),
  ...读json(path.join(根, "server", "config.json")) };
const panel默认 = 读json(path.join(根, "panel", "config.example.json"));
const panel本地 = 读json(path.join(根, "panel", "config.json"));
const panel配置 = { ...panel默认, ...panel本地,
  onebot: { ...panel默认.onebot, ...panel本地?.onebot } };

const 地址 = process.env.ONEBOT_HTTP_PORT
  ? `http://127.0.0.1:${process.env.ONEBOT_HTTP_PORT}`
  : `http://127.0.0.1:${server配置.httpPort || 3000}`;
const 令牌 = process.env.ONEBOT_TOKEN || server配置.token || "";

const 好 = (s) => console.log("  \x1b[32m✓\x1b[0m " + s);
const 坏 = (s) => console.log("  \x1b[31m✗\x1b[0m " + s);
const 劝 = (s) => console.log("  \x1b[33m!\x1b[0m " + s);

console.log("\nonebot-local 自检\n");

// 1. 配置文件
if (读json(path.join(根, "server", "config.json"))) 好("server/config.json 读到了");
else 劝("没有 server/config.json，用默认值（3000 / 3001 / 127.0.0.1）");

if (读json(path.join(根, "panel", "config.json"))) 好("panel/config.json 读到了");
else 好("没有 panel/config.json，面板默认使用离线演示模式");
if (panel配置.mode !== "onebot") 劝("面板当前不是 OneBot 模式；演示面板无需本地端");

// 2. server 在不在
let 状态 = null;
try {
  const r = await fetch(地址 + "/__state", {
    headers: 令牌 ? { Authorization: "Bearer " + 令牌 } : {},
    signal: AbortSignal.timeout(4000),
  });
  if (r.status === 401) {
    坏(`server 在 ${地址}，但 token 不对`);
    劝("server/config.json 和 panel/config.json 里的 token 要填一样");
  } else {
    状态 = await r.json();
    好(`server 在 ${地址}（身份 ${状态.selfId} → 对方 ${状态.peerId}）`);
  }
} catch (e) {
  坏(`连不上 ${地址}：${e.cause?.code || e.message}`);
  劝("先在 server 目录跑 npm start，看看它自己打印了什么");
}

if (状态) {
  // 3. 框架连上没
  if (状态.connections > 0) {
    好(`有 ${状态.connections} 个机器人框架连在 WebSocket 上`);
  } else {
    坏("还没有框架连上来");
    劝(`把框架的 OneBot 地址指到 ws://127.0.0.1:${server配置.wsPort || 3001}`);
    if (状态.actions?.length) {
      好("不过它调过这些动作（说明连过一次）：" +
        状态.actions.map(([n, c]) => `${n}×${c}`).join("  "));
    }
  }

  // 4. 面板那边
  const 面板口 = panel配置.port;
  if (server配置.windowUrl && String(server配置.windowUrl).includes(String(面板口))) {
    好(`server 会往 ${server配置.windowUrl} 推消息（面板的收话口）`);
  } else {
    劝(`server 的 windowUrl 是 ${server配置.windowUrl || "(默认)"}，确认那是面板的地址`);
  }
}

// 5. 试发一条
if (process.argv.includes("--send") && 状态 && 状态.connections > 0) {
  try {
    const r = await fetch(地址 + "/__inject", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(令牌 ? { Authorization: "Bearer " + 令牌 } : {}),
      },
      body: JSON.stringify({ text: "（这是一条自检消息）" }),
      signal: AbortSignal.timeout(8000),
    });
    const d = await r.json();
    if (d.ok && d.pushed > 0) 好("试发一条：已经推给框架了");
    else 劝("试发成功但没人接（框架可能刚断开）");
  } catch (e) {
    坏("试发失败：" + e.message);
  }
}
if (!process.argv.includes("--send")) console.log("  默认只检查连接；加 --send 才发送自检消息。");

console.log("\n查完了。任何一条打 ✗，先照着它下面的提示看一眼。\n");
