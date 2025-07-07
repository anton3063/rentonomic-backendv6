from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary
import cloudinary.uploader
import psycopg2
import uuid

app = FastAPI()

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rentonomic.com", "https://www.rentonomic.com", "https://rentonomic.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ DIRECT CLOUDINARY CONFIG — hardcoded for now
cloudinary.config(
    cloud_name="dkzwvm3hh",
    api_key="538411894574491",
    api_secret="BI_MCFrVICVQZWUzJVYTe1GmWfs"
)

# ✅ POSTGRES connection string (yours from earlier)
conn = psycopg2.connect("postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony")

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = Form(...)
):
    try:
        image_data = await image.read()
        upload_result = cloudinary.uploader.upload(image_data)
        image_url = upload_result.get("secure_url")

        listing_id = str(uuid.uuid4())

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO listings (id, title, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s, %s)",
                (listing_id, title, location, description, price_per_day, image_url)
            )
            conn.commit()

        return {"message": "Listing created", "image_url": image_url}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
            listings = cur.fetchall()

        result = [
            {
                "id": row[0],
                "title": row[1],
                "location": row[2],
                "description": row[3],
                "price_per_day": float(row[4]),
                "image_url": row[5],
            }
            for row in listings
        ]
        return result

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})








































