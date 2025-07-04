import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError
from fastapi import FastAPI, HTTPException

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

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)

@app.get("/")
def read_root():
    return {"message": "Hello from Rentonomic backend!"}

@app.get("/listings")
def get_listings():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM listings ORDER BY created_at DESC;")
        listings = cursor.fetchall()
        cursor.close()
        conn.close()
        return {"listings": listings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching listings: {e}")






















