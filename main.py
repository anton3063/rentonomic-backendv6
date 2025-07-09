from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import uuid
import requests

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

# Database setup
DATABASE_URL = "postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony"

# Cloudinary config
CLOUDINARY_UPLOAD_URL = "https://api.cloudinary.com/v1_1/dkzwvm3hh/image/upload"
CLOUDINARY_API_KEY = "538411894574491"
CLOUDINARY_API_SECRET = "BI_MCFrVICVQZWUzJVYTe1GmWfs"

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: str = Form(...),
    image: UploadFile = Form(...)
):
    # Upload to Cloudinary
    try:
        image_bytes = await image.read()
        upload_response = requests.post(
            CLOUDINARY_UPLOAD_URL,
            files={"file": image_bytes},
            data={
                "upload_preset": "ml_default",
                "api_key": CLOUDINARY_API_KEY,
                "timestamp": "1234567890",  # required placeholder
            },
            auth=(CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET)
        )

        cloudinary_result = upload_response.json()
        image_url = cloudinary_result.get("secure_url")

        if not image_url:
            raise HTTPException(status_code=500, detail="Image upload failed.")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    # Insert into DB
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        listing_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (listing_id, title, location, description, int(price_per_day), image_url))

        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Listing submitted successfully."}

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











































