from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api
from typing import List

app = FastAPI()

# CORS settings — adjust to your frontend domain(s)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rentonomic.com", "https://www.rentonomic.com", "https://rentonomic.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure Cloudinary from environment variables
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# Connect to PostgreSQL via DATABASE_URL env var
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

# Create listings table if not exists
with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT NOT NULL,
            price_per_day TEXT NOT NULL,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT now()
        );
    """)

class Listing(BaseModel):
    id: int
    title: str
    description: str | None = None
    location: str
    price_per_day: str
    image_url: str | None = None
    created_at: str

@app.post("/listings")
async def create_listing(
    title: str = Form(...),
    description: str = Form(None),
    location: str = Form(...),
    price_per_day: str = Form(...),
    file: UploadFile = File(None)
):
    image_url = None
    if file:
        # Upload image to Cloudinary
        try:
            upload_result = cloudinary.uploader.upload(file.file)
            image_url = upload_result.get("secure_url")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO listings (title, description, location, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at;
        """, (title, description, location, price_per_day, image_url))
        new_id, created_at = cur.fetchone()

    return {
        "id": new_id,
        "title": title,
        "description": description,
        "location": location,
        "price_per_day": price_per_day,
        "image_url": image_url,
        "created_at": created_at.isoformat()
    }

@app.get("/listings", response_model=List[Listing])
def get_listings():
    with conn.cursor() as cur:
        cur.execute("SELECT id, title, description, location, price_per_day, image_url, created_at FROM listings ORDER BY created_at DESC;")
        rows = cur.fetchall()

    return [
        Listing(
            id=row[0],
            title=row[1],
            description=row[2],
            location=row[3],
            price_per_day=row[4],
            image_url=row[5],
            created_at=row[6].isoformat()
        )
        for row in rows
    ]

@app.get("/")
def root():
    return {"message": "Rentonomic backend is running"}










