import os
import uuid
import base64
import hashlib
import logging
from datetime import datetime, timedelta, date
from typing import Optional, Any, Dict, List

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request, Path, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
import jwt

# Cloudinary
import cloudinary
import cloudinary.uploader as cu

# SendGrid
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# Stripe
import stripe

# -----------------------------
# Environment
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", "43200"))  # 30 days

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDGRID_FROM = os.getenv("SENDGRID_FROM", "alert@rentonomic.com")

CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")
if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://rentonomic.netlify.app")
CURRENCY = os.getenv("CURRENCY", "gbp")
PLATFORM_FEE_PERCENT = float(os.getenv("PLATFORM_FEE_PERCENT", "10"))

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# -----------------------------
# App & CORS
# -----------------------------
app = FastAPI(title="Rentonomic API", version="12.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()

# -----------------------------
# DB helpers
# -----------------------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def run_migrations():
    with db_conn() as conn, conn.cursor() as cur:
        # users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            stripe_account_id TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        # listings
        cur.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id UUID PRIMARY KEY,
            name TEXT,
            location TEXT,
            description TEXT,
            price_per_day INTEGER,
            image_url TEXT,
            owner_id UUID,
            owner_email TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        # rentals
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rentals (
            id UUID PRIMARY KEY,
            listing_id UUID NOT NULL,
            renter_id UUID NOT NULL,
            renter_email TEXT NOT NULL,
            days INTEGER NOT NULL,
            amount_total INTEGER NOT NULL, -- pence
            currency TEXT NOT NULL,
            checkout_session_id TEXT,
            payment_intent_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            start_date DATE,
            end_date DATE
        );
        """)
        # messaging tables
        cur.execute("""
        CREATE TABLE IF NOT EXISTS message_threads (
            id UUID PRIMARY KEY,
            rental_id UUID NOT NULL,
            renter_id UUID NOT NULL,
            lister_id UUID NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY,
            thread_id UUID NOT NULL,
            sender_id UUID NOT NULL,
            body TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        # helpful indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_owner_id ON listings(owner_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_rental ON message_threads(rental_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);")
        conn.commit()

run_migrations()

# -----------------------------
# Utilities
# -----------------------------
def hash_password(pw: str) -> str:
    salt = os.getenv("PW_SALT", "rentonomic-salt").encode()
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 120000)).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return hash_password(pw) == hashed

def create_token(sub: str) -> str:
    payload = {"sub": sub, "exp": datetime.utcnow() + timedelta(minutes=JWT_EXP_MIN), "iat": datetime.utcnow()}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials
    data = decode_token(token)
    email = data.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email, stripe_account_id FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="User not found")
        return row

def renter_price(base_price: int) -> int:
    return round(base_price * (1 + PLATFORM_FEE_PERCENT / 100.0))

def gbp_to_pence(amount: float | int) -> int:
    return int(round(float(amount) * 100))

def listing_public_shape(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row.get("name"),
        "location": row.get("location"),
        "description": row.get("description") or "",
        "price_per_day": row.get("price_per_day"),
        "renter_price_per_day": renter_price(int(row.get("price_per_day") or 0)),
        "image_url": row.get("image_url") or "",
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }

# -----------------------------
# Pydantic models
# -----------------------------
class SignupIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class EditListingIn(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    price_per_day: Optional[int] = None
    image_url: Optional[str] = None

class RentRequestIn(BaseModel):
    listing_id: uuid.UUID
    dates: List[str]
    message: Optional[str] = None

class OnboardingIn(BaseModel):
    refresh_url: Optional[str] = None
    return_url: Optional[str] = None

class CheckoutIn(BaseModel):
    listing_id: uuid.UUID
    dates: List[str]
    days: int
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None

class CheckoutOut(BaseModel):
    url: str
    session_id: str

class SendMessageIn(BaseModel):
    body: str

# -----------------------------
# Exception handlers
# -----------------------------
from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "Invalid input", "details": exc.errors()})

@app.exception_handler(HTTPException)
async def http_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

@app.exception_handler(Exception)
async def generic_handler(request: Request, exc: Exception):
    logging.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"error": "Server error"})

