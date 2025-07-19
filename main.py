from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import cloudinary
import cloudinary.uploader

app = FastAPI()

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary config
cloudinary.config(
    cloud_name="dkzwvm3hh",
    api_key="538411894574491",
    api_secret="BI_MCFrVICVQZWUzJVYTe1GmWfs"
)

# PostgreSQL config
conn = psycopg2.connect("postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony")
cursor = conn.cursor()

# Create table if not exists
cursor.execute('''
CREATE TABLE IF NOT EXISTS listings (
    id UUID PRIMARY KEY,
    name TEXT,
    location TEXT,
    description TEXT,
    price_per_day INTEGER,
    image_url TEXT,
    email TEXT
)
''')
conn.commit()

@app.post("/list")
async def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    email: str = Form(...),
    image: UploadFile = Form(...)
):
    try:
        result = cloudinary.uploader.upload(image.file)
        image_url = result.get("secure_url")
        listing_id = uuid.uuid4()

        cursor.execute(
            "INSERT INTO listings (id, name, location, description, price_per_day, image_url, email) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (listing_id, name, location, description, price_per_day, image_url, email)
        )
        conn.commit()

        return {"message": "Listing created successfully"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        cursor.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings")
        rows = cursor.fetchall()
        listings = [
            {
                "id": str(row[0]),
                "name": row[1],
                "location": row[2].split()[0],  # Show only first part of postcode
                "description": row[3],
                "price_per_day": row[4],
                "image_url": row[5]
            }
            for row in rows
        ]
        return listings
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
























































