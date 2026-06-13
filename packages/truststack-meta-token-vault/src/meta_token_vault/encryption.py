"""Encryptors for token secret material at rest.

The :class:`Encryptor` protocol abstracts how token *values* are protected when
stored. :class:`NoopEncryptor` is the default and performs no encryption -- it is
**DEV ONLY** and must not be used in production. :class:`FernetEncryptor` provides
real symmetric encryption via the optional ``cryptography`` dependency (install
the ``fernet`` extra).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cryptography.fernet import Fernet


@runtime_checkable
class Encryptor(Protocol):
    """Symmetric encryptor for token secret material."""

    def encrypt(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` and return a string-safe ciphertext."""
        ...

    def decrypt(self, ciphertext: str) -> str:
        """Reverse :meth:`encrypt`, returning the original plaintext."""
        ...


class NoopEncryptor:
    """Pass-through encryptor that stores secrets in cleartext.

    .. warning::
       **DEV ONLY.** This performs no encryption whatsoever. Use
       :class:`FernetEncryptor` (or a cloud KMS-backed encryptor) in any
       environment that handles real credentials.
    """

    def encrypt(self, plaintext: str) -> str:
        return plaintext

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext


class FernetEncryptor:
    """Symmetric authenticated encryption using ``cryptography``'s Fernet.

    The ``cryptography`` package is imported lazily so the vault remains
    installable without it; install the ``fernet`` extra to use this class.
    """

    def __init__(self, key: str | bytes) -> None:
        # Lazy import keeps cryptography optional.
        from cryptography.fernet import Fernet

        self._fernet: Fernet = Fernet(key if isinstance(key, bytes) else key.encode("utf-8"))

    @staticmethod
    def generate_key() -> str:
        """Generate a new URL-safe base64 Fernet key as a string."""
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode("utf-8")

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


__all__ = ["Encryptor", "FernetEncryptor", "NoopEncryptor"]
