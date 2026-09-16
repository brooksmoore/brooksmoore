"""Self-signed certificate helper.

Browsers only hand out the microphone on a secure origin. localhost counts;
http://192.168.x.x from a phone does not. So serving over HTTPS with a
self-signed certificate is the difference between this working on your phone
and not. You accept the browser warning once per device.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

DEFAULT_DIR = pathlib.Path.home() / ".voicelights"


class TlsError(RuntimeError):
    pass


def ensure_cert(
    lan_ip: str | None = None, directory: pathlib.Path | None = None, days: int = 3650
) -> tuple[str, str]:
    """Return (certfile, keyfile), generating them on first use."""
    directory = directory or DEFAULT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    certfile = directory / "server.crt"
    keyfile = directory / "server.key"
    if certfile.exists() and keyfile.exists():
        return str(certfile), str(keyfile)

    if not shutil.which("openssl"):
        raise TlsError(
            "openssl is not on PATH, so a certificate cannot be generated.\n"
            "Either install openssl, or run without --tls and open the page on "
            "this machine at http://localhost:8099 (localhost is a secure "
            "origin, so the microphone works there)."
        )

    alt = ["DNS:localhost", "IP:127.0.0.1"]
    if lan_ip:
        alt.append(f"IP:{lan_ip}")

    command = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(keyfile), "-out", str(certfile),
        "-days", str(days), "-subj", "/CN=voicelights",
        "-addext", f"subjectAltName={','.join(alt)}",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise TlsError(f"openssl failed:\n{result.stderr.strip()}")
    keyfile.chmod(0o600)
    return str(certfile), str(keyfile)