# -----------------------------
# Health
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

# -----------------------------
# Auth status (incl Stripe)
# -----------------------------
@app.get("/me")
def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    acct_id = current_user.get("stripe_account_id")
    status = None
    if STRIPE_SECRET_KEY and acct_id:
        try:
            acct = stripe.Account.retrieve(acct_id)
            status = {
                "details_submitted": bool(acct.get("details_submitted")),
                "charges_enabled": bool(acct.get("charges_enabled")),
                "payouts_enabled": bool(acct.get("payouts_enabled")),
            }
        except Exception:
            status = None
    return {"email": current_user["email"], "stripe_account_id": acct_id, "stripe_status": status}

# -----------------------------
# Auth
# -----------------------------
@app.post("/signup")
def signup(body: SignupIn):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE email=%s", (body.email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        user_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
            (user_id, body.email, hash_password(body.password)),
        )
        conn.commit()
    return {"token": create_token(body.email)}

@app.post("/login")
def login(body: LoginIn):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email, password_hash FROM users WHERE email=%s", (body.email,))
        row = cur.fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(body.email)}

# -----------------------------
# Listings
# -----------------------------
@app.get("/listings")
def all_listings():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day, image_url, created_at
            FROM listings
            ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
    return [listing_public_shape(r) for r in rows]

@app.get("/my-listings")
def my_listings(current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day, image_url, owner_email, created_at
            FROM listings
            WHERE owner_id=%s
            ORDER BY created_at DESC
        """, (current_user["id"],))
        rows = cur.fetchall()
    return rows

@app.post("/list")
def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(""),
    price_per_day: int = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    final_url = None
    if image is not None:
        uploaded = cu.upload(image.file, folder="rentonomic/listings")
        final_url = uploaded.get("secure_url")
    elif image_url:
        final_url = image_url
    else:
        raise HTTPException(status_code=400, detail="Image or image_url required")

    listing_id = str(uuid.uuid4())
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url, owner_id, owner_email)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, name, location, description, price_per_day, image_url, owner_email, created_at
        """, (listing_id, name, location, description, price_per_day, final_url, current_user["id"], current_user["email"]))
        row = cur.fetchone()
        conn.commit()
    return {"ok": True, "listing": row}

@app.put("/listings/{listing_id}")
def update_listing(
    listing_id: uuid.UUID = Path(...),
    body: EditListingIn = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    fields, vals = [], []
    for key in ["name", "location", "description", "price_per_day", "image_url"]:
        val = getattr(body, key) if body else None
        if val is not None:
            fields.append(f"{key}=%s")
            vals.append(val)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    vals.extend([str(listing_id), current_user["id"]])
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            UPDATE listings
            SET {', '.join(fields)}
            WHERE id=%s AND owner_id=%s
            RETURNING id, name, location, description, price_per_day, image_url, owner_email, created_at
        """, tuple(vals))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found or not owned by user")
        conn.commit()
    return {"ok": True, "listing": row}

@app.delete("/listings/{listing_id}")
def delete_listing(listing_id: uuid.UUID, current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM listings WHERE id=%s AND owner_id=%s RETURNING id", (str(listing_id), current_user["id"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found or not owned by user")
        conn.commit()
    return {"ok": True}

# -----------------------------
# Request to Rent (SendGrid)
# -----------------------------
@app.post("/request-to-rent")
def request_to_rent(body: RentRequestIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, price_per_day, owner_email
            FROM listings WHERE id=%s
        """, (str(body.listing_id),))
        listing = cur.fetchone()
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")

    lister_email = listing.get("owner_email")
    if not lister_email:
        raise HTTPException(status_code=400, detail="Listing has no owner email on record")

    message_text = body.message or "Is your item available for rent on this/these days?"
    date_str = ", ".join(body.dates) if body.dates else "(no dates provided)"
    html_body = f"""
    <p><strong>New rent request</strong></p>
    <p>Item: {listing['name']} ({listing['location']})</p>
    <p>Requested dates: {date_str}</p>
    <p>Message: {message_text}</p>
    <p>From: {current_user['email']}</p>
    """

    if not SENDGRID_API_KEY:
        logging.warning("SENDGRID_API_KEY not set; skipping email send")
        return {"ok": True, "sent": False, "note": "SendGrid not configured"}

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        mail = Mail(from_email=SENDGRID_FROM, to_emails=lister_email,
                    subject="Rentonomic — New Rental Request", html_content=html_body)
        resp = sg.send(mail)
        return {"ok": True, "sent": True, "status_code": resp.status_code}
    except Exception as e:
        logging.exception("SendGrid send failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to send request email")

