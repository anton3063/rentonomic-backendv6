from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import os
import cloudinary
import cloudinary.uploader

app = FastAPI()

# CORS settings
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

# Cloudinary config using individual variables
cloudinary.config(
    cloud_name=os.environ.get("CLOUD_NAME"),
    api_key=os.environ.get("CLOUD_API_KEY"),
    api_secret=os.environ.get("CLOUD_API_SECRET"),
    secure=True
)

# Database connection
DATABASE_URL = os.environ.get("DATABASE_URL")

@app.post("/listing")
async def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    image: UploadFile = Form(...)
):
    try:
        result = cloudinary.uploader.upload(image.file)
        image_url = result["secure_url"]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Image upload failed: {str(e)}"})

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        listing_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (listing_id, name, location, description, price_per_day, image_url))
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Listing created successfully"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Database error: {str(e)}"})

@app.get("/listings")
def get_listings():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        listings = [
            {
                "id": row[0],
                "name": row[1],
                "location": row[2],
                "description": row[3],
                "price_per_day": row[4],
                "image_url": row[5]
            }
            for row in rows
        ]
        return listings
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Database read error: {str(e)}"})












































