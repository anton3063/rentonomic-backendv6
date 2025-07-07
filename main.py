from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary.uploader
import os
from typing import List

# --- ENV VARIABLES ---
DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")  # Should be like: cloudinary://API_KEY:API_SECRET@cloud_name

# --- Cloudinary setup ---
cloudinary.config(cloudinary_url=CLOUDINARY_URL)

# --- DB Connection ---
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# --- Create Table if not exists ---
cur.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id SERIAL PRIMARY KEY,
        title TEXT,
        description TEXT,
        price_per_day NUMERIC,
        image_url TEXT,
        location TEXT
    )
""")
conn.commit()

# --- FastAPI setup ---
app = FastAPI()

# --- CORS ---
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

# --- GET listings ---
@app.get("/listings")
def get_listings():
    cur.execute("SELECT id, title, description, price_per_day, image_url, location FROM listings")
    rows = cur.fetchall()
    listings = [
        {
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "price_per_day": float(row[3]),
            "image_url": row[4],
            "location": row[5],
        }
        for row in rows
    ]
    return listings

# --- POST listing ---
@app.post("/listings")
async def post_listing(
    title: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    location: str = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Upload image to Cloudinary
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")

        # Store listing in database
        cur.execute(
            "INSERT INTO listings (title, description, price_per_day, image_url, location) VALUES (%s, %s, %s, %s, %s)",
            (title, description, price_per_day, image_url, location)
        )
        conn.commit()

        return {"message": "Listing added successfully."}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})





























