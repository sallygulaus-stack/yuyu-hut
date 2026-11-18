// onebot-local · 本地 OneBot v11 服务端（2026-11-12）
//
// 干什么用的：给机器人框架一个「假的 QQ」。它只说 OneBot v11 这一套公开协议，
// 上游接你的聊天面板，下游接任何认 OneBot 的框架（qq-bridge、NoneBot、Koishi……）。
//
// 它不碰腾讯：不装 QQ、不注入进程、不逆向任何私有协议、不连任何 QQ 服务器。
// 全部通信只在本机（默认只听 127.0.0.1 的两个端口）。
//
// 端口：HTTP 3000（动作 + 注入口）/ WS 3001（事件 + 动作，框架连这个）
//       3000/3001 是 OneBot 生态的常见口，改 config.json 就能换。
//
// 已实现（够跑通私聊）：
//   get_login_info · get_status · get_version_info
//   can_send_image · can_send_record · get_friend_list · get_stranger_info
//   send_private_msg · send_private_message · send_msg · get_msg
//   get_image（把本地文件读成 base64 递回去）
//   每 30 秒的 meta_event/heartbeat
// 没实现（会明确回 1404，不会静默）：
//   群聊相关全部、表情包同步（fetch_custom_face_detail）、戳一戳、撤回、红包
//
// 配置：见同目录 config.example.json，拷成 config.json 改。环境变量优先于文件。
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {parseArgs} from "node:util";
import { fileURLToPath } from "node:url";
import {writeFrame, dispatchQueued} from "./onebot-delivery.mjs";

const 这里 = path.dirname(fileURLToPath(import.meta.url));

// ── 配置 ─────────────────────────────────────────────────────────────
function 读配置() {
  const 参数 = parseArgs({options: {
    config: {type: "string"}, "data-dir": {type: "string"},
  }});
  const 默认 = JSON.parse(fs.readFileSync(path.join(这里, "config.example.json"), "utf8"));
  let 文件 = {};
  try {
    文件 = JSON.parse(fs.readFileSync(参数.values.config || path.join(这里, "config.json"), "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") console.warn("[配置] config.json 读不了，先用默认值：", e.message);
  }
  const 合 = { ...默认, ...文件 };
  const 环境映射 = {
    ONEBOT_HTTP_PORT: "httpPort", ONEBOT_WS_PORT: "wsPort", ONEBOT_LISTEN: "listen",
    ONEBOT_SELF: "selfId", ONEBOT_SELF_NAME: "selfName",
    ONEBOT_PEER: "peerId", ONEBOT_PEER_NAME: "peerName",
    ONEBOT_WINDOW: "windowUrl", ONEBOT_MEDIA: "mediaDir", ONEBOT_TOKEN: "token",
    ONEBOT_DATA_DIR: "dataDir",
  };
  for (const [环境名, 键] of Object.entries(环境映射)) {
    const 值 = process.env[环境名];
    if (值 !== undefined && 值 !== "") 合[键] = 值;
  }
  合.httpPort = Number(合.httpPort);
  合.wsPort = Number(合.wsPort);
  合.selfId = Number(合.selfId) || 10000;
  合.peerId = Number(合.peerId) || 10001;
  合.heartbeatMs = Math.max(5000, Number(合.heartbeatMs) || 30000);
  合.media_limits = {...默认.media_limits, ...合.media_limits};
  for (const value of Object.values(合.media_limits)) {
    if (!Number.isSafeInteger(value) || value < 1 || value > 50 * 1024 * 1024)
      throw new Error("媒体上限必须是 1 到 52428800 之间的整数字节数");
  }
  const 用户根 = process.platform === "win32"
    ? path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "QQPanel")
    : process.platform === "darwin"
    ? path.join(os.homedir(), "Library", "Application Support", "QQPanel")
    : path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "qq-panel");
  const 数据根 = path.resolve(参数.values["data-dir"] || 合.dataDir || path.join(用户根, "onebot"));
  合.dataDir = 数据根;
  合.mediaDir = 合.mediaDir ? path.resolve(数据根, String(合.mediaDir)) : path.join(数据根, "media");
  if (合.listen !== "127.0.0.1" && !合.token) throw new Error("非本机监听必须设置 token");
  return 合;
}

