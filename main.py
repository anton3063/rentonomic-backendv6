from fastapi import FastAPI, HTTPException, Request, Depends, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
import bcrypt
import jwt
import uuid
import os
import requests
from datetime import datetime
import cloudinary
import cloudinary.uploader

# Load environment variables
DATABASE_URL = os.environ.get("DATABASE_URL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
JWT_SECRET = os.environ.get("JWT_SECRET", "secret123")
CLOUD_NAME = os.environ.get("CLOUD_NAME")
CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY")
CLOUD_API_SECRET = os.environ.get("CLOUD_API_SECRET")

# Initialize Cloudinary
cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=CLOUD_API_KEY,
    api_secret=CLOUD_API_SECRET
)

# Initialize FastAPI
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can lock this to your domain if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT Token Handling
security = HTTPBearer()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        return payload.get("email")
    except jwt.PyJWTError:
        raise HTTPException(status_code=403, detail="Invalid token")

def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    email = verify_token(credentials)
    if email != "admin@rentonomic.com":
        raise HTTPException(status_code=403, detail="Admin access only")
    return email

# Models
class AuthRequest(BaseModel):
    email: str
    password: str

class RentalRequest(BaseModel):
    listing_id: str
    renter_email: str
    dates: list

# AUTH
@app.post("/signup")
def signup(auth: AuthRequest):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s", (auth.email,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_pw = bcrypt.hashpw(auth.password.encode(), bcrypt.gensalt())
    cur.execute("INSERT INTO users (email, password, signup_date) VALUES (%s, %s, %s)",
                (auth.email, hashed_pw.decode(), datetime.utcnow()))
    conn.commit()
    cur.close()
    conn.close()
    token = jwt.encode({"email": auth.email}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

@app.post("/login")
def login(auth: AuthRequest):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT password FROM users WHERE email = %s", (auth.email,))
    result = cur.fetchone()
    if not result or not bcrypt.checkpw(auth.password.encode(), result[0].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = jwt.encode({"email": auth.email}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

# LIST ITEM
@app.post("/list")
def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: int = Form(...),
    email: str = Form(...),
    image: UploadFile = File(...)
):
    try:
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url, email)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), name, location, description, price, image_url, email))
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Listing created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# GET LISTINGS
@app.get("/listings")
def get_listings():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings ORDER BY name ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    listings = []
    for r in rows:
        listings.append({
            "id": r[0],
            "name": r[1],
            "location": r[2],
            "description": r[3],
            "price": r[4],
            "image_url": r[5]
        })
    return listings

# REQUEST TO RENT
@app.post("/request-to-rent")
def request_to_rent(data: RentalRequest):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT email, name FROM listings WHERE id = %s", (data.listing_id,))
        result = cur.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Listing not found")
        owner_email, item_name = result

        subject = f"Rental Request for '{item_name}'"
        body = f"""
        Hi there,

        Someone is requesting to rent your item: {item_name}
        Dates requested: {', '.join(data.dates)}

        Message: Is your item available for rent on this/these days?

        Please log in to your dashboard to confirm or decline this request.

        — Rentonomic
        """

        headers = {
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "personalizations": [{"to": [{"email": owner_email}]}],
            "from": {"email": "noreply@rentonomic.com"},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}]
        }

        res = requests.post("https://api.sendgrid.com/v3/mail/send", json=payload, headers=headers)
        if res.status_code not in [200, 202]:
            raise HTTPException(status_code=500, detail="Failed to send email")

        return {"message": "Request sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ADMIN: Users
@app.get("/users")
def get_users(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT email, signup_date FROM users")
    users = [{"email": r[0], "signup_date": r[1], "is_verified": True} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return users

# ADMIN: Listings
@app.get("/all-listings")
def get_all(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, location, price_per_day, email FROM listings")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "name": r[1], "location": r[2], "price": r[3], "email": r[4]} for r in rows]

# ADMIN: Flags
@app.get("/admin/flags")
def get_flags(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, type, description, reporter_email, status, listing_id FROM flags")
    flags = [{
        "id": r[0],
        "type": r[1],
        "description": r[2],
        "reporter": r[3],
        "status": r[4],
        "listing_id": r[5]
    } for r in cur.fetchall()]
    cur.close()
    conn.close()
    return flags

@app.post("/admin/flags/{flag_id}/review")
def mark_flag_reviewed(flag_id: str, admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE flags SET status = 'Reviewed' WHERE id = %s", (flag_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Flag marked as reviewed"}

@app.delete("/admin/listings/{listing_id}")
def delete_listing(listing_id: str, admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM listings WHERE id = %s", (listing_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Listing deleted"}


























































