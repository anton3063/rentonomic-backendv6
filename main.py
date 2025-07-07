from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary.uploader
import os

app = FastAPI()

# ✅ CORS fix for your frontend
origins = [
    "https://rentonomic.com",
    "https://www.rentonomic.com",
    "https://rentonomic.netlify.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Get DB & Cloudinary from environment
DATABASE_URL = os.environ.get("DATABASE_URL")
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# ✅ Create table if not exists
cur.execute("""
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

@app.get("/listings")
def get_listings():
    cur.execute("SELECT * FROM listings")
    rows = cur.fetchall()
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

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = Form(...)
):
    try:
        result = cloudinary.uploader.upload(image.file)
        image_url = result["secure_url"]

        cur.execute(
            "INSERT INTO listings (title, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
            (title, location, description, price_per_day, image_url)
        )
        conn.commit()

        return {"message": "Listing created successfully."}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})





































