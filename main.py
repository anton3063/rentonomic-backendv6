from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import cloudinary
import cloudinary.uploader

app = FastAPI()

# ✅ CORS setup
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
    cloud_name="dc6v9nqrx",
    api_key="624284296664252",
    api_secret="EBgJtD_2Q9Kv2EQJjDaDDEqxmxI"
)

# ✅ PostgreSQL connection
conn = psycopg2.connect(
    host="ep-falling-boat-a58bgdmr.eu-central-1.pg.koyeb.app",
    database="rentonomic",
    user="rentonomic_owner",
    password="Concrete-0113xyz",
    sslmode="require"
)
cur = conn.cursor()

# ✅ Auto-create table if not exists
cur.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id SERIAL PRIMARY KEY,
        title TEXT,
        location TEXT,
        description TEXT,
        price_per_day TEXT,
        image_url TEXT
    )
""")
conn.commit()

# ✅ Submit listing
@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: str = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Upload image to Cloudinary
        result = cloudinary.uploader.upload(image.file)
        image_url = result.get("secure_url")

        # Insert into DB
        cur.execute("""
            INSERT INTO listings (title, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s)
        """, (title, location, description, price_per_day, image_url))
        conn.commit()

        return {"message": "Listing created successfully", "image_url": image_url}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# ✅ Fetch all listings
@app.get("/listings")
def get_listings():
    try:
        cur.execute("SELECT id, title, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
        rows = cur.fetchall()
        listings = []
        for row in rows:
            listings.append({
                "id": row[0],
                "title": row[1],
                "location": row[2],
                "description": row[3],
                "price_per_day": row[4],
                "image_url": row[5]
            })
        return listings
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)



































