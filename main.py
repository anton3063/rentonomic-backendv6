# main.py — Rentonomic API (full, with CORS regex + UUID adapter)
# - CORS regex allows rentonomic.com, subdomains, netlify.app, localhost
# - SendGrid US/EU via SENDGRID_API_HOST (auto-fallback to EU on 401)
# - Masked emails in notifications + dashboard
# - Message threads + chat (locked until paid)
# - Request-to-Rent flow (emails, thread auto-create)
# - Stripe checkout + webhook unlocks thread and marks rental paid
# - Idempotent migrations
# - UUID adapter fix for psycopg2 ("can't adapt type 'UUID'")

import os
import uuid
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import psycopg2
import psycopg2.extras

# === UUID adapter fix (prevents "can't adapt type 'UUID'") ===
from psycopg2.extensions import register_adapter, AsIs
import uuid as _uuid
def _adapt_uuid(u: _uuid.UUID): return AsIs(f"'{u}'::uuid")
register_adapter(_uuid.UUID, _adapt_uuid)

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr

import cloudinary
import cloudinary.uploader

import stripe

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content
from python_http_client.exceptions import UnauthorizedError as SGUnauthorized

# -----------------------------
# Env + Config
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", "43200"))  # 30 days

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDGRID_FROM = os.getenv("SENDGRID_FROM", "alert@rentonomic.com")
# Optional: set to "https://api.eu.sendgrid.com" if your account is EU
SENDGRID_API_HOST = os.getenv("SENDGRID_API_HOST", "https://api.sendgrid.com")

CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")
if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://rentonomic.com")

stripe.api_key = STRIPE_SECRET_KEY

# -----------------------------
# App + CORS
# -----------------------------
app = FastAPI(title="Rentonomic API", version="13.2")

# Allow rentonomic.com, *.rentonomic.com, *.netlify.app, localhost (any port)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost(:\d+)?|127\.0\.0\.1(:\d+)?|([a-z0-9-]+\.)?rentonomic\.com|([a-z0-9-]+\.)?netlify\.app)$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Stripe-Signature", "*"],
    expose_headers=["*"],
    max_age=86400,
)

security = HTTPBearer()

logging.basicConfig(level=logging.INFO)

# -----------------------------
# DB helpers
# -----------------------------
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# -----------------------------
# Models
# -----------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str

class SignupIn(BaseModel):
    email: EmailStr
    password: str

class ListingIn(BaseModel):
    name: str
    location: str
    description: str
    price_per_day: float

class RentRequestIn(BaseModel):
    listing_id: uuid.UUID
    dates: List[str]
    message: Optional[str] = None

class MessageIn(BaseModel):
    body: str

class CheckoutIn(BaseModel):
    listing_id: uuid.UUID
    renter_email: EmailStr
    days: int
    currency: str = "gbp"
    amount_total: int  # pence
    dates: List[str]

# -----------------------------
# JWT helpers (HMAC)
# -----------------------------
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).encode())

def jwt_encode(payload: dict, secret: str) -> str:
    import json, hmac
    header = {"alg": JWT_ALG, "typ": "JWT"}
    h = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"

def jwt_decode(token: str, secret: str) -> dict:
    import json, hmac
    try:
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}".encode()
        sig = _b64url_decode(s)
        calc = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, calc):
            raise HTTPException(status_code=401, detail="Invalid token signature")
        payload = json.loads(_b64url_decode(p))
        if "exp" in payload and datetime.utcfromtimestamp(payload["exp"]) < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Token expired")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def make_token(sub: str, email: str, is_admin: bool = False) -> str:
    exp = int((datetime.utcnow() + timedelta(minutes=JWT_EXP_MIN)).timestamp())
    payload = {"sub": sub, "email": email, "is_admin": is_admin, "exp": exp, "iat": int(datetime.utcnow().timestamp())}
    return jwt_encode(payload, JWT_SECRET)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials
    return jwt_decode(token, JWT_SECRET)

def admin_guard(user: dict):
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin only")