const 配置 = 读配置();
const 请求上限 = 4 * Math.ceil(Math.max(...Object.values(配置.media_limits)) / 3) + 1024 * 1024;

const 日志 = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// 媒体目录：面板递进来的图先落成文件，框架来 get_image 的时候再端出去。
// 为什么要落文件：桥对图片 file 字段有校验，只认「简单文件名」，base64:// 会被拒。
try { fs.mkdirSync(配置.mediaDir, { recursive: true }); } catch { /* 已经有了 */ }

const 状态文件 = path.join(配置.dataDir, "transport-state.json");
let 持久 = {endpointId: "ob_" + crypto.randomUUID(), messageId: 1000, eventId: 0,
  injections: {}, replies: {}, pending: [], callbacks: [], 往来: []};
try { 持久 = JSON.parse(fs.readFileSync(状态文件, "utf8")); }
catch (error) { if (error.code !== "ENOENT") throw error; }
// 老状态文件里没有「往来」（2026-11-19 加的聊天记录），直接整份读回来会缺字段。
持久.往来 ??= [];
持久.historySeq ??= 持久.往来.length;
持久.往来.forEach((row, index) => {
  row.序 ??= index + 1;
  row.message_id ??= `${持久.endpointId}:history:${row.序}`;
});
function 存往来(row) {
  const 原 = 持久.往来;
  持久.往来 = [...原, {...row, 序: ++持久.historySeq}].slice(-500);
  try { 存状态(); }
  catch (error) {
    持久.historySeq--;
    持久.往来 = 原;
    throw error;
  }
}
function 存状态() {
  const 临 = 状态文件 + ".part";
  const fd = fs.openSync(临, "w");
  try { fs.writeFileSync(fd, JSON.stringify(持久)); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(临, 状态文件);
}
存状态();
let 消息号 = 持久.messageId;
let 事件号 = 持久.eventId;
let 收到过的动作 = new Map();      // 动作名 → 次数，方便看框架到底调了什么
const 连接们 = new Set();
let 待发 = 持久.pending.map(item => item.event ? item : {event: item, status: "queued"});
持久.pending = 待发;
function 新编号() {
  持久.messageId = ++消息号;
  持久.eventId = ++事件号;
  return {message_id: 消息号, event_id: `${持久.endpointId}:evt:${事件号}`};
}

function 存图(b64, 原name, kind = "image") {
  const limit = 配置.media_limits[kind];
  if (b64.length > 4 * Math.ceil(limit / 3)) throw new Error("媒体超过配置上限");
  const raw = Buffer.from(b64, "base64");
  if (!raw.length || raw.length > limit) throw new Error("媒体为空或超过配置上限");
  const 扩 = (path.extname(String(原name || "")) || ".png").toLowerCase();
  const 名 = "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6) + 扩;
  fs.writeFileSync(path.join(配置.mediaDir, 名), raw);
  return 名;
}

// ── 鉴权 ─────────────────────────────────────────────────────────────
// 配置里 token 留空就完全不校验（本机单人用足够）。
// 一旦填了，动作口和注入口都要带：Authorization: Bearer <token>，或者 ?access_token=<token>。
function 过了鉴权(req, url) {
  if (!配置.token) return true;
  const 头 = String(req.headers.authorization || "");
  if (头 === "Bearer " + 配置.token) return true;
  if (url.searchParams.get("access_token") === 配置.token) return true;
  return false;
}

// ── WebSocket（手搓，不引依赖） ────────────────────────────────────────
const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

function 握手(req, socket) {
  const key = req.headers["sec-websocket-key"];
  if (!key) { socket.destroy(); return null; }
  const accept = crypto.createHash("sha1").update(key + GUID).digest("base64");
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
    "Upgrade: websocket\r\n" +
    "Connection: Upgrade\r\n" +
    `Sec-WebSocket-Accept: ${accept}\r\n\r\n`
  );
  const 连 = { socket, buf: Buffer.alloc(0), 活的: true };
  socket.on("error", () => { 连.活的 = false; 连接们.delete(连); });
  socket.on("close", () => { 连.活的 = false; 连接们.delete(连); });
  socket.on("end", () => { 连.活的 = false; 连接们.delete(连); });
  return 连;
}

