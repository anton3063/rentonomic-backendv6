from fastapi import FastAPI, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import os
import cloudinary.uploader

app = FastAPI()

# Allow frontend domains
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

# Get environment variables
DB_URL = os.getenv("DATABASE_URL")
CLOUD_NAME = os.getenv("CLOUD_NAME")
CLOUD_API_KEY = os.getenv("CLOUD_API_KEY")
CLOUD_API_SECRET = os.getenv("CLOUD_API_SECRET")

# Cloudinary config
cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=CLOUD_API_KEY,
    api_secret=CLOUD_API_SECRET
)

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Upload image to Cloudinary
        result = cloudinary.uploader.upload(image.file)
        image_url = result.get("secure_url")

        # Connect to PostgreSQL
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        listing_id = str(uuid.uuid4())

        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (listing_id, title, location, description, price_per_day, image_url))

        conn.commit()
        cur.close()
        conn.close()

        return {"message": "Listing created successfully"}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        listings = []
        for row in rows:
            listings.append({
                "id": row[0],
                "name": row[1],
                "location": row[2],
                "description": row[3],
                "price_per_day": row[4],
                "image_url": row[5],
            })

        return listings

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})















































