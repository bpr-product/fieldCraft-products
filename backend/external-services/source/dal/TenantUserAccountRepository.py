import traceback
from typing import Any, Dict, Optional

from source.dal.dbconnector import postgres_connection


class TenantUserAccountRepository:
    def __init__(self, tenant_db: str):
        # tenant_db is the DB name to connect to (you are currently using tenant_id as dbname)
        self.tenant_db = (tenant_db or "").strip()

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = postgres_connection(self.tenant_db)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, object_id, email, mobile_no, firstname, lastname, middlename,
                       gender, birthdate, status
                FROM user_account
                WHERE id = %s
                LIMIT 1
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
            if not row:
                return None

            return {
                "id": row[0],
                "objectId": row[1],
                "email": row[2],
                "mobileNo": row[3],
                "firstname": row[4],
                "lastname": row[5],
                "middlename": row[6],
                "gender": row[7],
                "birthdate": row[8],
                "status": row[9],
            }
        except Exception:
            print("[TenantUserAccountRepository.get_user_by_id] ERROR")
            print(traceback.format_exc())
            raise
        finally:
            cur.close()
            conn.close()

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch tenant user_account by email.

        Used for idempotent QuickBooks import when Tenant API returns an error for existing users.
        """
        if not email:
            return None
        sql = """
            SELECT id, firstname, lastname, email, mobile_no AS "mobileNo", object_id AS "objectId"
            FROM user_account
            WHERE lower(email) = lower(%s)
              AND invalid = false
            ORDER BY id DESC
            LIMIT 1
        """
        try:
            conn = postgres_connection(self.tenant_db)
            cur = conn.cursor()
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))
        except Exception:
            print("[TenantUserAccountRepository.get_user_by_email] ERROR")
            traceback.print_exc()
            return None
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    # def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
    #     if not email:
    #         return None
    #
    #     conn = postgres_tenant_connection()
    #     cur = conn.cursor()
    #     try:
    #         cur.execute(
    #             """
    #             SELECT id, object_id, email, mobile_no, firstname, lastname, middlename,
    #                    gender, birthdate, status
    #             FROM user_account
    #             WHERE lower(email) = lower(%s)
    #             ORDER BY id DESC
    #             LIMIT 1
    #             """,
    #             (email.strip(),),
    #         )
    #         row = cur.fetchone()
    #         if not row:
    #             return None
    #
    #         return {
    #             "id": row[0],
    #             "objectId": row[1],
    #             "email": row[2],
    #             "mobileNo": row[3],
    #             "firstname": row[4],
    #             "lastname": row[5],
    #             "middlename": row[6],
    #             "gender": row[7],
    #             "birthdate": row[8],
    #             "status": row[9],
    #         }
    #     except Exception:
    #         print("[TenantUserAccountRepository.get_user_by_email] ERROR")
    #         print(traceback.format_exc())
    #         raise
    #     finally:
    #         cur.close()
    #         conn.close()