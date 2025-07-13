from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uuid
import psycopg2
import requests

# CORS settings
app = FastAPI()
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

# Database connection
DATABASE_URL = "postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony"
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Cloudinary credentials
CLOUD_NAME = "dkzwvm3hh"
CLOUD_API_KEY = "538411894574491"
CLOUD_API_SECRET = "BI_MCFrVICVQZWUzJVYTe1GmWfs"

# Listing model
class Listing(BaseModel):
    name: str
    location: str
    description: str
    price_per_day: int
    image_url: str
    lister_name: str
    lister_email: str

# Endpoint to submit a new listing
@app.post("/listings")
def create_listing(listing: Listing):
    try:
        # Check if lister exists
        cursor.execute("SELECT id FROM listers WHERE email = %s", (listing.lister_email,))
        lister = cursor.fetchone()

        if lister:
            lister_id = lister[0]
        else:
            lister_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO listers (id, name, email) VALUES (%s, %s, %s)",
                (lister_id, listing.lister_name, listing.lister_email)
            )

        # Create new listing
        listing_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url, lister_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            listing_id,
            listing.name,
            listing.location,
            listing.description,
            listing.price_per_day,
            listing.image_url,
            lister_id
        ))

        conn.commit()
        return {"message": "Listing created successfully"}

    except Exception as e:
        conn.rollback()
        return JSONResponse(content={"error": str(e)}, status_code=500)

# Endpoint to fetch all listings
@app.get("/listings")
def get_listings():
    try:
        cursor.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings")
        rows = cursor.fetchall()
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
        return JSONResponse(content={"error": str(e)}, status_code=500)
















































