import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

# Pydantic model for a listing
class Listing(BaseModel):
    title: str
    description: str
    location: str
    price_per_day: float
    image_url: str

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(db_url)
    return conn

@app.get("/")
def read_root():
    return {"message": "Hello from Rentonomic backend!"}

@app.get("/listings")
def get_listings():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM listings ORDER BY created_at DESC;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {"listings": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/add_listing")
def add_listing(listing: Listing):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO listings (title, description, location, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
        """, (listing.title, listing.description, listing.location, listing.price_per_day, listing.image_url))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Listing added successfully", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))