# -----------------------------
# Migrations (idempotent)
# -----------------------------
def migrate():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # UUID helper
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            # users
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)
            # listings
            cur.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                owner_id UUID REFERENCES users(id),
                owner_email TEXT,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                description TEXT NOT NULL,
                price_per_day NUMERIC NOT NULL DEFAULT 0,
                image_url TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)
            # rentals
            cur.execute("""
            CREATE TABLE IF NOT EXISTS rentals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                listing_id UUID NOT NULL REFERENCES listings(id),
                lister_id UUID REFERENCES users(id),
                renter_id UUID REFERENCES users(id),
                renter_email TEXT,
                amount_total NUMERIC,
                currency TEXT,
                checkout_session_id TEXT,
                start_date DATE,
                end_date DATE,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)
            # message_threads
            cur.execute("""
            CREATE TABLE IF NOT EXISTS message_threads (
                thread_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                listing_id UUID NOT NULL REFERENCES listings(id),
                rental_id UUID REFERENCES rentals(id),
                lister_id UUID REFERENCES users(id),
                renter_id UUID REFERENCES users(id),
                lister_email TEXT,
                renter_email TEXT,
                start_date DATE,
                end_date DATE,
                is_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)
            # messages
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                thread_id UUID NOT NULL REFERENCES message_threads(thread_id) ON DELETE CASCADE,
                sender_id UUID REFERENCES users(id),
                body TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """)
            # indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_owner ON listings(owner_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rentals_listing ON rentals(listing_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_listing ON message_threads(listing_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_parties ON message_threads(renter_id, lister_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);")
        conn.commit()

migrate()

# -----------------------------
# Helpers
# -----------------------------
def mask_email(e: Optional[str]) -> str:
    if not e: return ""
    try:
        u, d = e.split("@", 1)
        if len(u) <= 2:
            return u[:1] + "*" + "@" + d
        return f"{u[0]}{'*'*(len(u)-2)}{u[-1]}@{d}"
    except:
        return e

def sg_client(host: Optional[str] = None) -> SendGridAPIClient:
    h = host or SENDGRID_API_HOST or "https://api.sendgrid.com"
    return SendGridAPIClient(api_key=SENDGRID_API_KEY, host=h)

def send_email_html(to_addr: str, subject: str, html: str):
    if not SENDGRID_API_KEY:
        logging.error("SENDGRID_API_KEY not set")
        raise HTTPException(500, "Email not configured")
    mail = Mail(
        from_email=Email(SENDGRID_FROM, "Rentonomic Alerts"),
        to_emails=[To(to_addr)],
        subject=subject,
        html_content=Content("text/html", html),
    )
    # Try configured host first; on 401 fallback to EU host once
    try:
        resp = sg_client().send(mail)
        if resp.status_code not in (200, 202):
            logging.error("SendGrid send failed: %s %s", resp.status_code, getattr(resp, "body", b"")[:200])
            raise HTTPException(500, "Failed to send email")
    except SGUnauthorized:
        logging.warning("SendGrid 401 on %s; retrying EU host", SENDGRID_API_HOST)
        resp = sg_client("https://api.eu.sendgrid.com").send(mail)
        if resp.status_code not in (200, 202):
            logging.error("SendGrid EU send failed: %s %s", resp.status_code, getattr(resp, "body", b"")[:200])
            raise HTTPException(500, "Failed to send email")
    except Exception as e:
        logging.exception("SendGrid send failed: %s", e)
        raise HTTPException(500, "Failed to send email")

# -----------------------------
# Auth
# -----------------------------
@app.post("/signup")
def signup(data: SignupIn):
    email = str(data.email).lower().strip()
    pw = data.password
    if not pw or len(pw) < 6:
        raise HTTPException(400, "Password too short")
    pw_hash = hashlib.sha256(pw.encode()).hexdigest()
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            raise HTTPException(400, "Email already registered")
        cur.execute("INSERT INTO users(email, password_hash) VALUES (%s,%s) RETURNING id, is_admin", (email, pw_hash))
        row = cur.fetchone()
        token = make_token(str(row["id"]), email, is_admin=row["is_admin"])
    return {"token": token}

@app.post("/login")
def login(data: LoginIn):
    email = str(data.email).lower().strip()
    pw_hash = hashlib.sha256(data.password.encode()).hexdigest()
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, is_admin, password_hash FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row or row["password_hash"] != pw_hash:
            raise HTTPException(401, "Invalid email or password")
        token = make_token(str(row["id"]), email, is_admin=row["is_admin"])
    return {"token": token}

@app.get("/me")
def me(user=Depends(get_current_user)):
    return user

# -----------------------------
# Listings
# -----------------------------
@app.get("/listings")
def get_listings():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day,
                   (price_per_day * 1.10) as renter_price_per_day,
                   image_url, created_at, owner_email, owner_id
            FROM listings
            ORDER BY created_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
        out = []
        for r in rows:
            out.append({
                "id": str(r["id"]),
                "name": r["name"],
                "location": r["location"],
                "description": r["description"],
                "price_per_day": float(r["price_per_day"]),
                "renter_price_per_day": float(r["renter_price_per_day"]),
                "image_url": r["image_url"],
                "created_at": r["created_at"].isoformat(),
                "owner_email": r["owner_email"],
                "owner_id": str(r["owner_id"]) if r["owner_id"] else None,
            })
        return out

@app.get("/my-listings")
def my_listings(user=Depends(get_current_user)):
    uid = user["sub"]
    email = user.get("email", "").lower()
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT id, owner_id, owner_email, name, location, description, price_per_day, image_url, created_at
            FROM listings
            WHERE owner_id = %s OR lower(owner_email) = %s
            ORDER BY created_at DESC
        """, (uuid.UUID(uid), email))
        rows = cur.fetchall()
        return [
            {
                "id": str(r["id"]),
                "owner_id": str(r["owner_id"]) if r["owner_id"] else None,
                "owner_email": r["owner_email"],
                "name": r["name"],
                "location": r["location"],
                "description": r["description"],
                "price_per_day": float(r["price_per_day"]),
                "image_url": r["image_url"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]

@app.post("/listings")
def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = File(None),
    user=Depends(get_current_user),
):
    owner_id = uuid.UUID(user["sub"])
    owner_email = user.get("email")
    image_url = None
    if image and CLOUDINARY_URL:
        up = cloudinary.uploader.upload(image.file, folder="rentonomic/listings")
        image_url = up.get("secure_url")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO listings (owner_id, owner_email, name, location, description, price_per_day, image_url)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (owner_id, owner_email, name, location, description, price_per_day, image_url))
        lid = cur.fetchone()[0]
        conn.commit()
    return {"id": str(lid)}