function 发帧(socket, opcode, payload) {
  return writeFrame(socket, opcode, payload);
}

function 收帧(连, 数据) {
  连.buf = Buffer.concat([连.buf, 数据]);
  for (;;) {
    const b = 连.buf;
    if (b.length < 2) return;
    const opcode = b[0] & 0x0f;
    const masked = (b[1] & 0x80) !== 0;
    let len = b[1] & 0x7f;
    let off = 2;
    if (len === 126) {
      if (b.length < 4) return;
      len = b.readUInt16BE(2);
      off = 4;
    } else if (len === 127) {
      if (b.length < 10) return;
      len = Number(b.readBigUInt64BE(2));
      off = 10;
    }
    if (!Number.isSafeInteger(len) || len > 请求上限) {
      连.socket.destroy();
      return;
    }
    let mask = null;
    if (masked) {
      if (b.length < off + 4) return;
      mask = b.subarray(off, off + 4);
      off += 4;
    }
    if (b.length < off + len) return;
    let payload = Buffer.from(b.subarray(off, off + len));
    if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
    连.buf = b.subarray(off + len);

    if (opcode === 0x8) {                     // close
      try { 连.socket.end(); } catch { /* 已断 */ }
      连.活的 = false;
      连接们.delete(连);
      return;
    }
    if (opcode === 0x9) { 发帧(连.socket, 0xa, payload); continue; }   // ping → pong
    if (opcode === 0x1) {                     // text
      let 报文;
      try { 报文 = JSON.parse(payload.toString("utf8")); } catch { continue; }
      处理报文(连, 报文);
    }
  }
}

// ── OneBot 动作 ────────────────────────────────────────────────────────
function 段转文字(段们) {
  if (typeof 段们 === "string") return 段们;
  if (!Array.isArray(段们)) return "";
  const 片 = [];
  for (const 段 of 段们) {
    const t = 段?.type;
    const d = 段?.data ?? {};
    if (t === "text") 片.push(String(d.text ?? ""));
    else if (t === "at") 片.push(`@${d.qq === "all" ? "全体" : d.qq}`);
    else if (t === "image") 片.push("[图片]");
    else if (t === "record") 片.push("[语音]");
    else if (t === "face") 片.push(`[表情 ${d.id ?? ""}]`);
    else if (t === "mface") 片.push(`[表情 ${d.summary ?? d.emoji_id ?? ""}]`);
    else if (t === "reply") 片.push("");
    else 片.push(`[${t ?? "未知"}]`);
  }
  return 片.join("");
}

const 类型表 = {
  ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
  ".flac": "audio/flac", ".aac": "audio/aac", ".amr": "audio/amr",
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
  ".webp": "image/webp", ".bmp": "image/bmp",
};

function 猜类型(名) {
  return 类型表[String(path.extname(名 || "")).toLowerCase()] || "application/octet-stream";
}

// 把一段里的 file 弄成面板能直接用的小包：base64 原样、本地文件读出来、URL 交出去。
function 弄媒体(段) {
  const d = 段?.data ?? {};
  const 原 = String(d.file ?? d.url ?? "");
  if (!原) return null;
  const 种类 = 段.type === "record" ? "record" : "image";
  const limit = 配置.media_limits[种类];
  const reject = () => {
    throw Object.assign(new Error("媒体超过配置上限"), {permanent: true, code: "media_too_large"});
  };
  if (原.startsWith("base64://")) {
    if (原.length - 9 > 4 * Math.ceil(limit / 3)) reject();
    return { type: 种类, mime: 猜类型(d.filename ?? d.file ?? ""), b64: 原.slice(9) };
  }
  if (/^https?:\/\//i.test(原)) {
    return { type: 种类, mime: "", url: 原 };
  }
  let 原文件 = 原;
  // 有些工具会写「容器内路径」（比如 /app/data/media/x.png），而真文件在别处。
  // 配 legacyMediaPrefix / legacyMediaDir 就把前缀换掉，默认不启用。
  if (配置.legacyMediaPrefix && 原文件.startsWith(配置.legacyMediaPrefix)) {
    原文件 = path.join(配置.legacyMediaDir, 原文件.slice(配置.legacyMediaPrefix.length));
  }
  // 语音合成那边经常 silk 和 mp3 同名字并排放着，浏览器播不了 silk，优先拿 mp3。
  if (/\.silk$/i.test(原文件)) {
    const 替代 = 原文件.replace(/\.silk$/i, ".mp3");
    if (fs.existsSync(替代)) 原文件 = 替代;
  }
  try {
    if (fs.statSync(原文件).size > limit) reject();
    const 字节 = fs.readFileSync(原文件);
    return { type: 种类, mime: 猜类型(原文件), b64: 字节.toString("base64") };
  } catch (e) {
    if (e.permanent) throw e;
    日志("读不到媒体文件:", 原, e.message);
    return { type: 种类, mime: "", missing: 原 };
  }
}

