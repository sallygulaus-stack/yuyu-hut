// A successful socket write is transport uncertainty, never a framework receipt.
export function writeFrame(socket, opcode, payload) {
  if (socket.destroyed || !socket.writable || socket.writableEnded) return "not_written";
  const len = payload.length;
  let head;
  if (len < 126) {
    head = Buffer.from([0x80 | opcode, len]);
  } else if (len < 65536) {
    head = Buffer.alloc(4);
    head[0] = 0x80 | opcode;
    head[1] = 126;
    head.writeUInt16BE(len, 2);
  } else {
    head = Buffer.alloc(10);
    head[0] = 0x80 | opcode;
    head[1] = 127;
    head.writeBigUInt64BE(BigInt(len), 2);
  }
  try {
    // false means Node buffered the bytes; replay could duplicate a model call.
    return socket.write(Buffer.concat([head, payload])) ? "written" : "buffered";
  } catch {
    return "unknown";
  }
}

export function dispatchQueued(entry, connection, save) {
  if (entry.status !== "queued" || !connection?.活的 ||
      connection.socket.destroyed || !connection.socket.writable || connection.socket.writableEnded) return false;
  // Save before I/O so a crash cannot turn an attempted delivery into automatic replay.
  entry.status = "framework_unknown";
  try { save(); }
  catch (error) { entry.status = "queued"; throw error; }
  const outcome = writeFrame(connection.socket, 1, Buffer.from(JSON.stringify(entry.event), "utf8"));
  if (outcome === "not_written") {
    entry.status = "queued";
    save();
    return false;
  }
  return true;
}
