from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import cloudinary.uploader
import os

app = FastAPI()

# ✅ CORRECT CORS SETTINGS
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

# ✅ DATABASE CONNECTION
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# ✅ CLOUDINARY CONFIG
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# ✅ CREATE LISTINGS TABLE IF NOT EXISTS
cursor.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id SERIAL PRIMARY KEY,
        title TEXT,
        location TEXT,
        description TEXT,
        price_per_day NUMERIC,
        image_url TEXT
    )
""")
conn.commit()

# ✅ POST endpoint for listing submissions
@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = File(...)
):
    # Upload image to Cloudinary
    upload_result = cloudinary.uploader.upload(image.file)
    image_url = upload_result.get("secure_url")

    # Save listing to Postgres
    cursor.execute(
        "INSERT INTO listings (title, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
        (title, location, description, price_per_day, image_url)
    )
    conn.commit()

    return {"message": "Listing created successfully"}

# ✅ GET endpoint to retrieve all listings
@app.get("/listings")
def get_listings():
    cursor.execute("SELECT * FROM listings")
    rows = cursor.fetchall()
    listings = [
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
    return {"listings": listings}






























