#!/usr/bin/env python3
"""Verify one signed status heartbeat and write the exact payload bytes.

The publish workflow used to keep this logic inline. It now delegates here so
the workflow, the local verifier, and their tests share one HMAC contract.
The signature and key are never printed; failures name only the malformed
input, never its value.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import heartbeat_signature


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--payload-env", default="PAYLOAD")
    parser.add_argument("--signature-env", default="SIGNATURE")
    parser.add_argument("--key-env", default="STATUS_HEARTBEAT_HMAC")
    args = parser.parse_args(argv)

    encoded = os.environ.get(args.payload_env, "")
    signature = os.environ.get(args.signature_env, "")
    key = os.environ.get(args.key_env, "")
    try:
        payload = heartbeat_signature.decode_payload(encoded)
        if not key:
            raise heartbeat_signature.SignatureError("HMAC key is not configured")
        heartbeat_signature.verify(payload, signature, key.encode("utf-8"))
    except heartbeat_signature.SignatureError as exc:
        print(f"verify_heartbeat: {exc}", file=sys.stderr)
        return 1

    temporary = args.out.with_name(args.out.name + ".tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(args.out)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        print(f"verify_heartbeat: cannot write {args.out}: {exc}", file=sys.stderr)
        return 1
    print(f"verify_heartbeat: wrote {args.out} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
