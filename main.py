from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary
import cloudinary.uploader
import uuid
import psycopg2
import logging

# === Cloudinary Config ===
cloudinary.config(
    cloud_name="dxxsfs8b9",
    api_key="496265773265798",
    api_secret="3QVRl6Q1UJ2DF4ovXQoIMpl-sK8"
)

# === Database Config ===
conn = psycopg2.connect(
    host="dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com",
    dbname="postgresanthony",
    user="postgresanthony_user",
    password="pGgZJxg32gWiUgFshwpFVleNw3RwcLxs"
)
cur = conn.cursor()

# === FastAPI App ===
app = FastAPI()

# === CORS Settings ===
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
    allow_headers=["*"]
)

# === API Routes ===

@app.post("/listing")
async def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    image: UploadFile = Form(...)
):
    try:
        # Upload image to Cloudinary
        result = cloudinary.uploader.upload(image.file)
        image_url = result.get("secure_url")

        # Insert into DB
        listing_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (listing_id, name, location, description, price_per_day, image_url))
        conn.commit()

        return {"message": "Listing created successfully"}

    except Exception as e:
        logging.exception("Error occurred during listing creation")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        cur.execute("SELECT * FROM listings")
        rows = cur.fetchall()
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
        logging.exception("Error fetching listings")
        return JSONResponse(status_code=500, content={"error": str(e)})









































