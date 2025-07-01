import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary
import cloudinary.uploader
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

# Configure CORS
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

# Cloudinary config from environment variables or hardcode for now
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "dzd5v9ggu"),
    api_key=os.getenv("CLOUDINARY_API_KEY", "815282963778522"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "JRXqWrZoY1ibmiPyDWW_TpQ4D4c"),
)

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
conn.autocommit = True

@app.get("/")
def root():
    return {"message": "Rentonomic backend is live!"}

@app.get("/listings")
def get_listings():
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM listings ORDER BY created_at DESC")
            listings = cur.fetchall()
        return listings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        image_url = upload_result.get("secure_url")

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO listings (title, location, description, price_per_day, image_url)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (title, location, description, price_per_day, image_url),
            )

        return {"message": "Listing created successfully", "image_url": image_url}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})