function 整理段(段们) {
  if (typeof 段们 === "string") return [{ type: "text", text: 段们 }];
  if (!Array.isArray(段们)) return [];
  const 出 = [];
  for (const 段 of 段们) {
    const t = 段?.type;
    const d = 段?.data ?? {};
    if (t === "text") 出.push({ type: "text", text: String(d.text ?? "") });
    else if (t === "reply") continue;                      // 引用在小窗里没意义
    else if (t === "at") 出.push({ type: "text", text: `@${d.qq === "all" ? "全体" : d.qq} ` });
    else if (t === "face") 出.push({ type: "face", id: String(d.id ?? "") });
    else if (t === "image" || t === "record" || t === "mface") {
      const m = 弄媒体(段);
      if (m) 出.push(m);
    } else 出.push({ type: "text", text: `[${t ?? "未知"}]` });
  }
  return 出;
}

async function 送去窗口(item) {
  const 元数据 = item.metadata;
  const 地址 = 配置.windowUrl;
  try {
    const 整理好 = 整理段(item.segments);
    const 文 = 段转文字(item.segments);
    if (!整理好.length) throw Object.assign(new Error("回调没有可发送的内容"), {
      permanent: true, code: "empty_callback"});
    const r = await fetch(地址, {
      method: "POST",
      headers: { "Content-Type": "application/json",
        ...(配置.token ? {Authorization: "Bearer " + 配置.token} : {}) },
      body: JSON.stringify({
        text: 文,
        段: 整理好,
        message_id: String(元数据.message_id || ""),
        event_id: String(元数据.event_id || ""),
        sequence: Number(元数据.sequence || 0),
        conversation_id: 元数据.conversation_id || "main",
        profile_id: "peer",
        endpoint_id: "onebot-local",
        source_endpoint_id: 持久.endpointId,
        created_at: 元数据.created_at,
        sender: {profile_id: "peer", identity: String(配置.selfId), display_name: 配置.selfName},
        delivery_status: "delivered",
        protocol_version: "onebot-v11",
      }),
      signal: AbortSignal.timeout(15000),
    });
    const 确认 = await r.json();
    if (!r.ok && 确认.retryable === false && [400, 413, 415, 422].includes(r.status)) {
      throw Object.assign(new Error(String(确认.error || "网关拒绝了这条消息").slice(0, 200)), {
        permanent: true, code: 确认.error_code || "callback_rejected", httpStatus: r.status});
    }
    if (!r.ok || 确认.ok !== true ||
        String(确认.message_id) !== String(元数据.message_id) ||
        String(确认.event_id) !== String(元数据.event_id)) throw new Error("回调未确认");
    const previousCallbacks = 持久.callbacks;
    持久.callbacks = previousCallbacks.filter(x => x.metadata.event_id !== 元数据.event_id);
    try { 存状态(); }
    catch (error) {
      持久.callbacks = previousCallbacks;
      throw error;
    }
    const 摘要 = 整理好.map((x) => x.type).join("+");
    日志("→ 窗口", 摘要, r.ok ? "ok" : `http ${r.status}`);
  } catch (e) {
    item.attempts = (Number(item.attempts) || 0) + 1;
    item.status = e.permanent ? "rejected" : "pending";
    item.last_error = {code: e.code || "callback_unconfirmed",
      message: e.permanent ? e.message : "回调暂未确认，将沿原身份重试",
      http_status: e.httpStatus || null, retryable: !e.permanent, at: Date.now() / 1000};
    item.next_attempt_at = e.permanent ? null :
      Date.now() / 1000 + Math.min(30, 2 ** Math.min(item.attempts, 5));
    存状态();
    日志("→ 窗口", item.status, item.last_error.code);
  }
}

