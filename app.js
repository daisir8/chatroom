/* 公网聊天室 —— 纯前端，通过公共 MQTT Broker (WebSocket) 实现公网实时通信 */
(function () {
  "use strict";

  // 默认公共 Broker（按序尝试，失败自动切换）
  const DEFAULT_BROKERS = [
    "wss://broker.hivemq.com:8884/mqtt",
    "wss://broker.emqx.io:8084/mqtt",
  ];

  // DOM
  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");
  const statusText = $("statusText");
  const roomInput = $("roomInput");
  const nameInput = $("nameInput");
  const brokerInput = $("brokerInput");
  const connectBtn = $("connectBtn");
  const toggleAdvanced = $("toggleAdvanced");
  const advanced = $("advanced");
  const membersEl = $("members");
  const messagesEl = $("messages");
  const msgInput = $("msgInput");
  const sendBtn = $("sendBtn");
  const emojiBtn = $("emojiBtn");
  const emojiPanel = $("emojiPanel");
  const scrollBottomBtn = $("scrollBottomBtn");

  // 状态
  let client = null;
  let connected = false;
  let myName = "";
  let room = "";
  let brokerIndex = 0;
  let heartbeatTimer = null;
  const online = new Map(); // name -> lastSeen
  const HEARTBEAT_MS = 4000;
  const TIMEOUT_MS = 10000;

  // 读取本地记忆
  roomInput.value = localStorage.getItem("chat_room") || "demo";
  nameInput.value = localStorage.getItem("chat_name") || ("用户" + Math.floor(Math.random() * 1000));
  brokerInput.value = localStorage.getItem("chat_broker") || "";

  function setStatus(state, text) {
    statusEl.dataset.state = state;
    statusText.textContent = text;
  }

  function topic(base) {
    return `workbuddy/chat/${room}/${base}`;
  }

  function nowTime() {
    return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderMessage({ who, text, time, me }) {
    const div = document.createElement("div");
    div.className = "msg" + (me ? " me" : "");
    div.innerHTML = `<div class="meta"><span class="who">${escapeHtml(who)}</span><span class="time">${time}</span></div><div class="text">${escapeHtml(text)}</div>`;
    messagesEl.appendChild(div);
    appendAndScroll();
  }

  function renderSystem(text) {
    const div = document.createElement("div");
    div.className = "msg system";
    div.textContent = text;
    messagesEl.appendChild(div);
    appendAndScroll();
  }

  function renderMembers() {
    if (online.size === 0) { membersEl.textContent = "—"; return; }
    membersEl.innerHTML = "";
    for (const name of online.keys()) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = name === myName ? name + "（我）" : name;
      membersEl.appendChild(tag);
    }
  }

  // ---- 智能吸底：贴近底部才自动滚到底；上翻看历史则不打扰 ----
  let stickToBottom = true;

  function isNearBottom() {
    const el = messagesEl;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
    stickToBottom = true;
    scrollBottomBtn.classList.remove("show");
  }

  function appendAndScroll() {
    if (stickToBottom) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else {
      scrollBottomBtn.classList.add("show");
    }
  }

  messagesEl.addEventListener("scroll", () => {
    if (isNearBottom()) {
      stickToBottom = true;
      scrollBottomBtn.classList.remove("show");
    } else {
      stickToBottom = false;
    }
  });
  scrollBottomBtn.addEventListener("click", scrollToBottom);

  // ---- 表情选择器 ----
  const EMOJIS = [
    "😀","😁","😂","🤣","😊","😍","😘","😎",
    "🤔","😅","😭","😡","👍","👎","👏","🙏",
    "💪","🤝","❤️","💔","🔥","✨","🎉","🌟",
    "🤖","💡","✅","❌","⭐","🌹","🌈","🍻",
    "☕","🚀","💯","📌","💬","😴","🤯","🥳",
    "😇","🙄","😬","👀","💰","⚡","🌍","🎯"
  ];

  function buildEmojiPanel() {
    emojiPanel.innerHTML = "";
    EMOJIS.forEach((e) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = e;
      b.addEventListener("click", () => insertEmoji(e));
      emojiPanel.appendChild(b);
    });
  }

  function insertEmoji(em) {
    const start = msgInput.selectionStart || 0;
    const end = msgInput.selectionEnd || 0;
    msgInput.value = msgInput.value.slice(0, start) + em + msgInput.value.slice(end);
    const pos = start + em.length;
    msgInput.focus();
    msgInput.setSelectionRange(pos, pos);
  }

  function toggleEmojiPanel(force) {
    const show = force !== undefined ? force : emojiPanel.classList.contains("hidden");
    emojiPanel.classList.toggle("hidden", !show);
  }

  emojiBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleEmojiPanel();
  });
  document.addEventListener("click", (e) => {
    if (!emojiPanel.contains(e.target) && e.target !== emojiBtn) toggleEmojiPanel(false);
  });

  function pruneOnline() {
    const now = Date.now();
    let changed = false;
    for (const [name, ts] of online) {
      if (now - ts > TIMEOUT_MS) { online.delete(name); changed = true; }
    }
    if (changed) renderMembers();
  }

  function publishPresence(type) {
    if (!client || !connected) return;
    client.publish(
      topic("presence"),
      JSON.stringify({ type, user: myName, t: Date.now() }),
      { qos: 0, retain: false }
    );
  }

  function connect() {
    room = (roomInput.value || "demo").trim();
    myName = (nameInput.value || "匿名").trim();
    localStorage.setItem("chat_room", room);
    localStorage.setItem("chat_name", myName);

    const custom = brokerInput.value.trim();
    if (custom) localStorage.setItem("chat_broker", custom);
    const brokers = custom ? [custom] : DEFAULT_BROKERS;
    brokerIndex = 0;

    if (client) { try { client.end(true); } catch (e) {} client = null; }
    setStatus("connecting", "连接中…");
    connectBtn.disabled = true;
    renderSystem(`正在连接房间「${room}」…`);

    const tryConnect = () => {
      if (brokerIndex >= brokers.length) {
        setStatus("offline", "连接失败");
        connectBtn.disabled = false;
        renderSystem("所有 Broker 均无法连接，请检查网络或自定义 Broker。");
        return;
      }
      const url = brokers[brokerIndex];
      setStatus("connecting", "连接中… " + new URL(url).host);
      client = mqtt.connect(url, {
        clientId: "wbchat_" + Math.random().toString(16).slice(2, 10),
        keepalive: 30,
        reconnectPeriod: 3000,
        clean: true,
        will: { topic: topic("presence"), payload: JSON.stringify({ type: "leave", user: myName }), qos: 0, retain: false },
      });

      client.on("connect", () => {
        connected = true;
        setStatus("online", "已连接");
        connectBtn.disabled = false;
        connectBtn.textContent = "断开";
        msgInput.disabled = false;
        sendBtn.disabled = false;
        emojiBtn.disabled = false;
        online.clear();
        online.set(myName, Date.now());
        renderMembers();
        client.subscribe([topic("messages"), topic("presence")], { qos: 0 });
        publishPresence("join");
        renderSystem(`已加入房间「${room}」`);
        clearInterval(heartbeatTimer);
        heartbeatTimer = setInterval(() => {
          publishPresence("heartbeat");
          pruneOnline();
        }, HEARTBEAT_MS);
      });

      client.on("message", (t, payload) => {
        let data;
        try { data = JSON.parse(payload.toString()); } catch (e) { return; }
        if (t === topic("messages")) {
          const me = data.user === myName;
          renderMessage({ who: data.user, text: data.text, time: data.time, me });
        } else if (t === topic("presence")) {
          if (data.user) {
            if (data.type === "leave") { online.delete(data.user); }
            else { online.set(data.user, Date.now()); }
            renderMembers();
          }
        }
      });

      client.on("error", (err) => {
        console.warn("MQTT error:", err && err.message);
      });

      client.on("close", () => {
        if (!connected) {
          // 当前 broker 失败，尝试下一个
          brokerIndex++;
          try { client.end(true); } catch (e) {}
          client = null;
          tryConnect();
          return;
        }
        connected = false;
        setStatus("offline", "已断开");
        msgInput.disabled = true;
        sendBtn.disabled = true;
        connectBtn.textContent = "连接";
        clearInterval(heartbeatTimer);
      });

      client.on("reconnect", () => setStatus("connecting", "重连中…"));
    };

    tryConnect();
  }

  function disconnect() {
    clearInterval(heartbeatTimer);
    publishPresence("leave");
    if (client) { try { client.end(false); } catch (e) {} }
    connected = false;
    client = null;
    setStatus("offline", "已断开");
    msgInput.disabled = true;
    sendBtn.disabled = true;
    emojiBtn.disabled = true;
    toggleEmojiPanel(false);
    connectBtn.textContent = "连接";
    renderSystem("已断开连接");
  }

  function sendMessage() {
    const text = msgInput.value.trim();
    if (!text || !client || !connected) return;
    client.publish(
      topic("messages"),
      JSON.stringify({ user: myName, text, time: nowTime() }),
      { qos: 0, retain: false }
    );
    msgInput.value = "";
  }

  // 事件绑定
  connectBtn.addEventListener("click", () => {
    if (connected || connectBtn.textContent === "断开") disconnect();
    else connect();
  });
  toggleAdvanced.addEventListener("click", () => advanced.classList.toggle("hidden"));
  sendBtn.addEventListener("click", sendMessage);
  msgInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

  buildEmojiPanel();
  setStatus("offline", "未连接");
})();
