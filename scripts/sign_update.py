"""Create an Ed25519-signed update envelope for an already-built artifact."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-env", default="UNDOMESH_UPDATE_SIGNING_KEY")
    args = parser.parse_args()
    encoded = os.getenv(args.key_env, "")
    if not encoded:
        raise SystemExit(f"Signing key is missing from {args.key_env}")
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded))
    artifact = args.artifact.read_bytes()
    payload = {
        "version": args.version,
        "filename": args.artifact.name,
        "size": len(artifact),
        "sha256": hashlib.sha256(artifact).hexdigest(),
    }
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = {"payload": payload, "signature": base64.b64encode(private.sign(wire)).decode()}
    args.output.write_text(json.dumps(envelope, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
