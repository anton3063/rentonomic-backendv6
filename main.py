from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary
import cloudinary.uploader
import uuid
import os

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rentonomic.com",
        "https://www.rentonomic.com",
        "https://rentonomic.netlify.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary config
cloudinary.config(
    cloud_name="dxfu0e4qa",
    api_key="877465556446563",
    api_secret="DQUOrjArLXt3JYtt2xvLjPDoUkA"
)

# Railway PostgreSQL config
DB_URL = "postgresql://postgres:UoiETFVckuSWSjGMLjjJnXNLgsUfwFKd@switchback.proxy.rlwy.net:27985/railway"

# Create listings table if not exists
def create_table():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id UUID PRIMARY KEY,
            title TEXT,
            location TEXT,
            description TEXT,
            price_per_day NUMERIC,
            image_url TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

create_table()

@app.get("/listings")
def get_listings():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    listings = []
    for row in rows:
        listings.append({
            "id": str(row[0]),
            "title": row[1],
            "location": row[2],
            "description": row[3],
            "price_per_day": float(row[4]),
            "image_url": row[5]
        })
    return listings

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result["secure_url"]

        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        listing_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO listings (id, title, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (listing_id, title, location, description, price_per_day, image_url))
        conn.commit()
        cur.close()
        conn.close()

        return JSONResponse(content={"message": "Listing created successfully."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
