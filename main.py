from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary
import cloudinary.uploader
import psycopg2
import os

app = FastAPI()

# CORS setup — ALLOW frontend URLs
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

# Connect to PostgreSQL using environment variable
DATABASE_URL = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Cloudinary config from env
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_URL").split("@")[-1],
    api_key=os.environ.get("CLOUDINARY_URL").split(":")[1][2:],
    api_secret=os.environ.get("CLOUDINARY_URL").split(":")[2].split("@")[0],
)

# Endpoint to get all listings
@app.get("/listings")
def get_listings():
    cursor.execute("SELECT id, item, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
    rows = cursor.fetchall()
    listings = [
        {
            "id": row[0],
            "item": row[1],
            "location": row[2],
            "description": row[3],
            "price_per_day": row[4],
            "image_url": row[5]
        }
        for row in rows
    ]
    return listings

# Endpoint to submit a listing
@app.post("/listings")
async def submit_listing(
    item: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = Form(...)
):
    try:
        # Upload image to Cloudinary
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result["secure_url"]

        # Insert into database
        cursor.execute(
            "INSERT INTO listings (item, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
            (item, location, description, price_per_day, image_url)
        )
        conn.commit()

        return JSONResponse(content={"message": "Listing created successfully"}, status_code=201)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)







































