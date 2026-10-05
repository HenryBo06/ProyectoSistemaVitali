"""Verify passwords stored by the earlier local SmartOrder installation."""

from __future__ import annotations

from base64 import b64decode, b64encode
from hashlib import pbkdf2_hmac
from hmac import compare_digest

from django.contrib.auth.hashers import BasePasswordHasher, mask_hash


class LegacySmartOrderHasher(BasePasswordHasher):
    algorithm = "smartorder_legacy_pbkdf2_sha256"

    def encode(self, password, salt, iterations=600_000):
        raise NotImplementedError("Only imported legacy hashes use this verifier.")

    def verify(self, password, encoded):
        try:
            algorithm, iterations, salt, digest = encoded.split("$", 3)
            if algorithm != self.algorithm or int(iterations) != 600_000:
                return False
            expected = b64decode(digest, validate=True)
            derived = pbkdf2_hmac(
                "sha256", password.encode("utf-8"),
                b64decode(salt, validate=True), 600_000,
            )
            return compare_digest(derived, expected)
        except (ValueError, TypeError):
            return False

    def safe_summary(self, encoded):
        try:
            _, iterations, salt, digest = encoded.split("$", 3)
        except ValueError:
            return {"algorithm": self.algorithm, "hash": "<invalid>"}
        return {
            "algorithm": self.algorithm,
            "iterations": iterations,
            "salt": mask_hash(salt),
            "hash": mask_hash(digest),
        }


def encode_legacy_hash(salt: bytes, digest: bytes) -> str:
    return "$".join((
        LegacySmartOrderHasher.algorithm, "600000",
        b64encode(salt).decode("ascii"), b64encode(digest).decode("ascii"),
    ))