@app.put("/listings/{listing_id}")
def update_listing(
    listing_id: uuid.UUID,
    name: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    price_per_day: Optional[float] = Form(None),
    image: UploadFile = File(None),
    user=Depends(get_current_user)
):
    with get_conn() as conn, conn.cursor() as cur:
        if image and CLOUDINARY_URL:
            up = cloudinary.uploader.upload(image.file, folder="rentonomic/listings")
            image_url = up.get("secure_url")
            cur.execute("UPDATE listings SET image_url=%s WHERE id=%s", (image_url, listing_id))
        if name is not None:
            cur.execute("UPDATE listings SET name=%s WHERE id=%s", (name, listing_id))
        if location is not None:
            cur.execute("UPDATE listings SET location=%s WHERE id=%s", (location, listing_id))
        if description is not None:
            cur.execute("UPDATE listings SET description=%s WHERE id=%s", (description, listing_id))
        if price_per_day is not None:
            cur.execute("UPDATE listings SET price_per_day=%s WHERE id=%s", (price_per_day, listing_id))
        conn.commit()
    return {"ok": True}

@app.delete("/listings/{listing_id}")
def delete_listing(listing_id: uuid.UUID, user=Depends(get_current_user)):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM listings WHERE id=%s", (listing_id,))
        conn.commit()
    return {"ok": True}