# -----------------------------
# Stripe Connect — Onboarding (light, no website)
# -----------------------------
@app.post("/stripe/create-onboarding-link")
def stripe_onboarding_link(body: OnboardingIn | None = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    try:
        with db_conn() as conn, conn.cursor() as cur:
            account_id = current_user.get("stripe_account_id")

            if not account_id:
                acct = stripe.Account.create(
                    type="express",
                    country="GB",
                    email=current_user["email"],
                    business_type="individual",
                    capabilities={"transfers": {"requested": True}},  # payouts only
                    business_profile={
                        # Friendly text instead of website field
                        "product_description": "Sharing items locally through Rentonomic"
                    },
                    default_currency=CURRENCY,
                )
                account_id = acct["id"]
                cur.execute("UPDATE users SET stripe_account_id=%s WHERE id=%s", (account_id, current_user["id"]))
                conn.commit()
            else:
                # Clean old website & keep friendly description
                try:
                    stripe.Account.modify(
                        account_id,
                        business_profile={
                            "url": "",  # clear old website so Stripe doesn't ask for it
                            "product_description": "Sharing items locally through Rentonomic"
                        }
                    )
                except Exception:
                    pass

        link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=(body.refresh_url if body and body.refresh_url else f"{FRONTEND_URL}/list.html"),
            return_url=(body.return_url  if body and body.return_url  else f"{FRONTEND_URL}/list.html"),
            type="account_onboarding"
        )
        return {"ok": True, "url": link["url"], "account_id": account_id}

    except Exception as e:
        logging.exception("Stripe onboarding link failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create onboarding link")