function 处理动作(action, params) {
  switch (action) {
    case "get_login_info":
      return { user_id: 配置.selfId, nickname: 配置.selfName };
    case "get_status":
      return { online: true, good: true };
    case "get_version_info":
      return { app_name: "onebot-local", app_version: "0.1.0", protocol_version: "v11" };
    case "can_send_image":
      return { yes: true };
    case "can_send_record":
      return { yes: true };
    case "get_friend_list":
      return [{ user_id: 配置.peerId, nickname: 配置.peerName, remark: 配置.peerName }];
    case "get_stranger_info":
      return String(params.user_id) === String(配置.selfId)
        ? {user_id: 配置.selfId, nickname: 配置.selfName, sex: "unknown", age: 0}
        : {user_id: 配置.peerId, nickname: 配置.peerName, sex: "unknown", age: 0};
    case "send_private_msg":
    case "send_private_message":
    case "send_msg": {
      const 段们 = params.message ?? params.messages ?? "";
      const key = params.client_message_id ? JSON.stringify([
        params.conversation_id || "main", String(params.client_message_id)]) : "";
      if (key && 持久.replies[key]) return 持久.replies[key];
      const ids = 新编号();
      const metadata = {message_id: `${持久.endpointId}:bot:${ids.message_id}`,
        event_id: ids.event_id, sequence: ids.message_id,
        conversation_id: params.conversation_id || "main", created_at: Date.now() / 1000};
      if (配置.windowUrl) 持久.callbacks.push({segments: 段们, metadata});
      if (key) 持久.replies[key] = ids;
      try {
        存往来({从: "她", 文: 段转文字(段们), 时: metadata.created_at, message_id: metadata.message_id});
      } catch (error) {
        if (配置.windowUrl) 持久.callbacks.pop();
        if (key) delete 持久.replies[key];
        throw error;
      }
      return ids;
    }
    case "get_msg":
      return { message_id: params.message_id ?? 0, message: [], sender: { user_id: 配置.selfId } };
    case "get_record":
    case "get_image": {
      const 名 = String(params.file ?? "");
      if (!/^[\w.+=@-]+$/.test(名)) return { file: 名 };
      try {
        const 字节 = fs.readFileSync(path.join(配置.mediaDir, 名));
        日志("框架来取图", 名, Math.round(字节.length / 1024) + "KB");
        return { file: 名, base64: 字节.toString("base64") };
      } catch {
        日志("取图没找到", 名);
        return { file: 名 };
      }
    }
    case "get_group_list":
      return [];          // 只做私聊，群聊一律空
    case "get_forward_msg":
    case "get_recent_messages":
    case "get_unread_messages":
      return { messages: [], files: [] };
    default:
      return null;        // 不认识的回 1404，让对方别再试
  }
}

function 回动作(连, echo, action, params) {
  let data = null;
  try {
    data = 处理动作(action, params);
  } catch (e) {
    日志("动作出错", action, e.message);
    data = null;
  }
  const 报文 = data === null
    ? JSON.stringify({ status: "failed", retcode: 1404, data: null, echo })
    : JSON.stringify({ status: "ok", retcode: 0, data, echo });
  发帧(连.socket, 0x1, Buffer.from(报文, "utf8"));
}

function 处理报文(连, 报文) {
  if (typeof 报文?.action === "string") {
    收到过的动作.set(报文.action, (收到过的动作.get(报文.action) ?? 0) + 1);
    回动作(连, 报文.echo, 报文.action, 报文.params ?? {});
    return;
  }
  // 框架不会主动发事件；真发了当心跳忽略
}