# -----------------------------
# Message threads & chat
# -----------------------------
@app.get("/message-threads")
def list_threads(user=Depends(get_current_user)):
    uid = uuid.UUID(user["sub"])
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT t.thread_id, t.listing_id, t.rental_id, t.lister_id, t.renter_id,
                   t.lister_email, t.renter_email, t.start_date, t.end_date,
                   t.is_unlocked, t.status,
                   l.name as listing_name, l.location as listing_location
            FROM message_threads t
            JOIN listings l ON l.id = t.listing_id
            WHERE t.lister_id = %s OR t.renter_id = %s
            ORDER BY t.created_at DESC
        """, (uid, uid))
        rows = cur.fetchall()
        out = []
        for r in rows:
            you_are_lister = str(r["lister_id"]) == str(uid)
            counter = r["renter_email"] if you_are_lister else r["lister_email"]
            out.append({
                "thread_id": str(r["thread_id"]),
                "listing": {"id": str(r["listing_id"]), "name": r["listing_name"], "location": r["listing_location"]},
                "rental_id": str(r["rental_id"]) if r["rental_id"] else None,
                "is_unlocked": bool(r["is_unlocked"]),
                "status": r["status"],
                "start_date": r["start_date"].isoformat() if r["start_date"] else None,
                "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                "counterparty": mask_email(counter),
            })
        return out

@app.get("/threads/{thread_id}")
def get_thread(thread_id: uuid.UUID, user=Depends(get_current_user)):
    uid = uuid.UUID(user["sub"])
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT t.thread_id, t.lister_id, t.renter_id, t.is_unlocked, t.status
            FROM message_threads t
            WHERE t.thread_id = %s AND (t.lister_id = %s OR t.renter_id = %s)
        """, (thread_id, uid, uid))
        th = cur.fetchone()
        if not th:
            raise HTTPException(404, "Thread not found")
        cur.execute("""
            SELECT id, sender_id, body, created_at
            FROM messages
            WHERE thread_id = %s
            ORDER BY created_at ASC
        """, (thread_id,))
        msgs = cur.fetchall()
        return {
            "thread_id": str(th["thread_id"]),
            "is_unlocked": bool(th["is_unlocked"]),
            "status": th["status"],
            "messages": [
                {
                    "id": str(m["id"]),
                    "sender_id": str(m["sender_id"]) if m["sender_id"] else None,
                    "body": m["body"],
                    "created_at": m["created_at"].isoformat(),
                } for m in msgs
            ],
        }

