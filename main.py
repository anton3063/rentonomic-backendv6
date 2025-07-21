from fastapi import FastAPI, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import shutil
import os
import requests as httpx
from dotenv import load_dotenv

load_dotenv()  # Load .env file locally (optional)

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DATABASE_URL = "postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony"
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Cloudinary config
CLOUD_NAME = "dkzwvm3hh"
CLOUD_API_KEY = "538411894574491"
CLOUD_API_SECRET = "BI_MCFrVICVQZWUzJVYTe1GmWfs"

# SendGrid config (now pulled from env)
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = "requests@rentonomic.com"

# Create tables if not exists
cur.execute("""
CREATE TABLE IF NOT EXISTS listings (
    id UUID PRIMARY KEY,
    name TEXT,
    location TEXT,
    description TEXT,
    price_per_day INTEGER,
    image_url TEXT,
    lister_email TEXT
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS rental_requests (
    id UUID PRIMARY KEY,
    listing_id UUID,
    renter_email TEXT,
    message TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT
);
""")
conn.commit()

# --- API ROUTES ---

@app.post("/list")
async def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    lister_email: str = Form(...),
    image: UploadFile = Form(...)
):
    # Upload image to Cloudinary
    image_bytes = await image.read()
    with open("temp.jpg", "wb") as f:
        f.write(image_bytes)

    with open("temp.jpg", "rb") as f:
        response = httpx.post(
            f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/image/upload",
            data={"upload_preset": "rentonomic_unsigned"},
            files={"file": f}
        )

    os.remove("temp.jpg")
    image_url = response.json().get("secure_url")

    item_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO listings (id, name, location, description, price_per_day, image_url, lister_email) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (item_id, name, location, description, price_per_day, image_url, lister_email)
    )
    conn.commit()
    return {"message": "Item listed successfully", "id": item_id}


@app.get("/listings")
def get_listings():
    cur.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings;")
    rows = cur.fetchall()
    listings = []
    for row in rows:
        listings.append({
            "id": row[0],
            "name": row[1],
            "location": row[2],
            "description": row[3],
            "price_per_day": row[4],
            "image_url": row[5],
        })
    return listings


@app.post("/request_to_rent")
async def request_to_rent(request: Request):
    data = await request.json()
    listing_id = data["listing_id"]
    renter_email = data["renter_email"]
    message = data["message"]
    start_date = data["start_date"]
    end_date = data["end_date"]
    request_id = str(uuid.uuid4())

    cur.execute("INSERT INTO rental_requests (id, listing_id, renter_email, message, start_date, end_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (request_id, listing_id, renter_email, message, start_date, end_date, "pending"))
    conn.commit()

    # Notify the lister by email
    cur.execute("SELECT lister_email, name FROM listings WHERE id = %s", (listing_id,))
    result = cur.fetchone()
    if result:
        lister_email, item_name = result
        send_email(
            to_email=lister_email,
            subject="New Rentonomic Request",
            content=f"You received a rental request for '{item_name}' from {renter_email}.\n\nMessage:\n{message}\nDates: {start_date} to {end_date}\n\nPlease log into your dashboard to accept or decline."
        )

    return {"message": "Request sent successfully"}


@app.get("/dashboard")
def get_dashboard(email: str):
    cur.execute("SELECT id, name FROM listings WHERE lister_email = %s", (email,))
    listings = cur.fetchall()

    dashboard_data = []
    for listing_id, name in listings:
        cur.execute("SELECT id, renter_email, message, start_date, end_date, status FROM rental_requests WHERE listing_id = %s", (listing_id,))
        requests = cur.fetchall()
        dashboard_data.append({
            "listing_id": listing_id,
            "item_name": name,
            "requests": [
                {
                    "request_id": r[0],
                    "renter_email": r[1],
                    "message": r[2],
                    "start_date": r[3],
                    "end_date": r[4],
                    "status": r[5]
                } for r in requests
            ]
        })
    return dashboard_data


@app.post("/respond_to_request")
async def respond_to_request(request: Request):
    data = await request.json()
    request_id = data["request_id"]
    action = data["action"]  # "accepted" or "declined"

    cur.execute("UPDATE rental_requests SET status = %s WHERE id = %s", (action, request_id))
    conn.commit()

    cur.execute("SELECT renter_email, listing_id FROM rental_requests WHERE id = %s", (request_id,))
    renter_email, listing_id = cur.fetchone()

    cur.execute("SELECT name FROM listings WHERE id = %s", (listing_id,))
    item_name = cur.fetchone()[0]

    send_email(
        to_email=renter_email,
        subject=f"Your Rentonomic Request Was {action.capitalize()}",
        content=f"Your request to rent '{item_name}' has been {action} by the owner.\n\nThanks for using Rentonomic!"
    )

    return {"message": f"Request {action} and renter notified"}


def send_email(to_email: str, subject: str, content: str):
    httpx.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "personalizations": [{
                "to": [{"email": to_email}],
                "subject": subject
            }],
            "from": {"email": FROM_EMAIL},
            "content": [{
                "type": "text/plain",
                "value": content
            }]
        }
    )




























































