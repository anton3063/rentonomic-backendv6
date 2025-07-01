from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary
import cloudinary.uploader
import psycopg2
import uuid
import os

app = FastAPI()

# CORS config
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

# Cloudinary config
cloudinary.config(
    cloud_name="rentonomic",
    api_key="726146152152631",
    api_secret="3ixdoYJKW8KRqx8HRD0s5CQHxj8",
)

# PostgreSQL config
DATABASE_URL = "postgresql://postgres:UoiETFVckuSWSjGMLjjJnXNLgsUfwFKd@switchback.proxy.rlwy.net:27985/railway"

def get_connection():
    return psycopg2.connect(DATABASE_URL)

@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    try:
        result = cloudinary.uploader.upload(file.file)
        return {"image_url": result["secure_url"]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/listing")
async def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image_url: str = Form(...)
):
    try:
        conn = get_connection()
        cur = conn.cursor()
        listing_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO listings (id, name, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s, %s)",
            (listing_id, name, location, description, price_per_day, image_url)
        )
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Listing created"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        listings = [
            {
                "name": row[0],
                "location": row[1],
                "description": row[2],
                "price_per_day": row[3],
                "image_url": row[4],
            }
            for row in rows
        ]

        return listings
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