// ── 事件推送 ──────────────────────────────────────────────────────────
function 造私聊事件(text, 图 = [], 元数据 = {}) {
  const 段们 = [];
  if (text) 段们.push({ type: "text", data: { text } });
  for (const 一 of 图) {
    if (一?.b64) {
      const 名 = 存图(一.b64, 一.name);
      段们.push({ type: "image", data: { file: 名, sub_type: 1 } });
      日志("图已落盘", 名);
    } else if (一?.url) {
      段们.push({ type: "image", data: { url: String(一.url), sub_type: 1 } });
    }
  }
  for (const 段 of 元数据.segments || []) {
    if (段.type === "text") {
      if (!text) 段们.push({type: "text", data: {text: String(段.text || "")}});
    } else if (["image", "record"].includes(段.type) && 段.b64) {
      段们.push({type: 段.type, data: {file: 存图(段.b64, 段.name, 段.type)}});
    }
  }
  const ids = 新编号();
  return {
    post_type: "message",
    message_type: "private",
    sub_type: "friend",
    self_id: 配置.selfId,
    user_id: 配置.peerId,
    message_id: ids.message_id,
    client_message_id: 元数据.message_id || 元数据.client_message_id || "",
    event_id: ids.event_id,
    sequence: ids.message_id,
    conversation_id: 元数据.conversation_id || "main",
    protocol_version: "onebot-v11",
    message: 段们,
    raw_message: text || "[图片]",
    font: 0,
    sender: { user_id: 配置.peerId, nickname: 配置.peerName },
    time: Math.floor(Date.now() / 1000),
  };
}

function 广播(事件) {
  const 报文 = Buffer.from(JSON.stringify(事件), "utf8");
  let 数 = 0;
  for (const 连 of [...连接们]) {
    if (!连.活的) { 连接们.delete(连); continue; }
    if (发帧(连.socket, 0x1, 报文) !== "not_written") 数++;
  }
  return 数;
}

function 推一条(text, 图 = [], 元数据 = {}) {
  const 事件 = 造私聊事件(text, 图, 元数据);
  const entry = {event: 事件, status: "queued"};
  待发.push(entry);
  return entry;
}

function 投递回执(key) {
  const receipt = 持久.injections[key];
  const entry = 待发.find(item => JSON.stringify([
    item.event.conversation_id || "main", String(item.event.client_message_id)]) === key);
  const status = entry ? (entry.status === "queued" ? "framework_waiting" : "framework_unknown")
    : (receipt.delivery_status || (receipt.pushed ? "framework_unknown" : "framework_waiting"));
  return {...receipt, delivery_status: status,
    framework_status: status === "framework_waiting" ? "waiting" : "unknown",
    delivery_detail: status === "framework_waiting" ? "假端已保存，等待框架连接" :
      "已尝试写入框架连接；OneBot v11 没有事件处理确认，不自动重放"};
}

function 投递暂存() {
  const connection = [...连接们].find(item => item.活的 && !item.socket.destroyed && item.socket.writable);
  if (!connection) return;
  for (const entry of 待发) {
    if (entry.status !== "queued") continue;
    const key = JSON.stringify([entry.event.conversation_id || "main", String(entry.event.client_message_id)]);
    const save = () => {
      const receipt = 持久.injections[key];
      if (receipt) {
        receipt.delivery_status = entry.status === "queued" ? "framework_waiting" : "framework_unknown";
        receipt.pushed = entry.status === "queued" ? 0 : 1;
      }
      存状态();
    };
    dispatchQueued(entry, connection, save);
  }
}

// ── HTTP（动作 + 注入口 + 状态） ───────────────────────────────────────
function 读体(req) {
  return new Promise((resolve) => {
    const chunks = [];
    let bytes = 0;
    req.on("data", c => {
      bytes += c.length;
      if (bytes <= 请求上限) chunks.push(c);
    });
    req.on("end", () => {
      if (bytes > 请求上限) { resolve(null); return; }
      try { resolve(bytes ? JSON.parse(Buffer.concat(chunks, bytes).toString("utf8")) : {}); }
      catch { resolve({}); }
    });
    req.on("error", () => resolve({}));
  });
}

