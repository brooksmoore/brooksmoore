/* Studio Lights -- browser front end.
 *
 * Speech recognition runs in the browser (no audio ever leaves the device);
 * the transcript is POSTed to the local server, which parses it and drives the
 * bulbs over the LAN. Everything here is written to survive the microphone
 * being unavailable: the cards, sliders, scene chips and text box all work on
 * their own.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);

const el = {
  bulbs: $("#bulbs"),
  scenes: $("#scene-chips"),
  reply: $("#reply"),
  history: $("#history"),
  transcript: $("#transcript"),
  mic: $("#mic"),
  alwaysOn: $("#always-on"),
  speakBack: $("#speak-back"),
  dot: $("#link-dot"),
  typeForm: $("#type-form"),
  typeInput: $("#type-input"),
  help: $("#help"),
  helpBody: $("#help-body"),
};

const state = {
  devices: [],
  vocab: null,
  listening: false,
  holding: false,
  interim: "",
  sending: false,
};

/* ------------------------------------------------------------------ prefs */

const prefs = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem("voicelights." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (_) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem("voicelights." + key, JSON.stringify(value));
    } catch (_) {
      /* private mode: preferences just don't persist */
    }
  },
};

/* ------------------------------------------------------------------ audio */

let audioCtx = null;
function beep(kind) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const tones = { start: [880], ok: [660, 990], bad: [220, 165] };
    const seq = tones[kind] || tones.ok;
    seq.forEach((freq, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      const at = audioCtx.currentTime + i * 0.07;
      osc.frequency.value = freq;
      osc.type = "sine";
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.08, at + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.09);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(at);
      osc.stop(at + 0.1);
    });
  } catch (_) {
    /* audio is a nicety, never a requirement */
  }
}

function buzz(ms) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

function speak(text) {
  if (!el.speakBack.checked || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.25;
  utter.volume = 0.8;
  window.speechSynthesis.speak(utter);
}

/* --------------------------------------------------------------- rendering */

function bulbColor(dev) {
  const s = dev.state;
  if (!s.on) return "#3a3a46";
  if (s.hex) return s.hex;
  if (s.temperature != null) {
    // 1 = warmest, 100 = coolest. Approximate the white point for the swatch.
    const t = s.temperature / 100;
    const r = Math.round(255 - 35 * t);
    const g = Math.round(180 + 55 * t);
    const b = Math.round(90 + 150 * t);
    return `rgb(${r},${g},${b})`;
  }
  return "#ffb547";
}

function describe(dev) {
  const s = dev.state;
  if (!s.online) return s.error ? "offline" : "no reply";
  if (!s.on) return "off";
  const bits = [`${s.luminance == null ? "?" : s.luminance}%`];
  if (s.hex) bits.push(s.hex);
  else if (s.temperature != null) bits.push(s.temperature >= 60 ? "cool white" : "warm white");
  return bits.join(" · ");
}

function renderBulbs() {
  el.bulbs.innerHTML = "";
  state.devices.forEach((dev) => {
    const card = document.createElement("div");
    const color = bulbColor(dev);
    card.className =
      "bulb" + (dev.state.on ? " on" : "") + (dev.state.online ? "" : " offline");
    card.style.setProperty("--glow", color);

    const level = dev.state.on ? dev.state.luminance || 0 : 0;
    card.innerHTML = `
      <div class="fill" style="width:${level}%"></div>
      <div class="bulb-top">
        <div class="swatch" style="background:${color}"></div>
        <div class="bulb-text">
          <div class="bulb-name"></div>
          <div class="bulb-sub"></div>
        </div>
        <div class="power" role="switch" tabindex="0"
             aria-checked="${dev.state.on ? "true" : "false"}"></div>
      </div>
      <input class="slider" type="range" min="0" max="100" value="${level}"
             aria-label="brightness">`;

    card.querySelector(".bulb-name").textContent = dev.label;
    card.querySelector(".bulb-sub").textContent = describe(dev);

    const power = card.querySelector(".power");
    const toggle = () => {
      buzz(8);
      send({ action: "toggle", targets: [dev.id] });
    };
    power.addEventListener("click", toggle);
    power.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    });

    const slider = card.querySelector(".slider");
    slider.addEventListener("input", () => {
      card.querySelector(".fill").style.width = slider.value + "%";
    });
    slider.addEventListener("change", () => {
      send({ action: "brightness", targets: [dev.id], value: Number(slider.value) });
    });

    el.bulbs.appendChild(card);
  });
}

