##----------------------------------------------------------------------------
## Description : Ealgeview Service Layer 
##----------------------------------------------------------------------------
## Author: Bala Pradeep R
## Copyright: FieldCraft
## License: {license}
## Version: 1.0
##----------------------------------------------------------------------------
## History
## createdDate: 19-03-2025
##----------------------------------------------------------------------------
## Revision History
## -----------------------------------------------------------------------------
## Requirement/
## Issue/WorkItem |    DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
## -----------------------------------------------------------------------------
##  Feature-2745  | 19-03-2025 | BALA PRADEEP R  | Eagleview Service
##  Feature-2745  | 26-03-2025 | BALA PRADEEP R  | Insert oder data in postgres





import logging
import requests
from requests.auth import HTTPBasicAuth
import json
import uuid
from datetime import datetime
import os
import psycopg2
import traceback
from source.dal.dbconnector import postgres_connection
from source.dal.dbconnector import postgres_customer_connection , postgres_service_connection
from dotenv import load_dotenv
from source.Utils import DecryptionUtil
import sys
import socket
from azure.storage.blob import BlobServiceClient, ContentSettings
import hashlib
from fastapi import HTTPException
from source.Utils import constants as c

sys.path.append("source/.")
load_dotenv()

class EagleviewService:
    def __init__(self):
        self.salt =  os.getenv("SALT")
        self.iv  = os.getenv("IV")
        self.eagleview_PlaceOrder_url = os.getenv("Ealgeview_PlaceOrder_url")
        self.eagleview_Auth_url = os.getenv("Ealgeview_Auth_url")
        self.eagleview_CancelOrder_url = os.getenv("Ealgeview_CancelOrder_url")
        self.ContainerName = os.getenv("CONTAINER_NAME")
        self.eagleview_GetAvaiableProducts = os.getenv("Eagleview_GetAvaiableProducts_url")
        self.eagleview_GetAccountDetails = os.getenv("Eagleview_GetAccountDetails_url")
        self.eagleview_deployment = os.getenv("Ealgeview_Deployment")
        self.token = None

    def authenticate(self): # Ealgeview authentication using ClientId and ClientKey
        try:
            print("Getting Access Token to authenticate with EagleView API")
            url = self.eagleview_Auth_url
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            data = {
                "grant_type": "client_credentials"
            }

            try:
                decryption = DecryptionUtil.Decryption(self.salt, self.iv)
                credentials = decryption.fetch_eagleview_credentials()
                
                ClientId, ClientKey = credentials

                response = requests.post(url, headers=headers, data=data, auth=HTTPBasicAuth(username=ClientId, password=ClientKey))
                logging.info("EagleView authentication response status: %s", response.status_code)
                
                if response.status_code == 200:
                    self.token = response.json().get("access_token", "")
                    print("Token is successfully generated")
                    logging.info("Authentication successful")
                    return self.token
                else:
                    error_info = response.json()
                    print("error_info",error_info)
                    error_code = error_info.get("errorCode", "UnknownError")
                    error_message = error_info.get("errorSummary", "Unknown error occurred during authentication.")
        
                    full_error = f"{error_code}: {error_message}"
                    logging.error(full_error)
                    raise Exception (full_error)
            except requests.exceptions.RequestException as e:
                logging.error(f"An error occurred during authentication: {e}")
                traceback.print_exc()
                raise Exception (f"{e}")
        except Exception as e:
            logging.error(f"An error occurred during authentication: {e}")
            traceback.print_exc()
            raise Exception (f"{e}")
        
    def audit_log(self ,tenant_id, user_id, event): # Insert audit log into PostgreSQL tenant database
        try:
            connection_tenant = postgres_connection(tenant_id)
            cursor_tenant = connection_tenant.cursor()
            update_query = """
                INSERT INTO audit_log
                (created_date, ip_address, user_id, event, event_type, log_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            data = {
                "created_at": datetime.utcnow().isoformat(),
                "ip_address": socket.gethostbyname(socket.gethostname()),
                "user_id": user_id,
                "event": event,
                "event_type": "Eagleview Service",
                "log_id": str(uuid.uuid4())
            }

            cursor_tenant.execute(update_query, (
                data["created_date"],
                data["ip_address"],
                data["user_id"],
                data["event"],
                data["event_type"],
                data["log_id"]
            ))

            connection_tenant.commit()
            cursor_tenant.close()
            connection_tenant.close()
            
        except Exception as e:
            print(f"[audit_log] failed for tenant_id={tenant_id}: {e}")
            pass
        

    def place_measurement_order(self, request_uid, user_id ,tenant_id, asset_id,
                                measurement_type, delivery_type, measurement_structure_type, is_modified):  # Place an order using EagleView API
        try:
            customer_connection = postgres_customer_connection()
            cursor_customer = customer_connection.cursor()
            cursor_customer.execute(
                """SELECT address, city, state, zip_code, country
                FROM asset 
                WHERE id = %s;""", (asset_id,))
            
            row = cursor_customer.fetchone()
            Address, City, State, Zip, Country = row

            print(Address)
            print(City)
            print(State)
            print(Zip)
            print(Country)

            data = self.get_eagleview_json_from_blob("EagleviewConfig.json")
            AddressType = data.get("AddressType")

            token = EagleviewService.authenticate(self)
            print("Checking the arguments are passed in the EagleView Service")
            if not token:
                logging.error("Token is missing. Please authenticate first.")
                return {"error": "Token is missing. Authentication is required."}
            print("All the arguments are passed Successfully in the EagleView Service")
            url = self.eagleview_PlaceOrder_url
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}"
            }

            if self.eagleview_deployment == "sandbox":
                body = {
                    "OrderReports": {
                        "ReportAddresses": {
                            "Address": Address,
                            "City": City,
                            "State": State,
                            "Zip": Zip,
                            "AddressType": AddressType,
                            "Country": Country,
                            "PrimaryProductId": measurement_type,
                            "DeliveryProductId": delivery_type,
                            "MeasurementInstructionType": measurement_structure_type,
                            "ChangesInLast4Years": is_modified
                        }
                    }
                }
            else:
                body = {
                "OrderReports": [
                    {
                        "ReportAddresses": [
                            {
                                "Address": Address,
                                "City": City,
                                "State": State,
                                "Zip": Zip,
                                "Country": Country,
                                "AddressType": AddressType,
                                "VerifierUsedId": None,
                                "MapperUsedId": None,
                                "VerificationResultTypeId": None
                            }
                        ],
                        "ReportAttibutes": None,
                        "BuildingId": None,
                        "PrimaryProductId": measurement_type,
                        "DeliveryProductId": delivery_type,
                        "AddOnProductIds": [],
                        "MeasurementInstructionType": measurement_structure_type,
                        "ClaimNumber": None,
                        "ClaimInfo": None,
                        "BatchId": None,
                        "CatId": None,
                        "ChangesInLast4Years": is_modified,
                        "PONumber": None,
                        "Comments": None,
                        "ReferenceID": None,
                        "InsuredName": None,
                        "UpgradeFromReportId": None,
                        "PolicyNumber": None
                    }
                ],
                "PromoCode": None,
                "PlaceOrderUser": None,
                "CreditCardData": None
            }
            try:
                print("Placing the order using the EagleView Service")
                response = requests.post(url, headers=headers, json=body)

                if response.status_code == 200:
                    print("status",response.status_code)
                    logging.info(f"Response: {response.json()}")
                    print(response.json())
                    order_id = str(response.json().get("OrderId"))
                    print("order_id", order_id)
                    report_ids = response.json().get("ReportIds", [])
                    print("report_ids",report_ids)
                    data = {
                        "OrderId": order_id,
                        "ReportIds": report_ids
                    }
                    request = json.dumps(data)
                    orderType_data = {
                        "order_type":measurement_type
                    }
                    order_type = json.dumps(orderType_data)

                    self.audit_log(tenant_id, user_id, f"Order placed successfully for OrderId {order_id}.") # Audit log for order placement
                    report_ids_str = [str(report_id) for report_id in report_ids]
                    asset_details = {
                        "Address": Address,
                        "City": City,
                        "State": State,
                        "Country": Country,
                        "ZipCode": Zip
                    }

                    asset_data = json.dumps(asset_details)

                    if order_id and report_ids_str:
                    
                        order_data = {
                            "tenant_id": tenant_id,
                            "property_address": asset_data,
                            "request": request,
                            "order_type": order_type,
                            "created_at": datetime.utcnow().isoformat(),
                            "updated_at": datetime.utcnow().isoformat()
                        }

                        try:
                            self.updateOrder_response(request_uid ,order_data,measurement_type)
                            return {
                                "status": "success",
                                "message":f"Order {order_id} placed successfully using EagleView for tenant {tenant_id}",
                                "data": [data]
                            }
                        except Exception as e:
                            logging.error(f"Failed to insert order {order_id} and report {report_ids_str} into Postgres Service Database for tenant {tenant_id}: {e}")
                            traceback.print_exc()
                            self.audit_log(tenant_id, user_id, f"Failed to insert order {order_id} and reportId {report_ids_str} into Postgres Service Database for tenant {tenant_id}: {e}")
                            return {
                                "status": "success",
                                "message":f"Order {order_id} placed successfully using EagleView for tenant {tenant_id} but the data is not inserted into POstgres Service Databse",
                                "data": [data]
                            }


                    else:
                        logging.error(f"OrderId and ReportIds are not found in the respoonse for the requestId {request_uid}.")
                        self.audit_log(tenant_id, user_id, f"Order placed successfully but no orderId and reportId found for requestId {request_uid}.")
                        return {"status": "Success", "message": "Order placed successfully but no orderId and reportId found."}
                elif response.status_code == 400:
                    print("status",response.status_code)
                    error_message = response.json()
                    self.audit_log(tenant_id, user_id, f"Invalid request: {error_message}")
                    return {"status":"error",
                            "message": error_message,
                            "data": []}
                elif response.status_code == 401:
                    print("status",response.status_code)
                    error_message = response.json()
                    self.audit_log(tenant_id, user_id, f"Authentication error: {error_message}")
                    return {"status":"error",
                            "message": error_message,
                            "data": []}
                elif response.status_code == 500:
                    print("status",response.status_code)
                    self.audit_log(tenant_id, user_id, f"Internal server error.")
                    return {"status":"error",
                            "message": "Internal Server Error",
                            "data": []}
                else:
                    self.audit_log(tenant_id, user_id, f"Status Code: {response.status_code}")
                    return {"status":"error",
                            "message": f"Status Code: {response.status_code}",
                            "data": []}
            except Exception as e:
                traceback.print_exc()
                return {"status":"error",
                            "message": f"{e}",
                            "data": []}
        except Exception as e:
            traceback.print_exc()
            return {"status":"error",
                            "message": f"{e}",
                            "data": []}
        
        
    def get_Status(self, report_refs, tenant_id, user_id):  # Get the status of an order using Eagleview API and get the report once the order is completed
            try:
                response_handler = {"status": "success", "message": "", "data": []}
                BearerToken = self.authenticate()
                report_base_url = os.getenv('Eagleview_Report_url')

                def order_status(report_id):
                    report_url = f"{report_base_url}?reportId={report_id}"
                    report_headers = {
                        "Accept": "application/json",
                        "Authorization": f"Bearer {BearerToken}"
                    }
                    return requests.get(report_url, headers=report_headers)

                def new_status(report_data):
                    return {
                        "createdAt": datetime.now().isoformat(),
                        "Status": str(report_data.get("Status", {})),
                        "status_id": str(report_data.get("StatusId", {})),
                        "sub_status_id": str(report_data.get("SubStatusId", {})),
                        "invalid": False
                    }

                def status_history(cursor, request_uid, report_id):
                    cursor.execute("""
                        SELECT status_history
                        FROM service_request
                        WHERE request_uid = %s
                        AND (response::jsonb->'ReportIds') @> to_jsonb(ARRAY[%s]::int[]);
                    """, (request_uid, report_id))
                    return cursor.fetchone()

                def update_status(cursor, connection, request_uid, report_id, existing_info, new_status):
                    updated_additional_info = []
                    for entry in existing_info:
                        entry["invalid"] = True
                        updated_additional_info.append(entry)
                    updated_additional_info.append(new_status)
                    updated_additional_info_json = json.dumps(updated_additional_info)

                    logging.info(f"Updated additional_info for OrderUid {request_uid} and ReportId {report_id}: {updated_additional_info_json}")

                    update_query = """
                        UPDATE service_request
                        SET status_history = %s
                        WHERE request_uid = %s
                        AND (response::jsonb->'ReportIds') @> to_jsonb(ARRAY[%s]::int[]);
                    """
                    cursor.execute(update_query, (updated_additional_info_json, request_uid, report_id))
                    logging.info(f"Committing the update to PostgreSQL for OrderUid {request_uid} and ReportId {report_id}")
                    connection.commit()
                    logging.info(f"Successfully updated additional_info for OrderUid {request_uid} and ReportId: {report_id}")

                for order in report_refs:
                    request_uid = order['request_uid']
                    report_id = order['report_id']
                    asset_id = order['asset_id'] 
                    print(f"Processing Order UID: {request_uid}, Report ID: {report_id}")

                    report_response = order_status(report_id)

                    if report_response.status_code == 200:
                        report_data = report_response.json()
                        logging.info(report_data)

                        status_id = report_data.get("StatusId", {})

                        new_Status = new_status(report_data)

                        connection_service = postgres_service_connection()
                        cursor_service = connection_service.cursor()
                        result = status_history(cursor_service, request_uid, report_id)

                        logging.info(f"Fetched result: {result}")

                        if result is None:
                            logging.warning(f"request_uid={request_uid}, report_id={report_id} doesn't exist in the service databse.")
                            continue

                        existing_additional_info = []
                        if result[0] is not None:
                            existing_additional_info = json.loads(result[0])
                        for entry in existing_additional_info:
                            if (entry["Status"] == new_Status["Status"] and
                                    entry["status_id"] == new_Status["status_id"] and
                                    entry["sub_status_id"] == new_Status["sub_status_id"]):
                                logging.info(f"Matching status found for OrderUid {request_uid} and ReportId {report_id}, skipping insert/update.")
                                cursor_service.close()
                                connection_service.close()
                                break
                        else:
                            try:
                                update_status(cursor_service, connection_service, request_uid, report_id, existing_additional_info, new_Status)
                            except Exception as e:
                                logging.error(f"Error occurred while updating PostgreSQL for OrderUid {request_uid} and ReportId {report_id}: {e}")
                                connection_service.close()
                                return {"status": "error", "message": f"{e}", "data":[]}
                            finally:
                                cursor_service.close()
                                connection_service.close()

                        if status_id == 5:
                            measurement_report = report_data
                            self.store_measurement_blob(tenant_id,user_id,request_uid, asset_id, report_id, measurement_report)
                            self.audit_log(tenant_id, user_id, f"Measurement report inserted successfully for ReportId {report_id}.")
                    # else:
                    #     logging.error(f"Error: Failed to fetch data for Order {request_uid} and ReportId {report_id}. Status code: {report_response.status_code}")
                    #     logging.error(report_response.text)
                    elif report_response.status_code == 204:
                        try:
                            error_info = report_response.json()
                            error_message = error_info.get("errorSummary", "No data available for this report ID.")

                        except Exception as e:
                            error_message = report_response.text or "No data available for this report ID."
                        self.audit_log(tenant_id, user_id, f"ReportId {report_id} returned 204 - {error_message}.")
                        logging.warning(f"ReportId {report_id} returned 204 - {error_message}")
                        
                        continue 
                    elif report_response.status_code == 401:
                        try:
                            error_info = report_response.json()
                            error_message = error_info.get("errorSummary", "Invalid Report ID or unauthorized user.")
                        except ValueError:
                            error_message = report_response.text or "Invalid Report ID or unauthorized user."
                        self.audit_log(tenant_id, user_id, f"ReportId {report_id} returned 401 - {error_message}.")
                        logging.warning(f"ReportId {report_id} returned 401 - {error_message}")
                        continue 
                    else:
                        try:
                            error_info = report_response.json()
                            error_code = error_info.get("errorCode", "UnknownError")
                            error_message = error_info.get("errorSummary", "Unknown error occurred.")
                            full_error = f"{error_code}: {error_message}"
                        except ValueError:
                            full_error = report_response.text or "Unknown error occurred."

                    self.audit_log(tenant_id, user_id, f"Unexpected error for ReportId {report_id}: {full_error}.")
                    continue

                return {"status": "success", "message": "Processed all report IDs."}

            except Exception as e:
                logging.error(f"An error occurred: {e}")
                traceback.print_exc()
                response_handler[c.KEY_STATUS] = "error"
                response_handler[c.KEY_MESSAGE] = f"{e}"
                response_handler['data'] = []
                return response_handler
        

    def store_measurement_blob(self,tenant_id, user_id, request_uid, asset_id, report_id, measurement_report): #Store measurement report in Azure Blob Storage and PostgreSQL customer database
        try:
            def calculate_md5_checksum(data):
                md5_hash = hashlib.md5()
                md5_hash.update(data.encode('utf-8'))
                return md5_hash.hexdigest()
            blob_service_client = BlobServiceClient.from_connection_string(os.getenv("Customer_Asset_Container"))
            container_name = self.ContainerName
            container_client = blob_service_client.get_container_client(container_name)
        
            report_filename = f"Eagleview_{report_id}.json"

            blob_path = f"{asset_id}/measurement/{report_filename}"

            report_json = json.dumps(measurement_report)
            file_size = len(report_json)

            md5_checksum = calculate_md5_checksum(report_json)

            blob_client = container_client.get_blob_client(blob_path)

            try:
                blob_client.upload_blob(
                report_json,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"))
                self.audit_log(tenant_id, user_id, f"The Measurement Report has been successfully stored in the blob at the path {blob_path}.")

            except Exception as e:
                logging.error(f"Error uploading in blob storage: {e}")
                traceback.print_exc()
                return {"error": f"Error uploading in blob storage: {e}"}

            additional_info = {
                "request_uid": request_uid 
            }

            request_uid = json.dumps(additional_info)

            connection_customer = postgres_customer_connection()
            cursor_customer = connection_customer.cursor()

            insert_query = """
                INSERT INTO asset_document
                (created_at,updated_at ,asset_id,document_uid,invalid , version , schema_version, status, path, size, mime_type, md5, additional_info)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """
            cursor_customer.execute(insert_query,(
                                                  datetime.utcnow().isoformat(),
                                                  datetime.utcnow().isoformat(),
                                                  asset_id,                                                  
                                                  str(uuid.uuid4()),
                                                  False,
                                                  1,
                                                  0,
                                                  "Active",
                                                  blob_path,
                                                  file_size,
                                                  "application/json",
                                                  md5_checksum,
                                                  request_uid))
            connection_customer.commit()
            cursor_customer.close()
            connection_customer.close()

            logging.info(f"Measurement report stored successfully in blob storage and PostgreSQL for asset_id {asset_id} and report_id {report_id}.")
            return None
            
        except Exception as e:
            logging.error(f"Error starting measuremnt blob: {e}")
            traceback.print_exc()

    def CancelOrder(self, report_id, tenant_id, user_id):  # Cancel an order using EagleView API
        response_handler = {"status":"", "message":"", "data":[]}
        try:
            token = EagleviewService.authenticate(self)
            if not token:
                logging.error("Token is missing. Please authenticate first.")
                raise Exception("Token is missing. Authentication Required")
            url = self.eagleview_CancelOrder_url
            headers = {
                "Content-Type": "application/json",
                "Authentication": f"Bearer {token}"
            }
            body = {
                "ReportId:":report_id
            }
            response = requests.post(url, headers=headers, json=body)
            if response.status_code == 200:
                logging.info(f"Order with report_id {report_id} cancelled successfully.")
                self.audit_log(tenant_id, user_id, f"Order with report_id {report_id} cancelled successfully.")
                return {"message": f"Order with report_id {report_id} cancelled successfully."}

            elif response.status_code == 401:
                error_message = "Invalid ReportID or unauthorized user. Please try again with valid ReportID or authorized user."
                logging.error(error_message)
                self.audit_log(tenant_id, user_id, error_message)
                return {"status": "error", "message": error_message, "data": []}
            else:
                try:
                    error_info = response.json()
                    error_message = error_info.get("errorSummary", "Unknown error occurred.")
                except ValueError:
                    error_message = response.text or "Unknown error occurred."
                logging.error(f"Error {response.status_code}: {error_message}")
                self.audit_log(tenant_id, user_id, f"Error {response.status_code}: {error_message}")
                return {"status": "error", "message": error_message, "data": []}
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            traceback.print_exc()
            response_handler['status'] = "error"
            response_handler['message'] = f"{e}"
            response_handler['data'] = []
            return response_handler

    def updateOrder_response(self,request_uid, order_data, measurement_type):  # Insert ordered information into PostgreSQL provider database
        try:
            connection = postgres_service_connection()
            if connection:
                try:
                    with connection.cursor() as cursor:
                        select_query = "SELECT request FROM service_request WHERE request_uid = %s;"
                        cursor.execute(select_query, (request_uid,))
                        existing_order = cursor.fetchone()

                        if existing_order:
                            try:
                                existing_request_json = existing_order[0]
                                existing_request_data = json.loads(existing_request_json)
                                existing_request_data['order_type'] = measurement_type 
                                updated_request_json = json.dumps(existing_request_data)

                                update_query = """
                                UPDATE service_request
                                SET
                                    response = %s,
                                    request = %s,
                                    updated_at = %s
                                WHERE request_uid = %s;
                                """
                                cursor.execute(update_query, (
                                    order_data["request"],  
                                    updated_request_json,   
                                    order_data["updated_at"],   
                                    request_uid
                                ))

                                connection.commit()

                                logging.info(f"Updated order with request_uid {request_uid} in PostgreSQL")
                            
                            except Exception as e:
                                logging.error(f"Error while updating request JSON: {e}")

                        else:
                            logging.warning(f"No order found with request_uid {request_uid}")
                except Exception as e:
                    logging.error(f"Failed to insert order into PostgreSQL: {e}")
                    traceback.print_exc()
                    return {"error": f"Failed to insert order into PostgreSQL: {e}"}
        except Exception as e:
            logging.error(f"Error connecting to PostgreSQL provider database: {e}")
            traceback.print_exc()
            return {"error": f"Error connecting to PostgreSQL provider database: {e}"}
    
    def get_available_products(self):
        response_handler = {"status": "", "message": "", "data": []}
        try:
            url = self.eagleview_GetAvaiableProducts
            token = self.authenticate()

            if not token:
                logging.error("Token is missing. Please authenticate.")
                return {"status": "error", "message": "Token is missing. Please authenticate.", "data": []}

            logging.info(f"Using token: {token}")
            api_headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {token}"
            }

            api_response = requests.get(url, headers=api_headers)
            print("api_response",api_response)
            logging.info(f"API response: {api_response}")
            logging.info(f"status code: {api_response.status_code}")

            if api_response.status_code == 200:
                data = api_response.json()

                if not data:
                    logging.warning("API response is null or empty. Returning product data.")
                    raise Exception("API response is null or empty. Returning product data.")
                logging.info(f"API response: {data}")
                data = self.get_eagleview_json_from_blob("EagleviewProductConfig.json")
                response_handler[c.KEY_STATUS] = "success"
                response_handler[c.KEY_MESSAGE] = "Passing default configuration"
                response_handler["data"] = [data]
                return response_handler

            elif api_response.status_code == 401:
                logging.error("Authentication failed.")
                response_handler[c.KEY_MESSAGE] = "Authentication failed. Token is invalid."
                response_handler[c.KEY_STATUS] = "error"
                response_handler["data"] = []
                logging.error("EagleView authentication failed (status=%s)", api_response.status_code)
                return response_handler

            elif api_response.status_code == 404:
                logging.error("404 - Not Found error")
                response_handler[c.KEY_MESSAGE] = "404 error"
                response_handler[c.KEY_STATUS] = "error"
                response_handler["data"] = []
                logging.error(f"Error response handler: {response_handler}")
                return response_handler

            elif api_response.status_code == 500:
                logging.error("Internal server error")
                response_handler[c.KEY_MESSAGE] = "500 error - Internal server error"
                response_handler[c.KEY_STATUS] = "error"
                response_handler["data"] = []
                logging.error("EagleView internal server error (status=%s)", api_response.status_code)
                return response_handler

            else:
                logging.error("EagleView failed to fetch products (status=%s)", api_response.status_code)
                data = self.get_eagleview_json_from_blob("EagleviewProductConfig.json")
                response_handler[c.KEY_STATUS] = "success"
                response_handler[c.KEY_MESSAGE] = "Passing default configuration"
                response_handler["data"] = [data]
                return response_handler

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            traceback.print_exc()
            response_handler[c.KEY_MESSAGE] = f"An error occurred: {e}"
            response_handler[c.KEY_STATUS] = "error"
            response_handler["data"] = []
            return response_handler

    def get_eagleview_json_from_blob(self, file_name: str) -> dict:
        """
        Reads EagleviewProductConfig.json from Azure Blob Storage.

        Env var:
          - Provider_Connection_String : Azure Storage connection string

        Container:
          - eagleview-configuration

        Blob path:
          - Eagleview Product Config/EagleviewProductConfig.json
        """
        try:
            conn_str = os.getenv("Provider_Connection_String")
            if not conn_str:
                raise Exception("Missing env var: Provider_Connection_String")

            container_name = os.getenv("EAGLEVIEW_CONFIGURATION_CONTAINER")
            if not container_name:
                raise Exception("Missing env var: EAGLEVIEW_CONFIGURATION_CONTAINER")

            blob_name = f"Eagleview Product Config/{file_name}"

            service_client = BlobServiceClient.from_connection_string(conn_str)
            blob_client = service_client.get_blob_client(container=container_name, blob=blob_name)

            raw = blob_client.download_blob().readall()
            return json.loads(raw)
        except Exception as e:
            logging.error(f"Failed to read EagleView config from blob. file={file_name}. Error: {e}")
            raise e