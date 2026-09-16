# Studio Lights

Voice control for the three Meross bulbs in the studio, running entirely on the
local network. Hold a button, say what you want, the bulbs do it. No Siri, no
HomeKit round trip, no cloud.

```
"turn on the torch"            "lights out"
"lamp to 40 percent"           "torch and lamp off"
"make the mushroom deep blue"  "a bit dimmer"
"warm white"                   "movie mode"
```

## First, a correction

These are **Wi-Fi bulbs, not Bluetooth**. The MSL120 has no Bluetooth radio at
all — it joins your Wi-Fi during setup and is reachable at its own IP address
on the LAN. That is good news:

* Bluetooth would mean staying in range of the bulb and re-pairing constantly.
* Wi-Fi means anything on your network can talk to them, including this.
* Meross firmware exposes a local HTTP endpoint, so commands never leave the
  house. A command is one signed HTTP POST to the bulb — single-digit
  milliseconds on a LAN.

The reason Siri feels bad here is mostly the round trip: phone → Apple →
HomeKit hub → bulb, plus wake-word arbitration and Siri's own parsing. This
skips all of it.

## Try it before you set anything up

```bash
python3 run.py demo
```

That starts three *simulated* bulbs and opens the real interface at
<http://localhost:8099>. Nothing physical is touched. Hold the mic button (or
hold the space bar) and try the phrases above. It's the fastest way to see
whether the grammar matches how you actually speak.

## Real setup

Requires Python 3.9+. No dependencies to install.

### 1. Find the bulbs

```bash
python3 run.py discover
```

Scans your network, then **blinks each bulb three times** and asks which one it
was. That is how "Torch Light", "Lamp Light" and "Mushroom Light" get attached
to the right IP addresses — three identical MSL120s are otherwise
indistinguishable. Answers are written to `config.json`.

Naming them properly matters: a name the parser doesn't know is refused rather
than applied to every bulb, so `mushroom light off` before you've named
anything will tell you it doesn't know a "mushroom" instead of turning off the
whole studio.

If your bulbs have static DHCP reservations, great. If not, re-run `discover`
if one ever stops answering.

### 2. A device key, only if your bulbs want one

Local commands are signed with a per-account secret called the device key.
`discover` tries an empty key automatically, and **bulbs provisioned through
HomeKit rather than the Meross app often accept it** — in which case you are
already done and can skip this entirely.

If `discover` reports that your bulbs rejected every key, fetch the real one:

```bash
python3 run.py key
```

This signs in to your Meross account and saves the key to `config.json`. Your
password is used once and never stored.

If that fails (Meross changes this endpoint periodically — it is the one part
of this project that cannot be tested without a live account), the key can also
be found in:

* the Meross app, under a device's settings → device info, on some versions;
* a Homebridge `config.json`, as `"key"` under the Meross platform block;
* Home Assistant's Meross LAN integration, which stores it per device.

Put it in `config.json` as `"key"`, or export it as `MEROSS_KEY` to keep it out
of the file entirely. Bulbs that need a different key from the rest get their
own `"key"` entry, written automatically.

### 3. Run it

```bash
python3 run.py serve --tls
```

Then open `https://<this machine's IP>:8099` on your phone.

**`--tls` is not optional on a phone.** Browsers only allow microphone access
on a secure origin. `localhost` counts; `http://192.168.1.x` does not. The flag
generates a self-signed certificate on first use (`~/.voicelights/`), so your
phone will warn you once — accept it, and add the page to your home screen
while you're there. It then opens full-screen like an app.

On the machine running the server, plain `http://localhost:8099` works without
`--tls`.

## Using it

* **Hold the mic button and talk.** Releasing submits immediately rather than
  waiting for the speech engine to decide you've stopped — that's most of why
  it feels quicker than a smart speaker. On a laptop, hold the space bar.
* **Always on** listens continuously for `hey lights ...`. Costs battery, and
  recognition runs in the browser, but nothing is sent anywhere until a phrase
  parses to a command.
* **Speak** reads confirmations back. Off by default.
* Cards, sliders and scene chips work without the microphone, and the text box
  works when speech recognition isn't available at all.
* The `?` button lists every word the parser knows.