@app.post("/threads/{thread_id}/message")
def post_message(thread_id: uuid.UUID, data: MessageIn, user=Depends(get_current_user)):
    uid = uuid.UUID(user["sub"])
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT is_unlocked FROM message_threads
            WHERE thread_id = %s AND (lister_id = %s OR renter_id = %s)
        """, (thread_id, uid, uid))
        th = cur.fetchone()
        if not th:
            raise HTTPException(404, "Thread not found")
        if not th["is_unlocked"]:
            raise HTTPException(403, "Thread locked until payment completes")
        cur.execute("""
            INSERT INTO messages(thread_id, sender_id, body)
            VALUES (%s, %s, %s) RETURNING id, created_at
        """, (thread_id, uid, data.body))
        mid, created_at = cur.fetchone()
        conn.commit()
        return {"id": str(mid), "created_at": created_at.isoformat()}

# -----------------------------
# Request to Rent
# -----------------------------
def create_or_get_thread_for_listing(listing_id: uuid.UUID, current_user: dict, start_date: Optional[str], end_date: Optional[str]) -> uuid.UUID:
    uid = uuid.UUID(current_user["sub"])
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, owner_id, owner_email, name FROM listings WHERE id=%s", (listing_id,))
        lst = cur.fetchone()
        if not lst:
            raise HTTPException(404, "Listing not found")
        lister_id = lst["owner_id"]
        lister_email = lst["owner_email"]
        renter_id = uid
        renter_email = current_user.get("email")

        # existing thread?
        cur.execute("""
            SELECT thread_id FROM message_threads
            WHERE listing_id=%s AND lister_id=%s AND renter_id=%s
            ORDER BY created_at DESC LIMIT 1
        """, (listing_id, lister_id, renter_id))
        th = cur.fetchone()
        if th:
            thread_id = th["thread_id"]
            if start_date and end_date:
                cur.execute("UPDATE message_threads SET start_date=%s, end_date=%s WHERE thread_id=%s",
                            (start_date, end_date, thread_id))
                conn.commit()
            return thread_id

        # create new
        cur.execute("""
            INSERT INTO message_threads(listing_id, lister_id, renter_id, lister_email, renter_email, start_date, end_date, status, is_unlocked)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',FALSE)
            RETURNING thread_id
        """, (listing_id, lister_id, renter_id, lister_email, renter_email, start_date, end_date))
        thread_id = cur.fetchone()["thread_id"]
        conn.commit()
        return thread_id

def send_rent_request_email(listing_name: str, lister_email: str, renter_email: str, start_date: Optional[str], end_date: Optional[str]):
    masked = mask_email(renter_email)
    dashboard_url = f"{FRONTEND_URL}/dashboard.html"
    html = f"""
      <div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;line-height:1.5;color:#111">
        <h2>New rental enquiry</h2>
        <p><strong>{masked}</strong> has requested to rent your item <strong>{listing_name}</strong>.</p>
        <p>Dates requested: <strong>{start_date or 'n/a'} → {end_date or 'n/a'}</strong></p>
        <p>Please reply in your dashboard:</p>
        <p><a href="{dashboard_url}" style="display:inline-block;background:#2ea44f;color:#fff;padding:10px 14px;border-radius:8px;text-decoration:none">Open Dashboard</a></p>
        <p style="color:#555;font-size:12px">For privacy, renter emails are masked. Chat happens inside your Rentonomic dashboard.</p>
      </div>
    """
    send_email_html(lister_email, f"Rental enquiry — {listing_name}", html)

@app.post("/request-to-rent")
def request_to_rent(data: RentRequestIn, user=Depends(get_current_user)):
    dates = data.dates or []
    if not dates:
        raise HTTPException(422, "Dates array required")
    start_date = min(dates)
    end_date = max(dates)

    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, name, owner_email FROM listings WHERE id=%s", (data.listing_id,))
        lst = cur.fetchone()
        if not lst:
            raise HTTPException(404, "Listing not found")
        listing_name = lst["name"]
        lister_email = lst["owner_email"]

    thread_id = create_or_get_thread_for_listing(data.listing_id, user, start_date, end_date)

    # email to lister
    send_rent_request_email(listing_name, lister_email, user.get("email"), start_date, end_date)

    # system message in thread
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO messages(thread_id, sender_id, body) VALUES (%s,%s,%s)",
                    (thread_id, None, f"Rental request for {start_date} → {end_date}"))
        conn.commit()

    return {"ok": True, "thread_id": str(thread_id)}

# -----------------------------
# Stripe checkout + webhook
# -----------------------------
@app.post("/create-checkout-session")
def create_checkout_session(data: CheckoutIn, user=Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")
    if not data.days or data.days < 1:
        raise HTTPException(400, "Invalid days")

    start_date = min(data.dates) if data.dates else None
    end_date = max(data.dates) if data.dates else None
    thread_id = create_or_get_thread_for_listing(data.listing_id, user, start_date, end_date)

    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        # create rental row (pending)
        cur.execute("""
            INSERT INTO rentals(listing_id, lister_id, renter_id, renter_email, amount_total, currency, status, start_date, end_date)
            SELECT %s, l.owner_id, %s, %s, %s, %s, 'pending', %s, %s
            FROM listings l
            WHERE l.id=%s
            RETURNING id
        """, (data.listing_id, uuid.UUID(user["sub"]), data.renter_email, data.amount_total, data.currency,
              start_date, end_date, data.listing_id))
        rental_id = cur.fetchone()["id"]
        # link thread
        cur.execute("UPDATE message_threads SET rental_id=%s WHERE thread_id=%s", (rental_id, thread_id))
        conn.commit()

    session = stripe.checkout.Session.create(
        mode="payment",
        success_url=f"{FRONTEND_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{FRONTEND_URL}/cancel",
        payment_method_types=["card", "klarna", "link", "revolut_pay", "amazon_pay"],
        currency=data.currency,
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": data.currency,
                "unit_amount": data.amount_total,
                "product_data": {"name": "Rental payment"},
            },
        }],
        metadata={
            "listing_id": str(data.listing_id),
            "renter_email": str(data.renter_email),
            "dates": ",".join(data.dates or []),
            "days": str(data.days),
            "rental_id": str(rental_id),
            "thread_id": str(thread_id),
        },
        payment_intent_data={
            "application_fee_amount": int(round(data.amount_total * 0.10)),
        }
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE rentals SET checkout_session_id=%s WHERE id=%s", (session["id"], rental_id))
        conn.commit()

    return {"checkout_url": session["url"], "rental_id": str(rental_id), "thread_id": str(thread_id)}

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        logging.error("STRIPE_WEBHOOK_SECRET not set")
        raise HTTPException(500, "Webhook not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logging.exception("Stripe webhook construct failed: %s", e)
        raise HTTPException(400, "Invalid payload")

    et = event["type"]
    data = event["data"]["object"]
    logging.info("Stripe event: %s", et)

    if et == "checkout.session.completed":
        md = data.get("metadata") or {}
        rental_id = md.get("rental_id")
        thread_id = md.get("thread_id")
        with get_conn() as conn, conn.cursor() as cur:
            if rental_id:
                try:
                    cur.execute("UPDATE rentals SET status='paid' WHERE id=%s", (uuid.UUID(rental_id),))
                except Exception:
                    pass
            if thread_id:
                try:
                    cur.execute("UPDATE message_threads SET is_unlocked=TRUE, status='paid' WHERE thread_id=%s", (uuid.UUID(thread_id),))
                except Exception:
                    pass
            conn.commit()

    return PlainTextResponse("ok")

# -----------------------------
# Approve / Decline
# -----------------------------
@app.post("/rentals/{rental_id}/approve")
def approve_rental(rental_id: uuid.UUID, user=Depends(get_current_user)):
    uid = uuid.UUID(user["sub"])
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT r.id, r.listing_id, l.owner_id, t.thread_id
            FROM rentals r
            JOIN listings l ON l.id = r.listing_id
            LEFT JOIN message_threads t ON t.rental_id = r.id
            WHERE r.id = %s
        """, (rental_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Rental not found")
        if str(row["owner_id"]) != str(uid):
            raise HTTPException(403, "Only the lister can approve")
        cur.execute("UPDATE rentals SET status='approved' WHERE id=%s", (rental_id,))
        if row["thread_id"]:
            cur.execute("UPDATE message_threads SET status='approved' WHERE thread_id=%s", (row["thread_id"],))
        conn.commit()
    return {"ok": True}

@app.post("/rentals/{rental_id}/decline")
def decline_rental(rental_id: uuid.UUID, user=Depends(get_current_user)):
    uid = uuid.UUID(user["sub"])
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT r.id, r.listing_id, l.owner_id, t.thread_id
            FROM rentals r
            JOIN listings l ON l.id = r.listing_id
            LEFT JOIN message_threads t ON t.rental_id = r.id
            WHERE r.id = %s
        """, (rental_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Rental not found")
        if str(row["owner_id"]) != str(uid):
            raise HTTPException(403, "Only the lister can decline")
        cur.execute("UPDATE rentals SET status='declined' WHERE id=%s", (rental_id,))
        if row["thread_id"]:
            cur.execute("UPDATE message_threads SET status='declined' WHERE thread_id=%s", (row["thread_id"],))
        conn.commit()
    return {"ok": True}

# -----------------------------
# Admin
# -----------------------------
@app.post("/login-admin")
def login_admin(data: LoginIn):
    email = str(data.email).lower().strip()
    pw_hash = hashlib.sha256(data.password.encode()).hexdigest()
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, password_hash FROM users WHERE email=%s AND is_admin=TRUE", (email,))
        row = cur.fetchone()
        if not row or row["password_hash"] != pw_hash:
            raise HTTPException(401, "Invalid admin credentials")
        token = make_token(str(row["id"]), email, is_admin=True)
    return {"token": token}

@app.get("/admin/all-rental-requests")
def admin_all_rentals(user=Depends(get_current_user)):
    admin_guard(user)
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT r.id, r.listing_id, r.renter_email, r.amount_total, r.currency,
                   r.checkout_session_id, r.start_date, r.end_date, r.status,
                   l.name as listing_name
            FROM rentals r
            JOIN listings l ON l.id = r.listing_id
            ORDER BY r.created_at DESC
        """)
        rows = cur.fetchall()
        return [
            {
                "id": str(r["id"]),
                "listing_id": str(r["listing_id"]),
                "renter_email": r["renter_email"],
                "amount_total": float(r["amount_total"]) if r["amount_total"] is not None else None,
                "currency": r["currency"],
                "checkout_session_id": r["checkout_session_id"],
                "start_date": r["start_date"].isoformat() if r["start_date"] else None,
                "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                "status": r["status"],
                "listing_name": r["listing_name"],
            }
            for r in rows
        ]

# -----------------------------
# Health
# -----------------------------
@app.get("/")
def root():
    return {"ok": True, "service": "rentonomic-backend"}

@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")



























































































