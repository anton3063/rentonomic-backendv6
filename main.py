from fastapi import FastAPI, HTTPException, Request, Depends, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
import bcrypt
import jwt
import uuid
import os
import requests
from datetime import datetime, timedelta

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB connection
DATABASE_URL = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# JWT
JWT_SECRET = os.environ.get("JWT_SECRET", "secret123")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60

# SendGrid
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = "noreply@rentonomic.com"

# === MODELS ===
class SignupData(BaseModel):
    email: str
    password: str

class LoginData(BaseModel):
    email: str
    password: str

class RentRequest(BaseModel):
    item_name: str
    lister_email: str
    selected_dates: list

# === AUTH ===
class JWTBearer(HTTPBearer):
    async def __call__(self, request: Request):
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        try:
            payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            request.state.user = payload
            return credentials.credentials
        except:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

def create_token(email: str):
    exp = datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES)
    payload = {"sub": email, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

# === AUTH ROUTES ===
@app.post("/signup")
def signup(data: SignupData):
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (data.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")

        hashed = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt())
        cursor.execute(
            "INSERT INTO users (id, email, password_hash, created_at, is_verified) VALUES (%s, %s, %s, NOW(), FALSE)",
            (str(uuid.uuid4()), data.email, hashed.decode()))
        conn.commit()
        return {"token": create_token(data.email)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/login")
def login(data: LoginData):
    try:
        cursor.execute("SELECT password_hash FROM users WHERE email = %s", (data.email,))
        user = cursor.fetchone()
        if not user or not bcrypt.checkpw(data.password.encode(), user[0].encode()):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"token": create_token(data.email)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/me")
def get_me(request: Request, token: str = Depends(JWTBearer())):
    return {"email": request.state.user["sub"]}

# === LIST ITEM ===
@app.post("/list")
def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: int = Form(...),
    image: UploadFile = Form(...),
    email: str = Form(...)
):
    try:
        image_bytes = image.file.read()
        cloudinary_url = "https://api.cloudinary.com/v1_1/dkzwvm3hh/image/upload"
        payload = {
            'file': f'data:{image.content_type};base64,{image_bytes.decode("latin1")}',
            'upload_preset': 'rentonomic_unsigned'
        }
        response = requests.post(cloudinary_url, files={"file": image_bytes}, data={"upload_preset": "rentonomic_unsigned"})
        image_url = response.json()["secure_url"]

        cursor.execute("INSERT INTO listings (id, name, location, description, price_per_day, image_url, email) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), name, location, description, price, image_url, email))
        conn.commit()
        return {"message": "Item listed successfully"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/listings")
def get_listings():
    try:
        cursor.execute("SELECT name, location, price_per_day, image_url FROM listings ORDER BY name ASC")
        listings = cursor.fetchall()
        return [{
            "name": r[0],
            "location": r[1],
            "price": r[2],
            "image_url": r[3]
        } for r in listings]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# === RENTAL REQUEST ===
@app.post("/request-to-rent")
def request_to_rent(data: RentRequest):
    try:
        message = f"""
        You have a rental request for your item: {data.item_name}
        Dates requested: {', '.join(data.selected_dates)}
        Message: Is your item available for rent on this/these days?
        """

        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": data.lister_email}]}],
                "from": {"email": FROM_EMAIL},
                "subject": "New Rental Request via Rentonomic",
                "content": [{"type": "text/plain", "value": message}]
            }
        )

        if response.status_code >= 400:
            raise HTTPException(status_code=500, detail="SendGrid error")

        return {"message": "Rental request sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# === ADMIN: GET USERS ===
@app.get("/users", dependencies=[Depends(JWTBearer())])
def get_all_users(request: Request):
    email = request.state.user["sub"]
    if email != "admin@rentonomic.com":
        raise HTTPException(status_code=403, detail="Access denied")

    cursor.execute("SELECT email, created_at, is_verified FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()
    return [{
        "email": u[0],
        "signup_date": u[1].strftime("%Y-%m-%d") if u[1] else "N/A",
        "is_verified": u[2]
    } for u in users]

# === ADMIN: GET ALL LISTINGS ===
@app.get("/all-listings", dependencies=[Depends(JWTBearer())])
def get_all_listings(request: Request):
    email = request.state.user["sub"]
    if email != "admin@rentonomic.com":
        raise HTTPException(status_code=403, detail="Access denied")

    cursor.execute("SELECT name, location, price_per_day, email FROM listings ORDER BY name ASC")
    rows = cursor.fetchall()
    return [{
        "name": r[0],
        "location": r[1],
        "price": r[2],
        "email": r[3]
    } for r in rows]




























































