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


import json
import os
import base64
import hashlib
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import unpad
import traceback
from source.dal.dbconnector import postgres_service_connection
from source.Utils import constants as const

class Decryption:
    def __init__(self, salt, iv):
        self.salt = salt.encode()
        self.iv = iv.encode()

    def get_password(self, password):
        try:
            key = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, 65536, dklen=32)
            return key
        except Exception as e:
            print(f"Error generating password key: {e}")
            traceback.print_exc()
            return (f"Error generating password key: {e}")


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
                print(f"Error during decryption:{e}")
                traceback.print_exc()
                raise Exception("error during decryption")

            try:
                decrypted_text = unpad(decrypted_padded_text, AES.block_size).decode()
                return decrypted_text
            except ValueError as e:
                print(f"Error during unpadding: {e}")
                traceback.print_exc()
                raise Exception (f"Error during unpadding: {e}")
        except Exception as e:
            print(f"Error during decryption: {e}")
            traceback.print_exc()
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

            iv = get_random_bytes(12)
            cipher = AES.new(key, AES.MODE_GCM, nonce=iv, mac_len=16)
            cipher_text, tag = cipher.encrypt_and_digest(plain_text.encode("utf-8"))
            encrypted_data_with_iv = iv + cipher_text + tag
            return base64.b64encode(encrypted_data_with_iv).decode("utf-8")
        except Exception as e:
            print(f"Error during v2 encryption: {e}")
            traceback.print_exc()
            raise Exception(f"Error during v2 encryption: {e}")

    def decrypt_v2(self, encrypted_connection, password):
        try:
            key = self.get_password(password)
            if not key:
                return None

            encrypted_data_with_iv = base64.b64decode(encrypted_connection)
            iv_length_bytes = 12
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
            print(f"Error during v2 decryption: {e}")
            traceback.print_exc()
            raise Exception(f"Error during v2 decryption: {e}")


    def fetch_eagleview_credentials(self):
        try:
            connection = postgres_service_connection()
            cursor = connection.cursor()
            cursor.execute("SELECT data FROM service_configuration WHERE key = %s", ("EagleView.credential",))
            result = cursor.fetchone()

            if result and result[0]:
                encrypted_blob = result[0]

                decrypted_json = self.decrypt(encrypted_blob, "EagleView.credential")
                try:
                    data_dict = json.loads(decrypted_json)
                except json.JSONDecodeError as e:
                    print(f"Error decoding decrypted JSON: {e}")
                    traceback.print_exc()
                    return f"Error decoding decrypted JSON: {e}"

                clientid = data_dict.get(const.KEY_CLIENT_ID)
                clientsecret = data_dict.get('ClientKey')

                if not clientid:
                    raise ValueError("ClientId not found in the decrypted JSON.")

                if not clientsecret:
                    raise ValueError("ClientKey not found in the decrypted JSON.")

                if clientid and clientsecret:
                    return clientid, clientsecret
                else:
                    print("ClientId or ClientKey not found in the decrypted JSON.")
                    return "ClientId or ClientKey not found in the decrypted JSON."

            else:
                print("No result found for 'Eagleview' in the database.")
                return "No result found for 'Eagleview' in the database."

        except Exception as e:
            print(f"Error fetching or decrypting data: {e}")
            traceback.print_exc()
            raise

    def fetch_quickbooks_credentials(self, tenant_id: str, service_id: str):
        try:
            connection = postgres_service_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT data
                FROM service_configuration
                WHERE service_id = %s
                  AND key = %s
                  AND invalid = false
                LIMIT 1
                """,
                (service_id, "QuickBooks"),
            )

            result = cursor.fetchone()

            if not result or not result[0]:
                raise Exception("No result found for 'QuickBooks' in the database.")

            encrypted_blob = result[0]

            if self._is_quickbooks_v2_enabled():
                decrypted_json = self.decrypt_v2(encrypted_blob, "QuickBooks")
            else:
                decrypted_json = self.decrypt(encrypted_blob, "QuickBooks")

            try:
                data_dict = json.loads(decrypted_json)
            except json.JSONDecodeError as e:
                print(f"Error decoding decrypted JSON: {e}")
                traceback.print_exc()
                raise Exception(f"Error decoding decrypted JSON: {e}")

            # validate required fields early
            if not data_dict.get(const.KEY_CLIENT_ID):
                raise ValueError("ClientId not found in decrypted JSON.")
            if not data_dict.get(const.KEY_CLIENT_SECRET) and not data_dict.get("ClientKey"):
                raise ValueError("ClientSecret not found in decrypted JSON.")

            return data_dict

        except Exception as e:
            print(f"Error fetching or decrypting QuickBooks data: {e}")
            traceback.print_exc()
            raise\
                