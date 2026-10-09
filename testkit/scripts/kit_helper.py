"""Small helpers for the DoMe test kit scripts. Runs inside the pc-agent virtual environment
(qrcode, Pillow and cryptography are agent dependencies), started by the PowerShell scripts:

    python kit_helper.py qr <text> <out.png>
    python kit_helper.py extension <extension-dir> <key.pem>

``extension`` gives the unpacked test build of the browser extension a fixed identity: it creates
(once) an RSA key kept next to the build in testkit/.state, writes the public half into the
build's manifest.json as ``"key"`` and prints the extension ID Chrome and Edge derive from it.
The agent's native-messaging registration must name that exact ID, and with a pinned key the ID
no longer depends on the folder the extension was loaded from.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def extension_id_from_public_der(der: bytes) -> str:
    """Chrome's rule: first 16 bytes of SHA-256(SubjectPublicKeyInfo), hex digits 0-f mapped to a-p."""
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])


def load_or_create_key(path: Path) -> rsa.RSAPrivateKey:
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SystemExit(f"{path} is not an RSA private key; delete it to create a new one")
        return key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return key


def pin_extension_key(extension_dir: Path, key_path: Path) -> str:
    manifest_path = extension_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no manifest.json in {extension_dir}; build the extension first")
    der = load_or_create_key(key_path).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["key"] = base64.b64encode(der).decode("ascii")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return extension_id_from_public_der(der)


def write_qr(text: str, out: Path) -> None:
    import qrcode

    qr = qrcode.QRCode(border=4, box_size=10)
    qr.add_data(text)
    qr.make(fit=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    qr.make_image().save(str(out))


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "qr":
        write_qr(argv[1], Path(argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "extension":
        print(pin_extension_key(Path(argv[1]), Path(argv[2])))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
