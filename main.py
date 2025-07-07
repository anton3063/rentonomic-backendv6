from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary
import cloudinary.uploader
import uuid
import os

app = FastAPI()

# CORS settings
origins = [
    "https://rentonomic.com",
    "https://www.rentonomic.com",
    "https://rentonomic.netlify.app",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary configuration (from Render ENV VAR)
cloudinary.config(
    secure=True,
    cloud_name="dhtcox1qk",  # If this ever changes, update here
    api_key="544156274679278",
    api_secret=os.getenv("CLOUDINARY_SECRET")  # Loaded from environment
)

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Ensure table exists
cursor.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id SERIAL PRIMARY KEY,
        title TEXT,
        description TEXT,
        location TEXT,
        price_per_day TEXT,
        image_url TEXT
    )
""")
conn.commit()

@app.post("/listings")
async def create_listing(
    title: str = Form(...),
    description: str = Form(...),
    location: str = Form(...),
    price_per_day: str = Form(...),
    image: UploadFile = Form(...)
):
    try:
        # Upload image to Cloudinary
        result = cloudinary.uploader.upload(image.file, public_id=str(uuid.uuid4()), folder="rentonomic")
        image_url = result.get("secure_url")

        # Insert into DB
        cursor.execute(
            "INSERT INTO listings (title, description, location, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
            (title, description, location, price_per_day, image_url)
        )
        conn.commit()

        return {"message": "Listing created successfully."}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        cursor.execute("SELECT * FROM listings ORDER BY id DESC")
        rows = cursor.fetchall()
        listings = []
        for row in rows:
            listings.append({
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "location": row[3],
                "price_per_day": row[4],
                "image_url": row[5]
            })
        return listings
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})




































