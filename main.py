from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary
import cloudinary.uploader
import os
import uuid

app = FastAPI()

# CORS FIX 🚨
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

# Cloudinary setup ✅
cloudinary.config(
    cloud_name="dxuik8b3e",
    api_key="583579498246553",
    api_secret="tBCf2s1I_iR_c0RXuPQ2QDHwzT8"
)

# Database setup ✅
DATABASE_URL = "postgresql://postgres:Concrete-0113xyz@monorail.proxy.rlwy.net:49077/railway"

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Upload to Cloudinary ✅
        public_id = f"rentonomic/{uuid.uuid4()}"
        result = cloudinary.uploader.upload(image.file, public_id=public_id)
        image_url = result["secure_url"]

        # Insert into PostgreSQL ✅
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO listings (title, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s)
        """, (title, location, description, price, image_url))
        conn.commit()
        cur.close()
        conn.close()

        return JSONResponse(content={"message": "Listing created successfully"})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/")
def root():
    return {"status": "Backend is running"}

































