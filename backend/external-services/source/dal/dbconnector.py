##----------------------------------------------------------------------------
## Description : DB Connection Layer
##----------------------------------------------------------------------------
## Author: Bala Pradeep R
## Copyright: FieldCraft
## License: {license}
## Version: 1.0
##----------------------------------------------------------------------------
## History
## createdDate: 19-03-2025
##----------------------------------------------------------------------------
##  Feature-2745  | 19-03-2025 | BALA PRADEEP R | Added postgres and mongodb Connection
##  Feature-2745  | 26-03-2025 | BALA PRADEEP R | Added provider postgres connection


import psycopg2
import os
from pymongo import MongoClient

from dotenv import load_dotenv
load_dotenv()

def postgres_connection(tenant_id):
    connection = None
    try:
        
        host = os.getenv("TENANT_DB_URL")
        user = os.getenv("TENANT_DB_USERNAME")
        password = os.getenv("TENANT_DB_PASSWORD")
        port = os.getenv("DATABASE_PORT")

        connection = psycopg2.connect(
            host=host,
            user=user,
            password=password,
            port=port,
            dbname=tenant_id
        )

        connection.set_session(autocommit=True)
        return connection
    except Exception as err:
        print(f"Tenant DB connection error: {type(err).__name__}")
        return None

def postgres_service_connection():
    connection = None
    try:
        host= os.getenv("SERVICE_DB_URL")
        user=os.getenv("SERVICE_DB_USERNAME")
        password=os.getenv("SERVICE_DB_PASSWORD")
        dbname=os.getenv("SERVICE_DATABASE")
        port=os.getenv("DATABASE_PORT")

        connection = psycopg2.connect(
            host=host,
            user=user,
            password=password,
            dbname=dbname,
            port=port
        )

        connection.set_session(autocommit=True)
        return connection
    except Exception as err:
        print(f"DB connection error: {type(err).__name__}")
        return None
    
def postgres_customer_connection():
    connection = None
    try:
        connection = psycopg2.connect(
            dbname= os.getenv("CUSTOMER_DATABASE"),
            user=os.getenv("CUSTOMER_DB_USERNAME"), 
            password=os.getenv("CUSTOMER_DB_PASSWORD"), 
            host=os.getenv("CUSTOMER_DB_URL"), 
            port=os.getenv("DATABASE_PORT")
        )

        connection.set_session(autocommit=True)
        return connection
    except Exception as err:
        print(f"DB connection error: {type(err).__name__}")
        return None
        