function renderScenes() {
  if (!state.vocab) return;
  el.scenes.innerHTML = "";
  Object.keys(state.vocab.scenes).forEach((id) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = id.charAt(0).toUpperCase() + id.slice(1);
    chip.addEventListener("click", () => {
      buzz(8);
      send({ action: "scene", value: id });
    });
    el.scenes.appendChild(chip);
  });
}

function pushHistory(said, ms, ok) {
  const li = document.createElement("li");
  if (!ok) li.className = "bad";
  const left = document.createElement("span");
  left.className = "said";
  left.textContent = said;
  const right = document.createElement("span");
  right.className = "ms";
  right.textContent = ms == null ? "" : ms + " ms";
  li.append(left, right);
  el.history.prepend(li);
  while (el.history.children.length > 6) el.history.lastChild.remove();
}

function showReply(text, ok) {
  el.reply.textContent = text;
  el.reply.classList.toggle("bad", !ok);
}

/* ---------------------------------------------------------------- network */

async function send(command) {
  return post("/api/command", command);
}

let transcriptTimer = null;

async function say(text) {
  if (!text || state.sending) return;
  state.sending = true;
  setTranscript(text, "final");
  const result = await post("/api/say", { text });
  state.sending = false;
  if (result) pushHistory(text, result.ms, result.ok);
  // The history row keeps the record; clear the prompt so it reads as ready.
  clearTimeout(transcriptTimer);
  transcriptTimer = setTimeout(() => {
    if (!state.listening) setTranscript("");
  }, 2500);
  return result;
}

async function post(path, body) {
  const started = performance.now();
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (data.devices) {
      state.devices = data.devices;
      renderBulbs();
    }
    showReply(data.reply || "", data.ok !== false);
    if (data.ok === false) {
      beep("bad");
      buzz([20, 60, 20]);
    } else {
      beep("ok");
      speak(data.reply);
    }
    data.ms = data.ms != null ? data.ms : Math.round(performance.now() - started);
    return data;
  } catch (err) {
    showReply("Lost the server. Is `run.py serve` still running?", false);
    el.dot.className = "dot down";
    beep("bad");
    return null;
  }
}

function connectEvents() {
  const source = new EventSource("/api/events");
  source.addEventListener("open", () => (el.dot.className = "dot live"));
  source.addEventListener("state", (event) => {
    try {
      state.devices = JSON.parse(event.data);
      renderBulbs();
      el.dot.className = "dot live";
    } catch (_) {}
  });
  source.addEventListener("error", () => {
    el.dot.className = "dot down";
    // EventSource reconnects on its own; this only reflects the gap.
  });
}

/* ------------------------------------------------------------------ speech */

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let wantListening = false;

function setTranscript(text, cls) {
  el.transcript.textContent = text || (SR ? "Hold to talk" : "Type a command below");
  el.transcript.className = "transcript" + (cls ? " " + cls : "");
}

function setListening(on) {
  state.listening = on;
  el.mic.classList.toggle("listening", on);
  if (on) {
    beep("start");
    buzz(12);
  }
}

function buildRecognition(continuous) {
  const rec = new SR();
  rec.continuous = continuous;
  rec.interimResults = true;
  rec.lang = navigator.language || "en-US";
  rec.maxAlternatives = 1;

  rec.addEventListener("start", () => setListening(true));

  rec.addEventListener("result", (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text = result[0].transcript.trim();
      if (result.isFinal) {
        state.interim = "";
        say(text);
        if (!continuous) stop();
        return;
      }
      interim += text + " ";
    }
    state.interim = interim.trim();
    setTranscript(state.interim, "interim");
  });

  rec.addEventListener("error", (event) => {
    if (event.error === "no-speech" || event.error === "aborted") return;
    el.mic.classList.add("error");
    setTimeout(() => el.mic.classList.remove("error"), 1500);
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      wantListening = false;
      el.alwaysOn.checked = false;
      showReply(
        window.isSecureContext
          ? "Microphone permission was refused. Allow it in site settings."
          : "The browser only allows the microphone over https:// or on localhost. Restart the server with --tls.",
        false
      );
    }
  });

  rec.addEventListener("end", () => {
    setListening(false);
    setTranscript("");
    // Chrome ends a continuous session every minute or so; restart it.
    if (wantListening) {
      setTimeout(() => {
        if (wantListening) {
          try {
            rec.start();
          } catch (_) {}
        }
      }, 180);
    }
  });

  return rec;
}

function start(continuous) {
  if (!SR) return;
  if (recognition && state.listening) return;
  recognition = buildRecognition(continuous);
  try {
    recognition.start();
  } catch (_) {
    /* already starting */
  }
}

function stop() {
  wantListening = false;
  if (recognition) {
    try {
      recognition.stop();
    } catch (_) {}
  }
}

