// Jembatan long-connection Lark → AI Station.
//
// Menerima event im.message.receive_v1 lewat SDK resmi, meneruskannya ke
// /api/ingress/lark, lalu mengirim balasan worker kembali ke chat yang sama.

const lark = require("@larksuiteoapi/node-sdk");

const APP_ID = process.env.LARK_APP_ID || "";
const APP_SECRET = process.env.LARK_APP_SECRET || "";
const STATION = process.env.STATION_URL || "http://127.0.0.1:8999";

if (!APP_ID || !APP_SECRET) {
  console.error("lark-bridge: LARK_APP_ID / LARK_APP_SECRET belum diset");
  process.exit(1);
}

const domain =
  (process.env.LARK_DOMAIN || "lark").toLowerCase() === "feishu"
    ? lark.Domain.Feishu
    : lark.Domain.Lark;

const client = new lark.Client({
  appId: APP_ID,
  appSecret: APP_SECRET,
  appType: lark.AppType.SelfBuild,
  domain,
});

async function forward(chatId, text) {
  const response = await fetch(`${STATION}/api/ingress/lark`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    console.error(
      `station: ${body.error || response.status} (chat_id: ${chatId})`
    );
    return;
  }
  const reply = (body.reply || "").trim();
  if (!reply) return;
  await client.im.message.create({
    params: { receive_id_type: "chat_id" },
    data: {
      receive_id: chatId,
      msg_type: "text",
      content: JSON.stringify({ text: reply }),
    },
  });
}

const dispatcher = new lark.EventDispatcher({}).register({
  "im.message.receive_v1": async (data) => {
    try {
      const message = data.message || {};
      const sender = data.sender || {};
      console.log(
        `event: receive_v1 sender=${sender.sender_type || "-"} type=${
          message.message_type || "-"
        } chat=${message.chat_id || "-"}`
      );
      if (sender.sender_type === "app") return;
      if (message.message_type !== "text") return;
      const chatId = message.chat_id;
      const content = JSON.parse(message.content || "{}");
      const text = (content.text || "").trim();
      console.log(
        `event: message chat=${chatId || "-"} type=${message.message_type} bytes=${text.length}`
      );
      if (!chatId || !text) return;
      void forward(chatId, text).catch((error) =>
        console.error("forward:", error.message)
      );
    } catch (error) {
      console.error("event:", error.message);
    }
  },
});

const wsClient = new lark.WSClient({ appId: APP_ID, appSecret: APP_SECRET, domain });
wsClient.start({ eventDispatcher: dispatcher });
console.log("lark-bridge: terhubung, menunggu event…");