const 服务器 = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const 回 = (码, obj) => {
    const b = Buffer.from(JSON.stringify(obj));
    res.writeHead(码, { "Content-Type": "application/json; charset=utf-8", "Content-Length": b.length });
    res.end(b);
  };
  if (req.method === "POST") {
    if (!过了鉴权(req, url)) return 回(401, {ok: false, status: "failed", retcode: 1403, data: null});
    if (Number(req.headers["content-length"]) > 请求上限) {
      res.setHeader("Connection", "close");
      回(413, {ok: false, error: "请求超过媒体容量", error_code: "request_too_large", retryable: false});
      req.resume();
      return;
    }
  }
  let 请求体;
  if (req.method === "POST") {
    请求体 = await 读体(req);
    if (请求体 === null)
      return 回(413, {ok: false, error: "请求超过媒体容量", error_code: "request_too_large", retryable: false});
  }

  // 状态查询：面板拿它判断「框架连上没」
  if (url.pathname === "/__state") {
    if (!过了鉴权(req, url)) return 回(401, { ok: false, error: "token 不对" });
    return 回(200, {
      ok: true,
      selfId: 配置.selfId,
      peerId: 配置.peerId,
      selfName: 配置.selfName, peerName: 配置.peerName,
      endpointId: 持久.endpointId, version: "onebot-local/0.2",
      capabilities: {text: true, image: true, record: true, idempotent_send: true},
      media_limits: 配置.media_limits,
      connections: 连接们.size,
      messageId: 消息号,
      pending: {waiting: 待发.filter(item => item.status === "queued").length,
        unknown: 待发.filter(item => item.status === "framework_unknown").length,
        callbacks: 持久.callbacks.filter(item => item.status !== "rejected").length,
        rejected_callbacks: 持久.callbacks.filter(item => item.status === "rejected").length},
      actions: [...收到过的动作.entries()].sort((a, b) => b[1] - a[1]),
    });
  }

  // 往来的那一串（我注入的 + 机器人发出的），给面板「测试窗口」当聊天记录读。
  if (url.pathname === "/__history" && req.method === "GET") {
    if (!过了鉴权(req, url)) return 回(401, { ok: false, error: "token 不对" });
    return 回(200, {ok: true, history_id: 持久.endpointId, current: 持久.historySeq, 条: 持久.往来});
  }

  if (url.pathname === "/__callbacks" && req.method === "GET") {
    if (!过了鉴权(req, url)) return 回(401, {ok: false});
    return 回(200, {ok: true, items: 持久.callbacks.map(item => ({
      metadata: item.metadata, status: item.status || "pending", attempts: item.attempts || 0,
      last_error: item.last_error || null, next_attempt_at: item.next_attempt_at || null,
    }))});
  }
  if (url.pathname === "/__callbacks/retry" && req.method === "POST") {
    if (!过了鉴权(req, url)) return 回(401, {ok: false});
    const body = 请求体;
    const item = 持久.callbacks.find(value => value.metadata.event_id === body.event_id);
    if (!item) return 回(404, {ok: false, error: "找不到保留的回调"});
    const previous = {...item};
    try {
      item.status = "pending"; item.next_attempt_at = 0;
      存状态();
    } catch {
      Object.assign(item, previous);
      return 回(503, {ok: false, error: "重试状态未能保存"});
    }
    return 回(200, {ok: true, accepted: true, delivery_status: "callback_pending",
      message_id: item.metadata.message_id, event_id: item.metadata.event_id});
  }

  if (url.pathname === "/__delivery" && req.method === "POST") {
    if (!过了鉴权(req, url)) return 回(401, {ok: false});
    const body = 请求体;
    const key = JSON.stringify([body.conversation_id || "main", String(body.message_id || "")]);
    const receipt = 持久.injections[key];
    if (!receipt || receipt.event_id !== body.event_id) return 回(404, {ok: false, error: "unknown identity"});
    return 回(200, 投递回执(key));
  }

  // 注入口：面板把「用户说的话」塞进来，假端当一个私聊事件推给框架
  if (url.pathname === "/__inject" && req.method === "POST") {
    if (!过了鉴权(req, url)) return 回(401, { ok: false, error: "token 不对" });
    const 体 = 请求体;
    const text = String(体.text ?? "").trim();
    const 图 = Array.isArray(体.图) ? 体.图 : [];
    if (!text && !图.length && !(体.segments || []).length) return 回(400, { ok: false, error: "空话" });
    const clientId = String(体.message_id || 体.client_message_id || crypto.randomUUID());
    const key = JSON.stringify([体.conversation_id || "main", clientId]);
    try {
      if (持久.injections[key]) {
        if (体.event_id && 持久.injections[key].event_id !== 体.event_id)
          return 回(409, {ok: false, error: "identity conflict"});
        存状态();
        投递暂存();
        return 回(200, 投递回执(key));
      }
      const entry = 推一条(text, 图, {...体, message_id: clientId});
      持久.injections[key] = {ok: true, accepted: true, pushed: 0,
        message_id: clientId, event_id: String(体.event_id || entry.event.event_id),
        delivery_status: "framework_waiting", 记录序: 持久.historySeq + 1};
      try {
        存往来({从: "我", 文: text, 时: entry.event.time, message_id: clientId});
      } catch (error) {
        待发.pop();
        delete 持久.injections[key];
        throw error;
      }
      投递暂存();
      return 回(200, 投递回执(key));
    } catch {
      return 回(503, {ok: false, error: "message persistence or transport failed"});
    }
  }

  if (req.method === "POST") {
    if (!过了鉴权(req, url)) return 回(401, { status: "failed", retcode: 1403, data: null });
    const 体 = 请求体;
    const 路径 = url.pathname.replace(/^\/+/, "").replace(/\/+$/, "");

    // WebSocket 时代之后还留着 HTTP 动作口，是因为有些框架只走 HTTP。
    // 两种发法都认：
    //   ① 标准：POST /send_private_msg，body 就是参数
    //   ② 老式：POST /，body 里带 {action, params, echo}
    let action = null;
    let params = {};
    let echo;
    if (路径 && !路径.startsWith("__")) {
      action = 路径;
      params = 体 ?? {};
    } else if (typeof 体.action === "string") {
      action = 体.action;
      params = 体.params ?? {};
      echo = 体.echo;
    }
    if (!action) {
      return 回(400, { status: "failed", retcode: 1400, data: null, echo });
    }
    收到过的动作.set(action, (收到过的动作.get(action) ?? 0) + 1);
    const data = 处理动作(action, params);
    if (data === null) {
      日志("不支持的动作:", action);
      return 回(200, { status: "failed", retcode: 1404, data: null, echo });
    }
    return 回(200, { status: "ok", retcode: 0, data, echo });
  }

  回(404, { ok: false, error: "没有这个地址" });
});

