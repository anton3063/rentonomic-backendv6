from fastapi import FastAPI, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import psycopg2
import cloudinary.uploader
from uuid import uuid4

# Initialize app
app = FastAPI()

# CORS setup
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

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")

# Configure Cloudinary
cloudinary.config(cloudinary_url=CLOUDINARY_URL)

# Connect to PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Create table if it doesn't exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS listings (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    location TEXT,
    description TEXT,
    price_per_day NUMERIC,
    image_url TEXT
)
""")
conn.commit()

# Submit listing
@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        temp_path = f"{UPLOAD_FOLDER}/{uuid4().hex}_{image.filename}"
        with open(temp_path, "wb") as f:
            f.write(await image.read())

        result = cloudinary.uploader.upload(temp_path)
        os.remove(temp_path)
        image_url = result["secure_url"]

        cursor.execute(
            "INSERT INTO listings (title, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
            (title, location, description, price, image_url)
        )
        conn.commit()

        return {"message": "Listing created"}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# Get all listings
@app.get("/listings")
def get_listings():
    cursor.execute("SELECT * FROM listings ORDER BY id DESC")
    rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "title": row[1],
            "location": row[2],
            "description": row[3],
            "price_per_day": float(row[4]),
            "image_url": row[5]
        }
        for row in rows
    ]

































