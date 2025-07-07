from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import cloudinary
import cloudinary.uploader
import os

app = FastAPI()

# ✅ CORS: allow frontend domains
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

# ✅ Cloudinary config
cloudinary.config(
    cloud_name="YOUR_CLOUD_NAME",
    api_key="YOUR_API_KEY",
    api_secret="YOUR_API_SECRET"
)

# ✅ DB connection
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

@app.get("/listings")
def get_listings():
    cur.execute("SELECT title, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
    rows = cur.fetchall()
    return [
        {
            "title": row[0],
            "location": row[1],
            "description": row[2],
            "price": row[3],
            "image_url": row[4]
        } for row in rows
    ]

@app.post("/listings")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: str = Form(...),
    image: UploadFile = File(...)
):
    # ✅ Upload to Cloudinary
    result = cloudinary.uploader.upload(image.file)
    image_url = result.get("secure_url")

    # ✅ Save to DB
    cur.execute(
        "INSERT INTO listings (title, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s)",
        (title, location, description, price_per_day, image_url)
    )
    conn.commit()
    return {"message": "Listing created successfully"}






































