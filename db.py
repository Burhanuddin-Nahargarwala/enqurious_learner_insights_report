import psycopg2 as psy
import os
from common import HOST, DBNAME, USER, PASSWORD

# Create a connection
conn = psy.connect(
    host=HOST,
    user=USER,
    database=DBNAME,
    password=PASSWORD
)

# Give the client_id, as row level security is applied
cursor = conn.cursor()

def close_cursor_and_conn():
    cursor.close()
    conn.close()