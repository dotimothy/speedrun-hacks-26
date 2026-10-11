"""Generate a self-signed certificate for serving the demo over HTTPS.

Why this exists: `getUserMedia` only works in a secure context. `localhost`
counts as one, but a LAN address does not -- so the moment you open the
dashboard on a phone or a second laptop at `http://192.168.x.x:5173`, the
microphone button stops working with no useful error. Serving over HTTPS
fixes that, and a local demo has nowhere to get a real certificate from.

The certificate covers `localhost`, the loopback addresses and every LAN IPv4
this machine currently has, so the same files work however you reach it.

    python scripts/make_cert.py             # writes certs/dev.crt and dev.key
    python scripts/make_cert.py --add 10.0.0.5

Browsers will warn: the certificate is signed by nobody. That is expected.
Click through once per device. Do not use these files for anything real --
the private key is sitting unencrypted in the repo directory and is
gitignored for that reason.
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import socket
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CERT_DIR = REPO / "certs"
CERT = CERT_DIR / "dev.crt"
KEY = CERT_DIR / "dev.key"


def local_addresses() -> list[str]:
    found: set[str] = set()
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            found.add(address)
    except Exception:
        pass
    # gethostbyname_ex misses some interfaces; a UDP connect to an off-machine
    # address reveals the one actually used for outbound traffic without
    # sending anything.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", action="append", default=[], help="extra IP or hostname")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--force", action="store_true", help="overwrite an existing cert")
    args = parser.parse_args()

    if CERT.exists() and KEY.exists() and not args.force:
        print(f"{CERT.relative_to(REPO)} already exists. Use --force to regenerate.")
        return 0

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("needs the `cryptography` package: pip install cryptography")
        return 1

    hostnames = ["localhost", socket.gethostname()]
    addresses = ["127.0.0.1", "::1", *local_addresses()]

    for extra in args.add:
        try:
            ipaddress.ip_address(extra)
            addresses.append(extra)
        except ValueError:
            hostnames.append(extra)

    alt_names: list[x509.GeneralName] = [x509.DNSName(h) for h in dict.fromkeys(hostnames)]
    for address in dict.fromkeys(addresses):
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            continue

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "DispatchIQ local demo"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DispatchIQ prototype"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=args.days))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(exist_ok=True)
    KEY.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    print(f"wrote {CERT.relative_to(REPO)} and {KEY.relative_to(REPO)}")
    print("valid for:")
    for name in alt_names:
        print(f"  - {name.value}")
    print("\nStart over HTTPS with:")
    print("  python scripts/serve.py")
    print("\nReach it from another device at:")
    for address in local_addresses():
        print(f"  https://{address}:5173")
    print("\nThe browser will warn about the certificate once per device. That is expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