/* Push-to-talk. Releasing submits whatever has been heard so far rather than
 * waiting for the engine's end-of-speech timeout, which is most of why this
 * feels quicker than a smart speaker. */
function holdStart(event) {
  event.preventDefault();
  if (el.alwaysOn.checked) return;
  state.holding = true;
  start(false);
}

function holdEnd(event) {
  if (!state.holding) return;
  event.preventDefault();
  state.holding = false;
  const heard = state.interim;
  stop();
  if (heard) {
    state.interim = "";
    say(heard);
  }
}

/* -------------------------------------------------------------------- help */

async function loadVocab() {
  try {
    const resp = await fetch("/api/vocab");
    state.vocab = await resp.json();
  } catch (_) {
    return;
  }
  renderScenes();

  const names = Object.values(state.vocab.devices).map((a) => a[0]);
  const examples = [
    `turn on the ${(names[0] || "lamp").toLowerCase()}`,
    "lights out",
    `${(names[1] || "lamp").toLowerCase()} to 40 percent`,
    "brighter",
    "a bit dimmer",
    `make the ${(names[2] || "lamp").toLowerCase()} deep blue`,
    "warm white",
    "torch and lamp off",
    "movie mode",
    "what's on",
  ];
  const wake = state.vocab.wake_words || [];
  el.helpBody.innerHTML = `
    <h3>Examples</h3>
    <div>${examples.map((e) => `<code>${e}</code>`).join("")}</div>
    <h3>Lights</h3>
    <div>${Object.values(state.vocab.devices)
      .map((a) => `<code>${a.slice(0, 3).join(" / ")}</code>`)
      .join("")}</div>
    <h3>Scenes</h3>
    <div>${Object.entries(state.vocab.scenes)
      .map(([id, phrases]) => `<code>${(phrases[0] || id)}</code>`)
      .join("")}</div>
    <h3>Colours</h3>
    <div>${state.vocab.colors.map((c) => `<code>${c}</code>`).join("")}</div>
    <h3>Whites</h3>
    <div>${state.vocab.temperatures.map((c) => `<code>${c}</code>`).join("")}</div>
    ${wake.length ? `<h3>Always-on wake words</h3><div>${wake
      .map((w) => `<code>hey ${w}</code>`)
      .join("")}</div>` : ""}`;
}

/* -------------------------------------------------------------------- init */

function wire() {
  el.mic.addEventListener("pointerdown", holdStart);
  el.mic.addEventListener("pointerup", holdEnd);
  el.mic.addEventListener("pointercancel", holdEnd);
  el.mic.addEventListener("pointerleave", holdEnd);
  el.mic.addEventListener("contextmenu", (e) => e.preventDefault());

  el.alwaysOn.checked = prefs.get("alwaysOn", false);
  el.speakBack.checked = prefs.get("speakBack", false);

  el.alwaysOn.addEventListener("change", () => {
    prefs.set("alwaysOn", el.alwaysOn.checked);
    if (el.alwaysOn.checked) {
      wantListening = true;
      start(true);
      setTranscript("");
    } else {
      stop();
    }
  });
  el.speakBack.addEventListener("change", () =>
    prefs.set("speakBack", el.speakBack.checked)
  );

  el.typeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el.typeInput.value.trim();
    if (!text) return;
    el.typeInput.value = "";
    say(text);
  });

  $("#refresh-btn").addEventListener("click", async () => {
    const resp = await fetch("/api/refresh");
    const data = await resp.json();
    state.devices = data.devices;
    renderBulbs();
  });

  $("#help-btn").addEventListener("click", () => el.help.showModal());
  $("#help-close").addEventListener("click", () => el.help.close());

  // Space bar as push-to-talk on a laptop.
  document.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat) return;
    if (document.activeElement === el.typeInput) return;
    event.preventDefault();
    holdStart(event);
  });
  document.addEventListener("keyup", (event) => {
    if (event.code !== "Space") return;
    if (document.activeElement === el.typeInput) return;
    holdEnd(event);
  });

  if (!SR) {
    el.mic.disabled = true;
    el.mic.title = "This browser has no speech recognition";
    setTranscript("");
    showReply(
      "No speech recognition in this browser -- try Chrome, or use the controls above.",
      false
    );
  }
}

async function init() {
  wire();
  try {
    const resp = await fetch("/api/state");
    state.devices = (await resp.json()).devices;
    renderBulbs();
  } catch (_) {
    showReply("Could not reach the server.", false);
  }
  await loadVocab();
  connectEvents();
  if (el.alwaysOn.checked && SR) {
    wantListening = true;
    start(true);
  }
  setTranscript("");
}

init();