Speech recognition is the browser's (`SpeechRecognition`). Chrome is the most
reliable; Safari on iOS works but sometimes needs a tap per utterance. Audio is
never recorded or uploaded by this project — only the resulting text is POSTed
to your own machine.

### From the terminal

```bash
python3 run.py say "torch and lamp to 30 percent"
python3 run.py state
```

## What it understands

| | |
|---|---|
| Power | `torch on`, `lamp off`, `lights out`, `kill the lights`, `toggle the mushroom` |
| Targets | any name or nickname, `torch and lamp`, `all`, `everything`, or `it` for whatever you last touched |
| Brightness | `40 percent`, `set the lamp to 40`, `half`, `full`, `lowest` |
| Relative | `brighter`, `a bit dimmer`, `way brighter`, `dim the torch`, `brighter by 30` |
| Colour | 30 names — `red`, `deep blue`, `hot pink`, `turquoise`, `amber`, … |
| White | `warm`, `warm white`, `neutral`, `cool`, `daylight`, `candle` |
| Scenes | `movie mode`, `focus`, `chill`, `reading`, `party`, `goodnight` |
| Chained | `turn on the lamp then set it to 20`, `torch red and lamp blue` |
| Status | `what's on`, `status` |

Nicknames and scenes are yours to edit in `config.json` — see
`config.example.json`. Scenes are plain lists of steps; add one and it appears
as a chip in the UI and as a phrase the parser accepts, with no code changes.

## When something doesn't work

**"rejected the device key"** — the key is wrong. `run.py key` again, or read
it from one of the places listed above. `discover` already tried the empty key,
so this means your bulbs genuinely want the account one.

**"I don't know a light called ..."** — that name isn't in `config.json`. The
reply lists the names it does have; add nicknames under `aliases`.

**Microphone button does nothing / permission refused** — you're on `http://`
from a phone. Restart with `--tls`.

**A bulb says offline** — it's powered down at the wall, or its IP changed.
`run.py discover` again.

**Colours work but `warm white` looks cold** (or the reverse) — some firmware
maps the white scale backwards. Set `"invert_temperature": true` in
`config.json`. These three bulbs are already running two different firmware
versions (7.2.7 and 7.2.23), so this is worth knowing about.

**It mishears a phrase consistently** — add it to `tests/test_intents.py`, watch
it fail, then extend the grammar. The parser is a plain table-driven grammar in
`voicelights/intents.py`; there is no model to retrain.

## How this is tested

The honest summary: **everything except the physical radio is tested, and none
of it has been tested against your actual bulbs.**

* `tools/mock_bulb.py` implements the same wire protocol as an MSL120 —
  signature checking, capacity masks, error codes. The end-to-end tests drive
  real HTTP requests against it, so signing, transport, parsing, scenes,
  concurrency and failure handling are all exercised for real.
* The suite runs the full end-to-end set twice: once against normal firmware
  and once against firmware that rejects combined capacity masks, because these
  bulbs are on different firmware versions.
* `tools/mutate.py` checks that the grammar tests can actually fail, by breaking
  the parser 21 different ways and confirming each break is caught. Two tests
  originally passed against a broken parser; both were fixed rather than kept.

```bash
python3 -m unittest discover -s tests    # 88 tests
python3 tools/mutate.py                  # 21/21 mutants killed
```

What that does **not** cover: whether real MSL120 firmware accepts these exact
payloads, whether the Meross cloud sign-in still works, and whether the white
temperature scale runs the direction assumed here. Those need the real bulbs,
and `run.py demo` plus `run.py state` are the fastest way to find out.

## Layout

```
run.py                  entry point: discover, key, serve, say, state, demo
voicelights/
  meross.py             signed local protocol for Meross devices
  discovery.py          finds bulbs on the LAN, and which key they accept
  cloud.py              one-time device-key fetch (the only cloud call)
  intents.py            speech -> commands. No model, just a grammar
  controller.py         runs commands against bulbs, in parallel
  server.py             stdlib HTTP + server-sent events
  tls.py                self-signed cert so phones can use the microphone
  web/                  the interface
tools/
  mock_bulb.py          a fake MSL120 that speaks the real protocol
  mutate.py             proves the grammar tests can fail
tests/
```

Nothing outside the standard library is used at runtime.
