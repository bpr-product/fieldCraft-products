##----------------------------------------------------------------------------
## Description : Utils Layer
##----------------------------------------------------------------------------
## Author: Bala Pradeep R
## Copyright: FieldCraft
## License: {license}
## Version: 1.0
##----------------------------------------------------------------------------
## History
## createdDate: 27-03-2025
##----------------------------------------------------------------------------
## Revision History
## -----------------------------------------------------------------------------
## Requirement/
## Issue/WorkItem |    DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
## -----------------------------------------------------------------------------
##  Feature-2745  | 26-03-2025 | BALA PRADEEP R  |   Decryption Utils service


import os
import base64
import hashlib
import logging
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import unpad

log = logging.getLogger(__name__)


class Decryption:
    def __init__(self, salt, iv):
        self.salt = salt.encode()
        self.iv = iv.encode()

    def get_password(self, password):
        try:
            key = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, 65536, dklen=32)
            return key
        except Exception as e:
            log.error("Error generating password key: %s", e, exc_info=True)
            # Return None (not a truthy error string) so the callers' `if not
            # key: return None` guard fires, instead of a str being passed into
            # AES.new() and raising a cryptic "AES key must be 16/24/32 bytes".
            return None


    def decrypt(self, encrypted_connection, password):
        try:
            key = self.get_password(password)
            if not key:
                return None
            try:
                cipher = AES.new(key, AES.MODE_CBC, self.iv)
                cipher_bytes = base64.b64decode(encrypted_connection)
                decrypted_padded_text = cipher.decrypt(cipher_bytes)
            except Exception as e:
                log.error("Error during decryption: %s", e, exc_info=True)
                raise RuntimeError("error during decryption") from e

            try:
                decrypted_text = unpad(decrypted_padded_text, AES.block_size).decode()
                return decrypted_text
            except ValueError as e:
                log.warning("Error during unpadding: %s", e, exc_info=True)
                raise ValueError(f"Error during unpadding: {e}") from e
        except Exception as e:
            log.error("Error during decryption: %s", e, exc_info=True)
            return f"Error during decryption: {e}"


    def _is_quickbooks_v2_enabled(self):
        try:
            raw = (os.getenv("ENCRYPTION_DECRYPTION_V2", "false") or "").strip().lower()
            return raw in ("1", "true", "yes", "on")
        except Exception:
            return False

    def encrypt_v2(self, plain_text, password):
        try:
            key = self.get_password(password)
            if not key:
                return None

            iv = get_random_bytes(16)
            cipher = AES.new(key, AES.MODE_GCM, nonce=iv, mac_len=16)
            cipher_text, tag = cipher.encrypt_and_digest(plain_text.encode("utf-8"))
            encrypted_data_with_iv = iv + cipher_text + tag
            return base64.b64encode(encrypted_data_with_iv).decode("utf-8")
        except Exception as e:
            log.error("Error during v2 encryption: %s", e, exc_info=True)
            raise RuntimeError(f"Error during v2 encryption: {e}") from e

    def decrypt_v2(self, encrypted_connection, password):
        try:
            key = self.get_password(password)
            if not key:
                return None

            encrypted_data_with_iv = base64.b64decode(encrypted_connection)
            iv_length_bytes = 16
            gcm_tag_length_bytes = 16

            if len(encrypted_data_with_iv) < (iv_length_bytes + gcm_tag_length_bytes):
                raise ValueError("Invalid encrypted payload. Too short to contain IV and GCM tag.")

            iv = encrypted_data_with_iv[:iv_length_bytes]
            ciphertext_with_tag = encrypted_data_with_iv[iv_length_bytes:]
            cipher_text = ciphertext_with_tag[:-gcm_tag_length_bytes]
            tag = ciphertext_with_tag[-gcm_tag_length_bytes:]

            cipher = AES.new(key, AES.MODE_GCM, nonce=iv, mac_len=gcm_tag_length_bytes)
            plain_text = cipher.decrypt_and_verify(cipher_text, tag)
            return plain_text.decode("utf-8")
        except Exception as e:
            log.error("Error during v2 decryption: %s", e, exc_info=True)
            raise RuntimeError(f"Error during v2 decryption: {e}") from e
