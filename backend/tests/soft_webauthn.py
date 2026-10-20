"""Software-beveiligingssleutel voor de tests: doet wat een YubiKey of passkey in de browser
doet (navigator.credentials.create/get), met een P-256-sleutel en attestation "none"."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftKey:
    def __init__(self, origin: str = "http://testserver", rp_id: str = "testserver") -> None:
        self.origin = origin
        self.rp_id = rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin or self.origin, "crossOrigin": False}
        ).encode()

    def create(self, options: dict, origin: str | None = None) -> dict:
        """navigator.credentials.create(), als JSON zoals de VaultX-webinterface het stuurt."""
        assert options["rp"]["id"] == self.rp_id
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        attested = (
            bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + self._cose_key()
        )
        auth_data = rp_hash + bytes([0x41]) + struct.pack(">I", self.counter) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["usb", "nfc"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict, origin: str | None = None, *, bitwarden: bool = False) -> dict:
        """navigator.credentials.get(). Met bitwarden=True in het formaat van de Bitwarden-
        connector (clientDataJson met kleine letters, geen userHandle)."""
        assert options["rpId"] == self.rp_id
        allowed = [c["id"] for c in options["allowCredentials"]]
        assert b64url(self.credential_id) in allowed
        self.counter += 1
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        auth_data = rp_hash + bytes([0x01]) + struct.pack(">I", self.counter)
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        data_key = "clientDataJson" if bitwarden else "clientDataJSON"
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "extensions": {},
            "response": {
                "authenticatorData": b64url(auth_data),
                data_key: b64url(client_data),
                "signature": b64url(signature),
            },
        }
