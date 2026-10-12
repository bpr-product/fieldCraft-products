##----------------------------------------------------------------------------
## Description : Measurement Service Layer
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
##  Feature-2745  | 19-03-2025 | BALA PRADEEP R  |   Measurement Service




import logging
import json
from source.service.EagleviewService import EagleviewService
from source.dal.dbconnector import postgres_connection, postgres_service_connection
import re
import traceback

class MeasurementServices:
    def __init__(self, request_uid, user_uid, tenant_id, asset_id,service_id,
                measurement_type, delivery_type, measurement_structure_type, 
                 is_modified,subscriber_id):
        self.request_uid = request_uid
        self.tenant_id = tenant_id
        self.measurement_type = measurement_type
        self.delivery_type = delivery_type
        self.measurement_structure_type= measurement_structure_type
        self.is_modified = is_modified
        self.asset_id = asset_id
        self.service_id = service_id
        self.user_id = user_uid
        self.subscriber_id = subscriber_id

    def get_connection(self): # To establish a connection with the PostgreSQL database
        try:
            connection = postgres_connection(self.tenant_id)
            return connection
        except Exception as e:
            logging.error(f"Error establishing connection for tenant_id {self.tenant_id}: {e}")
            traceback.print_exc()
            return f"Error establishing connection for tenant_id {self.tenant_id}: {e}"
        
    def is_asset_exists(self):
        response_handler = {"status": "success", "message": "", "data": []}
        try:
            service_conn = postgres_service_connection()

            if not service_conn:
                logging.error("Database connection failed.")
                return False

            asset_id = self.asset_id
            request_uid = self.request_uid
            user_id = self.user_id

            print(f"Checking asset_id: {asset_id}, request_uid: {request_uid}")

            try:
                cursor = service_conn.cursor()
                query = """
                    SELECT request, requested_by , subscriber_id
                    FROM service_request
                    WHERE requested_by = %s AND subscriber_id = %s AND (CAST(request AS JSON)->>'assetId')::int = %s; 
                """
                cursor.execute(query, (user_id,self.subscriber_id, int(asset_id)))
                result = cursor.fetchone()
                print("result",result)

                if result:
                    request_json, requested_by, subscriber_id = result
                    print("request_json",request_json)
                    print("Result from service DB:", result)

                    request_data = json.loads(request_json)
                    order_type = request_data.get("order_type")
                    asset = request_data.get("assetId")
                    print(order_type)
                    print(self.measurement_type)
                    print(asset)
                    print(self.asset_id)
                    print(subscriber_id)
                    print(self.subscriber_id)

                    if order_type == self.measurement_type and asset == int(self.asset_id) and subscriber_id == self.subscriber_id:
                        
                        print("Order type and asset match.")
                        try:
                            return True

                        except Exception as e:
                            logging.error(f"Error querying customer DB for asset_id {asset_id}: {e}")
                            traceback.print_exc()
                            return False

                    else:
                        logging.error("No order had been placed on EagleView for this asset in your account, so an order is now being placed for this asset.")
                        print("No order had been placed on EagleView for this asset in your account, so an order is now being placed for this asset.")
                        return False

                else:
                    logging.error("No order had been placed on EagleView for this asset in your account, so an order is now being placed for this asset.")
                    print("No order had been placed on EagleView for this asset in your account, so an order is now being placed for this asset.")
                    return False

            except Exception as e:
                logging.error(f"Error querying service DB: {e}")
                traceback.print_exc()
                response_handler['status'] = "error"
                response_handler['message'] = f"{e}"
                response_handler['data'] = []
                return response_handler
            
            finally:
                cursor.close()
                service_conn.close()

        except Exception as e:
            logging.error(f"Unexpected error during DB checks: {e}")
            traceback.print_exc()
            response_handler['status'] = "error"
            response_handler['message'] = f"{e}"
            response_handler['data'] = []
            return response_handler


    def is_service_enabled(self,service_id_name): # To check if the required service is enabled for the tenant
        response_handler = {"status": "success", "message": "", "data": []}
    
        try:
            connection_service = postgres_service_connection()
            cursor = connection_service.cursor()
            if not connection_service:
                return False
            try:
                service_subscribers_query = """
                                SELECT service_id, status
                                FROM service_subscriber
                                WHERE id = %s AND invalid = %s AND service_id = %s;
                            """
                cursor.execute(service_subscribers_query, (self.subscriber_id,False,self.service_id))
                service_subscribers_result = cursor.fetchone()
                print("service_subscribers_result", service_subscribers_result)

                if service_subscribers_result:
                    service_id, status = service_subscribers_result
                    if status == "Active":
                        service_master_query = """
                            SELECT invalid, status 
                            FROM service_master
                            WHERE id = %s AND invalid = %s; 
                        """
                        cursor.execute(service_master_query, (service_id,False))
                        service_master_result = cursor.fetchone()
                        print("service_master_result", service_master_result)

                        if service_master_result:
                            invalid , status = service_master_result
                            if status == "Active":
                                print(f"The {service_id_name[0]} service is enabled in your account.")
                                return True
                    else:
                        print(f"{service_id_name[0]} service is not enabled in your account. Please enable the service to place an order.")
                        return False
                else:
                    logging.error(f"{service_id_name[0]} service is not enabled in your account. Please enable the service to place an order.")
                    return False
                # else:
                #     logging.error(f"No service found for {self.service_id} for tenant_id {self.tenant_id}.")
                #     return False
            except Exception as e:
                logging.error(f"Error querying PostgreSQL for tenant_id {self.tenant_id}: {e}.")
                traceback.print_exc()
                response_handler['status'] = "error"
                response_handler['message'] = f"{e}"
                response_handler['data'] = []
                return response_handler
            finally:
                cursor.close()
                connection_service.close()
        except Exception as e:
            logging.error(f"Error establishing connection for tenant_id {service_id_name[0]}: {e}")
            traceback.print_exc()
            response_handler['status'] = "error"
            response_handler['message'] = f"{e}"
            response_handler['data'] = []
            return response_handler

    def place_order(self): # To place the order for the required service
        try:
            connection_service = postgres_service_connection()
            cursor_service = connection_service.cursor()
            cursor_service.execute(
                """ SELECT name
                    FROM service_master
                    WHERE id = %s; """, (self.service_id,))
            service_id_name = cursor_service.fetchone()
            service_enabled = self.is_service_enabled(service_id_name)
            if service_enabled is not True:
                logging.error(f"{service_id_name[0]} service is not enabled for tenant_id {self.tenant_id}.")
                traceback.print_exc()
                return {"status":"error",
                        "message": f"{service_id_name[0]}service is not enabled in your account. Please enable the {self.tenant_id} service before placing the order.",
                        "data": []}

            existing_asset = self.is_asset_exists()
            if existing_asset is True:
                return {
                    "status": "exist",
                    "message":f"Asset already exists for tenant {self.tenant_id}. Service data is already available.",
                    "data":[]
                }
            if not existing_asset:
                try:
                    service_connection = postgres_service_connection()
                    service_cusror = service_connection.cursor()

                    service_cusror.execute(
                        """ SELECT name
                            FROM service_master
                            WHERE id = %s; """, (self.service_id,))
                    service_id_name = service_cusror.fetchone()
                except Exception as e:
                    logging.error (f"Error querying PostgreSQL for service name: {e}")
                    traceback.print_exc()
                    raise Exception (f"No data avilable in the service_master table for the service id {self.service_id}")
                if service_id_name[0] == "EagleView":
                    if not all([self.tenant_id, self.asset_id, self.measurement_type, self.delivery_type , self.measurement_structure_type, self.is_modified,self.user_id]): 
                        logging.error("Missing required parameters.")
                        traceback.print_exc()
                        return "Missing required parameters"
                    eagleview_Service = EagleviewService()
                    logging.info(f"request_uid: {self.request_uid}, user_id: {self.user_id}, tenant_id: {self.tenant_id}, asset_id: {self.asset_id},measurement_type: {self.measurement_type}, delivery_type: {self.delivery_type}, measurement_structure_type: {self.measurement_structure_type}, is_modified: {self.is_modified}")
                    print(f"request_uid: {self.request_uid}, user_id: {self.user_id}, tenant_id: {self.tenant_id}, asset_id: {self.asset_id}, measurement_type: {self.measurement_type}, delivery_type: {self.delivery_type}, measurement_structure_type: {self.measurement_structure_type}, is_modified: {self.is_modified}")

                    result = eagleview_Service.place_measurement_order(self.request_uid,self.user_id,self.tenant_id,self.asset_id,self.measurement_type, self.delivery_type, self.measurement_structure_type, self.is_modified)

                else:
                    logging.error(f"Unsupported service: {service_id_name[0]}.")
                    traceback.print_exc()
                    return f"Unsupported service: {service_id_name[0]}."

                if result.get("status") == "success" and result.get("data"):
                    print(result)
                    order_id = result['data'][0].get('OrderId', "")
                    logging.info(f"Order {order_id} placed successfully for {self.tenant_id} using {self.service_id}")
                    return {
                        "status": result.get("status"),
                        "message": result.get("message"),
                        "data": result.get("data")
                    }
                else:
                    logging.error(f"Error placing order for {self.tenant_id}: {result.get('message')}")
                    return {
                        "status": "error",
                        "data": [],
                        "message": result.get("message", "Unknown error")
                    }

        except Exception as e:
            logging.error(f"Error while placing the order for the tenant {self.tenant_id}: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "data": [],
                "message": f"{(e)}"
            }