# -----------------------------
# Stripe Checkout — 90/10 split
# -----------------------------
@app.post("/stripe/create-checkout-session", response_model=CheckoutOut)
def create_checkout_session(body: CheckoutIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    if body.days < 1:
        raise HTTPException(status_code=400, detail="Days must be at least 1")

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT l.id, l.name, l.location, l.price_per_day, l.owner_id, u.stripe_account_id
            FROM listings l
            LEFT JOIN users u ON u.id = l.owner_id
            WHERE l.id=%s
        """, (str(body.listing_id),))
        listing = cur.fetchone()
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")
        lister_acct = listing.get("stripe_account_id")
        if not lister_acct:
            raise HTTPException(status_code=400, detail="Lister has not completed Stripe onboarding")

        base_per_day = int(listing["price_per_day"])
        renter_per_day = renter_price(base_per_day)
        unit_amount = gbp_to_pence(renter_per_day)
        quantity = int(body.days)
        amount_total = unit_amount * quantity
        app_fee = int(round(amount_total * (PLATFORM_FEE_PERCENT / 100.0)))

        success_url = body.success_url or f"{FRONTEND_URL}/dashboard.html?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = body.cancel_url or f"{FRONTEND_URL}/dashboard.html?checkout=cancel"

        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=current_user["email"],
                line_items=[{
                    "price_data": {
                        "currency": CURRENCY,
                        "product_data": {
                            "name": f"{listing['name']} — {listing['location']}",
                            "metadata": {
                                "listing_id": str(listing["id"]),
                                "owner_id": str(listing["owner_id"]),
                            }
                        },
                        "unit_amount": unit_amount,
                    },
                    "quantity": quantity
                }],
                payment_intent_data={
                    "application_fee_amount": app_fee,
                    "transfer_data": {"destination": lister_acct},
                    "metadata": {
                        "listing_id": str(listing["id"]),
                        "renter_email": current_user["email"],
                        "days": str(quantity),
                    }
                },
                metadata={
                    "listing_id": str(listing["id"]),
                    "renter_email": current_user["email"],
                    "days": str(quantity),
                    "dates": ",".join(body.dates or []),
                }
            )
        except Exception as e:
            logging.exception("Stripe session create failed: %s", e)
            raise HTTPException(status_code=500, detail="Stripe session creation failed")

        rental_id = str(uuid.uuid4())
        with db_conn() as conn2, conn2.cursor() as cur2:
            cur2.execute("""
                INSERT INTO rentals (id, listing_id, renter_id, renter_email, days, amount_total, currency, checkout_session_id, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            """, (rental_id, str(listing["id"]), current_user["id"], current_user["email"], quantity, amount_total, CURRENCY, session["id"]))
            conn2.commit()

        return {"url": session["url"], "session_id": session["id"]}

# -----------------------------
# Messaging window helper
# -----------------------------
def messaging_open_for(rental_like: Dict[str, Any]) -> bool:
    if rental_like.get("status") != "paid":
        return False
    end_d = rental_like.get("end_date")
    if not end_d:
        return True  # allow if missing dates (failsafe)
    cutoff = datetime.combine(end_d, datetime.min.time()) + timedelta(days=2)
    return datetime.utcnow() <= cutoff

# -----------------------------
# Messaging endpoints (paid-only; open until 48h after end_date)
# -----------------------------
def user_in_thread(user_id: str, thread: Dict[str, Any]) -> bool:
    return str(user_id) in {str(thread["renter_id"]), str(thread["lister_id"])}

@app.get("/inbox")
def inbox(current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.rental_id, t.renter_id, t.lister_id, t.created_at,
                   r.status, r.start_date, r.end_date
            FROM message_threads t
            JOIN rentals r ON r.id = t.rental_id
            WHERE t.renter_id=%s OR t.lister_id=%s
            ORDER BY t.created_at DESC
        """, (current_user["id"], current_user["id"]))
        rows = cur.fetchall()

    visible = []
    for t in rows:
        if messaging_open_for(t):
            visible.append({
                "thread_id": str(t["id"]),
                "rental_id": str(t["rental_id"]),
                "created_at": t["created_at"].isoformat(),
                "status": t["status"],
                "start_date": t["start_date"].isoformat() if t["start_date"] else None,
                "end_date": t["end_date"].isoformat() if t["end_date"] else None,
            })
    return visible

@app.get("/threads/{thread_id}")
def get_thread(thread_id: uuid.UUID, current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.rental_id, t.renter_id, t.lister_id,
                   r.status, r.start_date, r.end_date
            FROM message_threads t
            JOIN rentals r ON r.id = t.rental_id
            WHERE t.id=%s
        """, (str(thread_id),))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        if not user_in_thread(current_user["id"], t):
            raise HTTPException(status_code=403, detail="Not allowed")
        if not messaging_open_for(t):
            raise HTTPException(status_code=403, detail="Chat closed for this rental")

        cur.execute("""
            SELECT id, sender_id, body, created_at
            FROM messages
            WHERE thread_id=%s
            ORDER BY created_at ASC
        """, (str(thread_id),))
        msgs = cur.fetchall()

    return {
        "thread_id": str(t["id"]),
        "messages": [{
            "id": str(m["id"]),
            "sender_id": str(m["sender_id"]),
            "body": m["body"],
            "created_at": m["created_at"].isoformat()
        } for m in msgs]
    }

@app.post("/threads/{thread_id}/message")
def send_message(thread_id: uuid.UUID, body: SendMessageIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not body.body or not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body required")

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.rental_id, t.renter_id, t.lister_id,
                   r.status, r.start_date, r.end_date
            FROM message_threads t
            JOIN rentals r ON r.id = t.rental_id
            WHERE t.id=%s
        """, (str(thread_id),))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        if not user_in_thread(current_user["id"], t):
            raise HTTPException(status_code=403, detail="Not allowed")
        if not messaging_open_for(t):
            raise HTTPException(status_code=403, detail="Chat closed for this rental")

        cur.execute("""
            INSERT INTO messages (id, thread_id, sender_id, body)
            VALUES (%s, %s, %s, %s)
            RETURNING id, created_at
        """, (str(uuid.uuid4()), str(thread_id), current_user["id"], body.body.strip()))
        m = cur.fetchone()
        conn.commit()

    return {
        "ok": True,
        "message": {
            "id": str(m["id"]),
            "sender_id": str(current_user["id"]),
            "body": body.body.strip(),
            "created_at": m["created_at"].isoformat()
        }
    }

# -----------------------------
# Stripe Webhook
# -----------------------------
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse(status_code=503, content={"error": "Webhook not configured"})

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logging.warning("Invalid Stripe signature: %s", e)
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

    try:
        type_ = event.get("type")
        data = event.get("data", {}).get("object", {})

        if type_ == "checkout.session.completed":
            session_id = data.get("id")
            payment_intent_id = data.get("payment_intent")

            # Dates: we saved CSV in Session.metadata["dates"]
            meta = data.get("metadata", {}) or {}
            dates_csv = meta.get("dates", "")
            parts = [d.strip() for d in dates_csv.split(",") if d.strip()]
            start_d = min(parts) if parts else None
            end_d = max(parts) if parts else None

            with db_conn() as conn, conn.cursor() as cur:
                # Mark rental paid + persist dates
                cur.execute("""
                    UPDATE rentals
                    SET status='paid',
                        payment_intent_id=%s,
                        start_date = COALESCE(start_date, %s::date),
                        end_date   = COALESCE(end_date,   %s::date)
                    WHERE checkout_session_id=%s
                    RETURNING id, listing_id, renter_id
                """, (payment_intent_id, start_d, end_d, session_id))
                r = cur.fetchone()

                if not r:
                    # Idempotent fallback (rare)
                    new_id = str(uuid.uuid4())
                    cur.execute("""
                        INSERT INTO rentals (id, listing_id, renter_id, renter_email, days, amount_total, currency, checkout_session_id, payment_intent_id, status, start_date, end_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'paid', %s, %s)
                        RETURNING id, listing_id, renter_id
                    """, (new_id, str(uuid.uuid4()), str(uuid.uuid4()), "", 1, 0, CURRENCY, session_id, payment_intent_id, start_d, end_d))
                    r = cur.fetchone()

                rental_id = r["id"]
                listing_id = r["listing_id"]
                renter_id = r["renter_id"]

                # Find lister
                cur.execute("SELECT owner_id FROM listings WHERE id=%s", (listing_id,))
                row = cur.fetchone()
                lister_id = row["owner_id"] if row else None

                # Ensure message thread exists
                cur.execute("SELECT id FROM message_threads WHERE rental_id=%s", (rental_id,))
                t = cur.fetchone()
                if t:
                    thread_id = t["id"]
                else:
                    thread_id = str(uuid.uuid4())
                    cur.execute("""
                        INSERT INTO message_threads (id, rental_id, renter_id, lister_id)
                        VALUES (%s, %s, %s, %s)
                    """, (thread_id, rental_id, renter_id, lister_id))
                    cur.execute("""
                        INSERT INTO messages (id, thread_id, sender_id, body)
                        VALUES (%s, %s, %s, %s)
                    """, (str(uuid.uuid4()), thread_id, lister_id, "✅ Payment confirmed. You can chat here to arrange pickup and return."))
                conn.commit()

        elif type_ in ("checkout.session.expired", "payment_intent.payment_failed"):
            session_id = data.get("id") if "checkout" in type_ else None
            payment_intent_id = data.get("id") if type_ == "payment_intent.payment_failed" else None
            with db_conn() as conn, conn.cursor() as cur:
                if session_id:
                    cur.execute("UPDATE rentals SET status='failed' WHERE checkout_session_id=%s", (session_id,))
                elif payment_intent_id:
                    cur.execute("UPDATE rentals SET status='failed' WHERE payment_intent_id=%s", (payment_intent_id,))
                conn.commit()

    except Exception as e:
        logging.exception("Webhook handling error: %s", e)
        return JSONResponse(status_code=500, content={"error": "Webhook handler error"})

    return PlainTextResponse("ok", status_code=200)











































