const ws服务器 = http.createServer((req, res) => {
  res.writeHead(426, { "Content-Type": "text/plain; charset=utf-8" });
  res.end("这个端口只说 WebSocket\n");
});

ws服务器.on("upgrade", (req, socket) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.headers.origin || !过了鉴权(req, url)) {
    socket.end("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
    return;
  }
  const 连 = 握手(req, socket);
  if (!连) return;
  连接们.add(连);
  日志(`框架连上来了（当前 ${连接们.size} 个连接）`);
  socket.on("data", (d) => 收帧(连, d));
  if (待发.length) {
    try { 投递暂存(); }
    catch (error) { 日志("暂存未能投递", error.message); }
  }
});

服务器.listen(配置.httpPort, 配置.listen, () => {
  日志(`onebot-local · HTTP 就绪 http://${配置.listen}:${配置.httpPort}`);
  日志(`身份 ${配置.selfId}「${配置.selfName}」←→ 对方 ${配置.peerId}「${配置.peerName}」`);
  日志(`媒体目录 ${配置.mediaDir}`);
  日志(配置.token ? "鉴权：已开启（要带 token）" : "鉴权：关闭（只建议本机用）");
});
ws服务器.listen(配置.wsPort, 配置.listen, () => {
  日志(`onebot-local · WS 就绪 ws://${配置.listen}:${配置.wsPort}`);
});

// 很多框架有「上游健康检查」：一段时间收不到任何报文就判掉线、刷警告。
// 真 QQ 那边是靠心跳喂的，这里也得喂。
setInterval(() => {
  广播({
    post_type: "meta_event",
    meta_event_type: "heartbeat",
    self_id: 配置.selfId,
    status: { online: true, good: true },
    time: Math.floor(Date.now() / 1000),
    interval: 配置.heartbeatMs,
  });
}, 配置.heartbeatMs);

let 回调忙 = false;
setInterval(async () => {
  if (!配置.windowUrl || 回调忙) return;
  const item = 持久.callbacks.find(value => value.status !== "rejected" &&
    (Number(value.next_attempt_at) || 0) <= Date.now() / 1000);
  if (!item) return;
  回调忙 = true;
  try {
    await 送去窗口(item);
  } catch {
    日志("回调状态保存失败，保留待处理记录");
  } finally { 回调忙 = false; }
}, 500);
