"""Command line entry points: discover, key, serve, say, state."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import time

from . import cloud, config as config_mod, discovery
from .controller import Controller
from .meross import Bulb, MerossError


def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    text = re.sub(r"_(light|lamp|bulb)$", "", text) or text
    return text or "bulb"


def load_controller(args) -> Controller:
    conf = config_mod.load(getattr(args, "config", None))
    return Controller(conf)


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------

def cmd_discover(args) -> int:
    try:
        conf = config_mod.load(args.config)
    except config_mod.ConfigError:
        conf = dict(config_mod.DEFAULTS)
        conf["_path"] = args.config or str(config_mod.DEFAULT_PATHS[0])

    # Try what we already have, then the empty key. Bulbs provisioned through
    # HomeKit rather than the Meross app frequently accept an empty key, so a
    # cloud login is a last resort rather than step one.
    candidates = [k for k in [args.key, conf.get("key")] if k]
    candidates.append("")

    hosts = args.hosts.split(",") if args.hosts else None
    print("Scanning the local network...")
    started = time.perf_counter()
    found = discovery.scan(hosts, keys=candidates, progress=lambda m: print(f"  {m}"))
    elapsed = time.perf_counter() - started

    # Only ask for credentials if a device was actually found and rejected
    # every key we had.
    if found and not any(f.key_ok for f in found) and not args.no_cloud:
        print(f"\nFound {len(found)} Meross device(s), but none accepted a key.")
        print("Sign in to Meross to fetch the right one (or Ctrl-C to skip).")
        try:
            key = prompt_for_key(args)
            print("  Re-probing with the fetched key...")
            found = discovery.scan([f.ip for f in found], keys=[key],
                                   progress=lambda m: print(f"  {m}"))
        except KeyboardInterrupt:
            print("\nSkipped. Bulbs will still be listed, but not controllable yet.")

    working = {f.key for f in found if f.key_ok}
    if working:
        # One key normally covers every bulb on an account.
        conf["key"] = sorted(working, key=len)[-1]
        if len(working) > 1:
            print("\nNote: these bulbs use different device keys; each is stored per-device.")

    if not found:
        print(f"\nNo Meross devices found ({elapsed:.1f}s).")
        print("Check that this machine is on the same Wi-Fi as the bulbs, and that")
        print("the bulbs are powered at the wall. Try --hosts 192.168.1.20,192.168.1.21")
        return 1

    bad_key = [f for f in found if not f.key_ok]
    if bad_key:
        print(f"\n{len(bad_key)} device(s) rejected the device key.")
        print("Found them, but cannot read or control them until the key is right.")
        print("Run `python run.py key` to fetch it, or see README.md for where")
        print("else the key can be found.")
        for f in bad_key:
            print(f"  {f.ip}")

    lights = [f for f in found if f.key_ok]
    print(f"\nFound {len(lights)} controllable device(s) in {elapsed:.1f}s:")
    for f in lights:
        print(f"  {f.ip:16s} {f.model or '?':10s} firmware {f.firmware or '?'}")

    devices = []
    for index, f in enumerate(lights, start=1):
        name = identify(f, f.key, index, conf, skip=args.no_identify)
        device = {
            "id": slug(name),
            "label": name,
            "ip": f.ip,
            "model": f.model,
            "serial": f.mac,
            "aliases": [],
            "channel": 0,
        }
        if f.key != conf.get("key", ""):
            device["key"] = f.key
        devices.append(device)

    conf["devices"] = devices
    path = config_mod.save(conf, args.config)
    print(f"\nWrote {path}")
    print("Edit it to add nicknames, then run:  python run.py serve --tls")
    return 0


def identify(found, key: str, index: int, conf: dict, skip: bool = False) -> str:
    """Blink a bulb so you can tell which physical light it is."""
    default = f"Bulb {index}"
    if skip or not sys.stdin.isatty():
        return default

    bulb = Bulb(id=f"tmp{index}", label=default, ip=found.ip, key=key,
                invert_temperature=bool(conf.get("invert_temperature")))
    print(f"\n  Blinking {found.ip} three times -- watch the studio.")
    try:
        bulb.refresh()
        was_on = bool(bulb.state.on)
        for _ in range(3):
            bulb.set_power(True)
            time.sleep(0.45)
            bulb.set_power(False)
            time.sleep(0.35)
        bulb.set_power(was_on)
    except MerossError as exc:
        print(f"  (couldn't blink it: {exc})")

    answer = input(f"  Which light was that? [{default}] ").strip()
    return answer or default


def prompt_for_key(args) -> str:
    email = args.email or input("  Meross email: ").strip()
    password = getpass.getpass("  Meross password: ")
    region = args.region or input("  Region [us/eu/ap, default us]: ").strip() or "us"
    try:
        key, devices = cloud.fetch(email, password, region)
    except cloud.CloudError as exc:
        print(f"  {exc}")
        mfa = input("  MFA code (blank to give up): ").strip()
        if not mfa:
            raise KeyboardInterrupt from None
        key, devices = cloud.fetch(email, password, region, mfa)
    print(f"  Got device key ({len(key)} chars).")
    for device in devices:
        print(f"    {device['name']}  {device['model']}  {device['mac']}")
    return key


# --------------------------------------------------------------------------
# key
# --------------------------------------------------------------------------

def cmd_key(args) -> int:
    try:
        conf = config_mod.load(args.config)
    except config_mod.ConfigError:
        conf = dict(config_mod.DEFAULTS)
    try:
        key = prompt_for_key(args)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1
    except cloud.CloudError as exc:
        print(f"\n{exc}")
        print("\nSee README.md for how to read the key out of the Meross app instead.")
        return 1
    conf["key"] = key
    path = config_mod.save(conf, args.config)
    print(f"Saved the device key to {path}")
    return 0


# --------------------------------------------------------------------------
# serve / say / state
# --------------------------------------------------------------------------

def cmd_serve(args) -> int:
    from . import server as server_mod

    controller = load_controller(args)
    certfile = keyfile = None
    if args.tls:
        from .tls import TlsError, ensure_cert

        lan_ip = None
        try:
            lan_ip = str(discovery.local_subnet().network_address)
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            lan_ip = sock.getsockname()[0]
            sock.close()
        except OSError:
            pass
        try:
            certfile, keyfile = ensure_cert(lan_ip)
        except TlsError as exc:
            print(f"{exc}")
            return 1

    print("Reading current bulb state...")
    for device in controller.refresh_all():
        state = device["state"]
        status = (
            f"{device['label']}: "
            + ("offline" if not state["online"] else "on" if state["on"] else "off")
            + (f" {state['luminance']}%" if state["online"] and state["on"] else "")
        )
        print(f"  {status}")

    server_mod.run(controller, args.host, args.port, certfile, keyfile)
    return 0


def cmd_demo(args) -> int:
    """Run the whole thing against three simulated bulbs.

    Useful for trying the voice grammar and the web UI before sorting out the
    device key, and for developing on a laptop that is nowhere near the studio.
    """
    import pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
    from mock_bulb import FakeBulb, serve  # noqa: E402

    from . import server as server_mod

    key = "demokey"
    devices = []
    for name in ("torch", "lamp", "mushroom"):
        bulb = FakeBulb(name, key)
        srv = serve(bulb, 0, quiet=True)
        devices.append(
            {"id": name, "label": f"{name.title()} Light",
             "ip": f"127.0.0.1:{srv.server_address[1]}"}
        )
        print(f"  simulated {name} on {devices[-1]['ip']}")

    controller = Controller(
        {"key": key, "devices": devices, "scenes": config_mod.DEFAULT_SCENES,
         "wake_words": ["lights"], "timeout": 3.0}
    )
    print("\n  Demo mode: no real bulbs are touched.")
    server_mod.run(controller, args.host, args.port)
    return 0


def cmd_say(args) -> int:
    controller = load_controller(args)
    result = controller.say(" ".join(args.text))
    print(result["reply"])
    if args.json:
        print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def cmd_state(args) -> int:
    controller = load_controller(args)
    for device in controller.refresh_all():
        state = device["state"]
        if not state["online"]:
            print(f"{device['label']:18s} offline  ({state['error'] or 'no reply'})")
            continue
        detail = ""
        if state["on"]:
            detail = f"{state['luminance']}%"
            if state["hex"]:
                detail += f" {state['hex']}"
            elif state["temperature"]:
                detail += f" white {state['temperature']}"
        print(f"{device['label']:18s} {'on ' if state['on'] else 'off'} {detail}")
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="run.py", description="Voice control for Meross Wi-Fi bulbs, over the LAN."
    )
    ap.add_argument("-c", "--config", help="path to config.json")
    subs = ap.add_subparsers(dest="command", required=True)

    disc = subs.add_parser("discover", help="find bulbs and write config.json")
    disc.add_argument("--key", help="Meross device key")
    disc.add_argument("--hosts", help="comma-separated IPs instead of scanning")
    disc.add_argument("--no-identify", action="store_true", help="skip the blink test")
    disc.add_argument("--no-cloud", action="store_true", help="never ask for a login")
    disc.add_argument("--email")
    disc.add_argument("--region", help="us, eu or ap")
    disc.set_defaults(func=cmd_discover)

    key = subs.add_parser("key", help="fetch the device key from your Meross account")
    key.add_argument("--email")
    key.add_argument("--region", help="us, eu or ap")
    key.set_defaults(func=cmd_key)

    serve = subs.add_parser("serve", help="run the voice web app")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8099)
    serve.add_argument("--tls", action="store_true",
                       help="serve over https so phones can use the microphone")
    serve.set_defaults(func=cmd_serve)

    say = subs.add_parser("say", help="run one command from the terminal")
    say.add_argument("text", nargs="+")
    say.add_argument("--json", action="store_true")
    say.set_defaults(func=cmd_say)

    state = subs.add_parser("state", help="print what the bulbs are doing")
    state.set_defaults(func=cmd_state)

    demo = subs.add_parser("demo", help="run the UI against three simulated bulbs")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8099)
    demo.set_defaults(func=cmd_demo)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config_mod.ConfigError as exc:
        print(f"{exc}")
        return 1
    except KeyboardInterrupt:
        return 130
