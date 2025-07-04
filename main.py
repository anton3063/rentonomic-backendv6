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

@app.get("/")
def read_root():
    return {"message": "Hello from Rentonomic backend!"}

@app.get("/listings")
def get_listings():
    db_url = os.getenv("DATABASE_URL")
    try:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("SELECT * FROM listings;")  # Adjust table name if needed
        listings = cur.fetchall()
        cur.close()
        conn.close()
        return {"listings": listings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")





















