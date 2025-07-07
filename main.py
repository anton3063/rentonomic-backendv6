from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary.uploader
import os

# Initialize FastAPI
app = FastAPI()

# CORS fix — allow frontend to talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rentonomic.com",
        "https://www.rentonomic.com",
        "https://rentonomic.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary config (from environment variables)
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# Database connection (from environment variable)
DATABASE_URL = os.getenv("DATABASE_URL")

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Upload image to Cloudinary
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")

        # Connect to PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Insert listing into database
        cur.execute(
            """
            INSERT INTO listings (title, location, description, price, image_url)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (title, location, description, price, image_url),
        )
        conn.commit()
        cur.close()
        conn.close()

        return {"message": "Listing created successfully"}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, title, location, description, price, image_url FROM listings ORDER BY id DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        listings = []
        for row in rows:
            listings.append({
                "id": row[0],
                "title": row[1],
                "location": row[2],
                "description": row[3],
                "price": row[4],
                "image_url": row[5],
            })

        return listings

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})































