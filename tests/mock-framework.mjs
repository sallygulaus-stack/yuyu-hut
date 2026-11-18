// Synthetic OneBot framework. No DSH, model, credentials or user data.
import fs from "node:fs";
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
let socket;
let echo = 0;
const awaiting = new Map();
function call(action, params) {
  return new Promise((resolve, reject) => {
    const key = String(++echo);
    const timer = setTimeout(() => { awaiting.delete(key); reject(new Error("timeout")); }, 5000);
    awaiting.set(key, value => { clearTimeout(timer); resolve(value); });
    socket.send(JSON.stringify({action, params, echo: key}));
  });
}
function connect() {
  socket = new WebSocket(config.ws_url + "/?access_token=" + encodeURIComponent(config.token));
  socket.onopen = () => console.log("MOCK_READY");
  socket.onerror = () => {};
  socket.onclose = () => setTimeout(connect, 500);
  socket.onmessage = async event => {
    try {
      const value = JSON.parse(event.data);
      if (value.echo && awaiting.has(value.echo)) {
        awaiting.get(value.echo)(value);
        awaiting.delete(value.echo);
        return;
      }
      if (value.post_type !== "message") return;
      const segments = [{type: "text", data: {text: "synthetic reply: " +
        value.message.filter(s => s.type === "text").map(s => s.data.text).join("")}}];
      for (const seg of value.message) {
        if (seg.type === "image" || seg.type === "record") {
          const reply = await call(seg.type === "image" ? "get_image" : "get_record", {file: seg.data.file});
          if (reply.retcode !== 0 || !reply.data.base64) throw new Error("media unavailable remotely");
          segments.push({type: seg.type, data: {file: "base64://" + reply.data.base64,
            filename: seg.data.file}});
        }
      }
      await call("send_private_msg", {message: segments, conversation_id: value.conversation_id,
        client_message_id: "reply:" + (value.client_message_id || value.event_id)});
    } catch (error) { console.error("MOCK_ERROR", error.message); }
  };
}
connect();
