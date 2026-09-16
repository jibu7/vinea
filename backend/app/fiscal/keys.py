"""The three device keys, and how they are kept.

An EBM device holds a CMC key, an internal key and a signing key. They exist because the
authority's own protocol needs them on every signed call, so the question is not whether to
store them but how. The answer, in three parts:

1. **Encrypted at rest** — Fernet under `Settings.fiscal_key_secret`, which production
   refuses to boot without. A published dev default is used in development and in the suite,
   deliberately, so the encryption path is the one that runs everywhere rather than a branch
   nobody exercises.
2. **Never returned by an endpoint.** No schema in `app/schemas/fiscal.py` carries them, and
   `tests/fiscal/test_key_redaction.py` walks the device serializer to prove it.
3. **Stripped from everything stored or logged** — `app.fiscal.rwanda.adapter.redact` runs
   over every payload and response before it reaches `fiscal_outbox` or `fiscal_receipts`,
   and the redaction test greps every row a tape produced for the key strings.

`decrypt_key` is deliberately narrow: one key at a time, at the moment of use, by a caller
that has a reason. There is no `device.keys` property, because a property would be read by
something eventually.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

#: What a redacted key looks like wherever one has been removed.
REDACTED = "***"


class FiscalKeyError(Exception):
    """The stored ciphertext could not be read under the configured secret.

    Almost always one thing: `FISCAL_KEY_SECRET` changed, or a database was restored into an
    environment holding a different one. Said plainly, because the recovery is to re-initialize
    the device against the authority — the keys are the authority's to reissue, and guessing
    is not among the options.
    """


def _cipher() -> Fernet:
    return Fernet(settings.fiscal_key_secret.encode())


def encrypt_key(value: str | None) -> str | None:
    """Ciphertext for storage. `None` in, `None` out — a device that has not initialized has
    no keys, and a ciphertext of an empty string would be indistinguishable from one."""
    if value is None or value == "":
        return None
    return _cipher().encrypt(value.encode()).decode()


def decrypt_key(ciphertext: str | None) -> str | None:
    """Plaintext, at the moment of use, for one key."""
    if ciphertext is None or ciphertext == "":
        return None
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as invalid:
        raise FiscalKeyError(
            "a device key could not be decrypted under the configured FISCAL_KEY_SECRET. "
            "Re-initialize the device against the revenue authority rather than editing the "
            "stored value: the keys are the authority's to reissue."
        ) from invalid


def key_material(*ciphertexts: str | None) -> tuple[str | None, ...]:
    """Several keys at once, for the one caller that legitimately needs all three (device
    initialization writing them). Still a function call rather than an attribute, so that
    reading them is always a thing somebody wrote."""
    return tuple(decrypt_key(ciphertext) for ciphertext in ciphertexts)
