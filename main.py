from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import cloudinary
import cloudinary.uploader
import os

app = FastAPI()

# CORS
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

# Cloudinary config from individual ENV vars
cloudinary.config(
    cloud_name=os.getenv("CLOUD_NAME"),
    api_key=os.getenv("CLOUD_API_KEY"),
    api_secret=os.getenv("CLOUD_API_SECRET")
)

# PostgreSQL config
DATABASE_URL = os.getenv("DATABASE_URL")

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
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")

        if not image_url:
            return JSONResponse(status_code=500, content={"error": "Image upload failed"})

        # Save to DB
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        listing_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO listings (id, name, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s, %s)",
            (listing_id, title, location, description, price_per_day, image_url)
        )
        conn.commit()
        cur.close()
        conn.close()

        return {"message": "Listing created successfully!"}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        conn = psycopg2.connect(DATABASE_URL)
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
                "image_url": row[5]
            })

        return listings

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})













































