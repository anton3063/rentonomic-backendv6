import os
import psycopg2
from psycopg2 import OperationalError
from fastapi import FastAPI

app = FastAPI()

def test_db_connection():
    db_url = os.getenv("DATABASE_URL")
    try:
        conn = psycopg2.connect(db_url)
        conn.close()
        print("✅ Database connection successful!")
    except OperationalError as e:
        print(f"❌ Database connection failed: {e}")

test_db_connection()

# Your existing API routes below

@app.get("/")
def read_root():
    return {"message": "Hello from Rentonomic backend!"}

# Example listings route (adjust or replace with your actual code)
@app.get("/listings")
def get_listings():
    return {"listings": []}  # Replace with actual DB query and response




















