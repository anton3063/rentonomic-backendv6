from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary.uploader

# === CONFIG ===
app = FastAPI()

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

# === CLOUDINARY ===
cloudinary.config(
    cloud_name="drr5ejtqn",
    api_key="844936237367879",
    api_secret="dvfIUZ4It9Qpp6EqMEnNPsmZcsk"
)

# === POSTGRES ===
conn = psycopg2.connect(
    host="ep-falling-boat-a58bgdmr.eu-central-1.pg.koyeb.app",
    database="rentonomic",
    user="rentonomic_owner",
    password="Concrete-0113xyz"
)
cur = conn.cursor()

# === ENDPOINTS ===

@app.get("/")
def home():
    return {"message": "Backend is live"}

@app.get("/listings")
def get_listings():
    cur.execute("SELECT id, title, location, description, price, image_url FROM listings ORDER BY id DESC")
    rows = cur.fetchall()
    listings = []
    for row in rows:
        listings.append({
            "id": row[0],
            "title": row[1],
            "location": row[2],
            "description": row[3],
            "price": row[4],
            "image_url": row[5]
        })
    return listings

@app.post("/listings")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: str = Form(...),
    image: UploadFile = File(...)
):
    try:
        result = cloudinary.uploader.upload(image.file)
        image_url = result["secure_url"]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Image upload failed: {str(e)}"})

    try:
        cur.execute(
            "INSERT INTO listings (title, location, description, price, image_url) VALUES (%s, %s, %s, %s, %s)",
            (title, location, description, price, image_url)
        )
        conn.commit()
        return {"message": "Listing created successfully"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Database error: {str(e)}"})


































