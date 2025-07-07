from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary.uploader
import os
from uuid import uuid4

# --- FastAPI app ---
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

# --- Load environment variables ---
DATABASE_URL = os.environ.get("DATABASE_URL")
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")

# --- Cloudinary config ---
cloudinary.config(cloudinary_url=CLOUDINARY_URL)

# --- Connect to PostgreSQL ---
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# --- Create listings table if not exists ---
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

# --- API: Submit a new listing ---
@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Save temporary file locally
        temp_filename = f"{UPLOAD_FOLDER}/{uuid4().hex}_{image.filename}"
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        with open(temp_filename, "wb") as buffer:
            buffer.write(await image.read())

        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(temp_filename)
        image_url = upload_result.get("secure_url")

        # Remove temporary file
        os.remove(temp_filename)

        # Save to database
        cursor.execute(
            "INSERT INTO listings (title, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
            (title, location, description, price, image_url)
        )
        conn.commit()

        return JSONResponse(content={"message": "Listing created"}, status_code=201)

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# --- API: Get all listings ---
@app.get("/listings")
def get_listings():
    cursor.execute("SELECT * FROM listings ORDER BY id DESC")
    rows = cursor.fetchall()
    listings = []
    for row in rows:
        listings.append({
            "id": row[0],
            "title": row[1],
            "location": row[2],
            "description": row[3],
            "price_per_day": float(row[4]),
            "image_url": row[5]
        })
    return listings
































