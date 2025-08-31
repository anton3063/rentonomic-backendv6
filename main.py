# main.py — Rentonomic (FastAPI) — full production build
# ------------------------------------------------------------
# Major features:
# - Auth (signup/login) with JWT
# - Listings: public list/get, create (owner), update (owner), my-listings
#             Admin: soft-delete/restore, hard-delete (manual cascade)
# - Rental requests: create (JSON), list mine (renter/lister/all), accept/decline
# - Messaging: threads/messages; server-side PII guard while locked; unlock on payment
# - Admin: site-wide rental requests (limit/offset), listing moderation
# - Stripe Connect: persist lister account, enforce onboarding, destination charge, fee (10%)
#                   onboarding link, webhook → mark paid, unlock thread, insert rentals, email both parties
# - Emails: SendGrid best-effort; never break requests on email failure
# - Outward postcode rule (store prefix only)
# - CORS configured for Netlify + production domain
# - Auto-creates aux tables if missing: stripe_accounts, rentals
# ------------------------------------------------------------

import os
import re
from typing import Optional, Union, Dict, Any, List
from datetime import datetime, timedelta, date

from fastapi import (
    FastAPI, Depends, HTTPException, Body, Form, Request, Header, Query
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import bcrypt
import jwt  # PyJWT
import psycopg2
import psycopg2.extras

# SendGrid (optional)
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:  # pragma: no cover
    SendGridAPIClient = None
    Mail = None

# Stripe (optional)
try:
    import stripe as stripe_sdk
except Exception:  # pragma: no cover
    stripe_sdk = None

# ---------------------- Config ----------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me")
JWT_ALG = "HS256"

ADMIN_EMAILS = {"admin@rentonomic.com"}  # golden-rule admin
ALERT_FROM_EMAIL = os.environ.get("ALERT_FROM_EMAIL", "alert@rentonomic.com")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_BASE = os.environ.get("FRONTEND_BASE", "https://rentonomic.com")

PLATFORM_FEE_RATE = 0.10  # 10%

app = FastAPI(title="Rentonomic API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rentonomic.com",
        "https://www.rentonomic.com",
        "https://rentonomic.netlify.app",
        "http://localhost",
        "http://localhost:5173",
        "*",  # keep broad during iteration; lock down later
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------- DB helpers ----------------------

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def fetch_all_dict(cur) -> List[Dict[str, Any]]:
    try:
        rows = cur.fetchall()
    except Exception:
        return []
    return [{desc.name: val for desc, val in zip(cur.description, r)} for r in rows]

def fetch_one_dict(cur) -> Optional[Dict[str, Any]]:
    r = cur.fetchone()
    if r is None:
        return None
    return {desc.name: val for desc, val in zip(cur.description, r)}

def ensure_aux_tables():
    """Create auxiliary tables if they don't exist."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stripe_accounts (
                email TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rentals (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                listing_id TEXT NOT NULL,
                renter_email TEXT NOT NULL,
                lister_email TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                amount_base_pence INTEGER NOT NULL,
                amount_renter_pence INTEGER NOT NULL,
                platform_fee_pence INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()
    finally:
        try: cur.close(); conn.close()
        except Exception: pass

@app.on_event("startup")
def _startup():
    ensure_aux_tables()

# ---------------------- Auth helpers ----------------------

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_access_token(email: str, days: int = 45) -> str:
    payload = {"sub": email, "exp": datetime.utcnow() + timedelta(days=days)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload.get("sub") or ""
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def is_admin(email: str) -> bool:
    return (email or "").lower() in ADMIN_EMAILS

# dependency: read Authorization header
def verify_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    return decode_token(token)

def outward_prefix(loc: Optional[str]) -> str:
    if not loc:
        return ""
    # outward postcode only
    first = (loc.split("—")[0]).split("-")[0].split(",")[0].strip().split()
    return (first[0] if first else "").upper()

# ---------------------- Email (best-effort) ----------------------

def send_alert_email(to_email: str, subject: str, html: str) -> None:
    try:
        if not (to_email and SENDGRID_API_KEY and SendGridAPIClient and Mail):
            return
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        msg = Mail(
            from_email=ALERT_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=html,
        )
        sg.send(msg)
    except Exception:
        pass  # never fail the API on email issues

# ---------------------- Models ----------------------

class SignupIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class RequestToRentIn(BaseModel):
    listing_id: Union[int, str]
    start_date: date
    end_date: date

class DeclineIn(BaseModel):
    reason: Optional[str] = None

class AcceptIn(BaseModel):
    note: Optional[str] = None  # placeholder for future

class CheckoutStartIn(BaseModel):
    request_id: Union[int, str]

class MessageSendIn(BaseModel):
    thread_id: Union[int, str]
    body: str

# ---------------------- Auth routes ----------------------

@app.post("/signup")
def signup(payload: SignupIn):
    email = payload.email.lower()
    password_hash = hash_password(payload.password)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        cur.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (%s, %s, NOW())",
            (email, password_hash),
        )
        conn.commit()
        token = create_access_token(email)
        return {"token": token, "email": email}
    finally:
        cur.close(); conn.close()

@app.post("/login")
def login(payload: LoginIn):
    email = payload.email.lower()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT password_hash FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row or not verify_password(payload.password, row[0]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_access_token(email)
        return {"token": token, "email": email}
    finally:
        cur.close(); conn.close()

# ---------------------- Listings ----------------------

@app.get("/listings")
def get_listings():
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, description, location, image_url,
                   price_per_day, owner_email, created_at, deleted_at
            FROM listings
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
        """)
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.get("/listing/{listing_id}")
def get_listing(listing_id: Union[int, str]):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, description, location, image_url,
                   price_per_day, owner_email, created_at, deleted_at
            FROM listings
            WHERE id=%s
        """, (str(listing_id),))
        row = fetch_one_dict(cur)
        if not row or row.get("deleted_at"):
            raise HTTPException(status_code=404, detail="Listing not found")
        return row
    finally:
        cur.close(); conn.close()

@app.post("/list")
def create_listing(
    name: str = Form(...),
    description: Optional[str] = Form(""),
    location: Optional[str] = Form(""),
    price_per_day: Union[str, float] = Form(...),
    image_url: Optional[str] = Form(""),
    current_user: str = Depends(verify_token),
):
    loc = outward_prefix(location)
    try:
        price_num = float(str(price_per_day).replace(",", ""))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid price")

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO listings (name, description, location, price_per_day, image_url, owner_email, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (name, description, loc, price_num, image_url, current_user))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"ok": True, "id": new_id}
    finally:
        cur.close(); conn.close()

@app.patch("/update-listing/{listing_id}")
def update_listing(
    listing_id: Union[int, str],
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    price_per_day: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    current_user: str = Depends(verify_token),
):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT owner_email FROM listings WHERE id=%s AND deleted_at IS NULL", (str(listing_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        if row[0] != current_user and not is_admin(current_user):
            raise HTTPException(status_code=403, detail="Forbidden")

        fields = []
        values = []
        if name is not None: fields.append("name=%s"); values.append(name)
        if description is not None: fields.append("description=%s"); values.append(description)
        if location is not None: fields.append("location=%s"); values.append(outward_prefix(location))
        if price_per_day is not None:
            try: price_num = float(str(price_per_day).replace(",", ""))
            except Exception: raise HTTPException(status_code=400, detail="Invalid price")
            fields.append("price_per_day=%s"); values.append(price_num)
        if image_url is not None: fields.append("image_url=%s"); values.append(image_url)
        if not fields:
            return {"ok": True, "id": str(listing_id), "message": "No changes"}

        values.append(str(listing_id))
        cur.execute(f"UPDATE listings SET {', '.join(fields)} WHERE id=%s", values)
        conn.commit()
        return {"ok": True, "id": str(listing_id)}
    finally:
        cur.close(); conn.close()

@app.get("/my-listings")
def my_listings(current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, description, location, image_url,
                   price_per_day, owner_email, created_at, deleted_at
            FROM listings
            WHERE owner_email=%s AND deleted_at IS NULL
            ORDER BY created_at DESC
        """, (current_user,))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

# --- Admin soft-delete / restore / hard-delete ---

@app.post("/admin/listings/{listing_id}/soft-delete")
def admin_soft_delete_listing(listing_id: Union[int, str], current_user: str = Depends(verify_token)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE listings SET deleted_at=NOW() WHERE id=%s", (str(listing_id),))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Listing not found")
        conn.commit()
        return {"ok": True, "soft_deleted": str(listing_id)}
    finally:
        cur.close(); conn.close()

@app.post("/admin/listings/{listing_id}/restore")
def admin_restore_listing(listing_id: Union[int, str], current_user: str = Depends(verify_token)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE listings SET deleted_at=NULL WHERE id=%s", (str(listing_id),))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Listing not found")
        conn.commit()
        return {"ok": True, "restored": str(listing_id)}
    finally:
        cur.close(); conn.close()

@app.delete("/admin/listings/{listing_id}")
def admin_delete_listing(listing_id: Union[int, str], current_user: str = Depends(verify_token)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, owner_email FROM listings WHERE id=%s", (str(listing_id),))
        row = fetch_one_dict(cur)
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        owner_email = row.get("owner_email",""); name = row.get("name","")

        # Delete dependents first (manual cascade)
        cur.execute("DELETE FROM messages WHERE thread_id IN (SELECT id FROM message_threads WHERE listing_id=%s)", (str(listing_id),))
        cur.execute("DELETE FROM message_threads WHERE listing_id=%s", (str(listing_id),))
        cur.execute("DELETE FROM rental_requests WHERE listing_id=%s", (str(listing_id),))
        cur.execute("DELETE FROM rentals WHERE listing_id=%s", (str(listing_id),))

        cur.execute("DELETE FROM listings WHERE id=%s", (str(listing_id),))
        conn.commit()

        if owner_email:
            send_alert_email(owner_email, "Listing removed by moderator",
                             f"<p>Your listing <strong>{name}</strong> was removed for violating our guidelines.</p>")
        return {"ok": True, "deleted_listing_id": str(listing_id)}
    finally:
        cur.close(); conn.close()

@app.delete("/delete-listing/{listing_id}")
def delete_listing_fallback(listing_id: Union[int, str], current_user: str = Depends(verify_token)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    return admin_delete_listing(listing_id, current_user)

# ---------------------- Rental Requests ----------------------

def ensure_thread(cur, listing_id: str, renter_email: str, lister_email: str) -> None:
    cur.execute("""
        INSERT INTO message_threads (listing_id, renter_email, lister_email, is_unlocked, created_at)
        VALUES (%s, %s, %s, FALSE, NOW())
        ON CONFLICT (listing_id, renter_email, lister_email) DO NOTHING
    """, (listing_id, renter_email, lister_email))

@app.post("/request-to-rent")
def request_to_rent(payload: RequestToRentIn, current_user: str = Depends(verify_token)):
    renter_email = current_user
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="End date must be on/after start date")

    listing_id = str(payload.listing_id)

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, owner_email, price_per_day FROM listings WHERE id=%s AND deleted_at IS NULL", (listing_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        listing_name = row[1]; lister_email = row[2]; price_per_day = float(row[3] or 0)

        cur.execute("""
            INSERT INTO rental_requests (listing_id, renter_email, lister_email, start_date, end_date, status, request_time)
            VALUES (%s, %s, %s, %s, %s, 'requested', NOW())
            RETURNING id
        """, (listing_id, renter_email, lister_email, payload.start_date, payload.end_date))
        req_id = cur.fetchone()[0]

        ensure_thread(cur, listing_id, renter_email, lister_email)
        conn.commit()

        # Email lister (best-effort)
        star = renter_email[0] if renter_email else ""
        obfuscated = (star + "******@" + renter_email.split("@")[1]) if "@" in renter_email else renter_email
        html = f"""
            <p>You have a new rental request for <strong>{listing_name}</strong>.</p>
            <p>Renter: <strong>{obfuscated}</strong></p>
            <p>Dates: {payload.start_date} → {payload.end_date}</p>
            <p>Please review the request in your dashboard.</p>
        """
        send_alert_email(lister_email, "New rental request", html)

        return {"ok": True, "request_id": req_id, "listing_id": listing_id, "price_per_day": price_per_day}
    finally:
        cur.close(); conn.close()

@app.get("/my-rental-requests")
def my_rental_requests(
    role: Optional[str] = Query(None, description="renter|lister|all"),
    current_user: str = Depends(verify_token)
):
    role = (role or "all").lower()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if role == "renter":
            cur.execute("""
                SELECT rr.*, COALESCE(l.name,'') AS listing_name
                FROM rental_requests rr
                LEFT JOIN listings l ON l.id=rr.listing_id
                WHERE rr.renter_email=%s
                ORDER BY rr.request_time DESC
            """, (current_user,))
        elif role == "lister":
            cur.execute("""
                SELECT rr.*, COALESCE(l.name,'') AS listing_name
                FROM rental_requests rr
                LEFT JOIN listings l ON l.id=rr.listing_id
                WHERE rr.lister_email=%s
                ORDER BY rr.request_time DESC
            """, (current_user,))
        else:
            cur.execute("""
                SELECT rr.*, COALESCE(l.name,'') AS listing_name
                FROM rental_requests rr
                LEFT JOIN listings l ON l.id=rr.listing_id
                WHERE rr.renter_email=%s OR rr.lister_email=%s
                ORDER BY rr.request_time DESC
            """, (current_user, current_user))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

# ---------------------- Stripe Connect helpers ----------------------

def stripe_ready() -> bool:
    return bool(stripe_sdk and STRIPE_SECRET_KEY)

def stripe_set_key():
    if stripe_ready():
        stripe_sdk.api_key = STRIPE_SECRET_KEY

def get_connect_account_id(email: str) -> Optional[str]:
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT account_id FROM stripe_accounts WHERE email=%s", (email,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close(); conn.close()

def set_connect_account_id(email: str, account_id: str) -> None:
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO stripe_accounts (email, account_id)
            VALUES (%s, %s)
            ON CONFLICT (email) DO UPDATE SET account_id=EXCLUDED.account_id
        """, (email, account_id))
        conn.commit()
    finally:
        cur.close(); conn.close()

def require_lister_ready_for_payments(lister_email: str) -> Optional[str]:
    """
    Returns None if ready; returns onboarding URL if not ready.
    """
    if not stripe_ready():
        return None  # don't block when Stripe not configured
    stripe_set_key()
    acct_id = get_connect_account_id(lister_email)
    if not acct_id:
        acct = stripe_sdk.Account.create(type="express", email=lister_email)
        set_connect_account_id(lister_email, acct.id)
        link = stripe_sdk.AccountLink.create(
            account=acct.id,
            refresh_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=refresh",
            return_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=return",
            type="account_onboarding",
        )
        return link.url
    acct = stripe_sdk.Account.retrieve(acct_id)
    if not (acct.get("charges_enabled") and acct.get("details_submitted")):
        link = stripe_sdk.AccountLink.create(
            account=acct_id,
            refresh_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=refresh",
            return_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=return",
            type="account_onboarding",
        )
        return link.url
    return None

# ---------------------- Accept / Decline ----------------------

@app.post("/rental-requests/{request_id}/accept")
def accept_request(request_id: Union[int, str], payload: AcceptIn = Body(None), current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.*, l.name AS listing_name, l.price_per_day
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id=rr.listing_id
            WHERE rr.id=%s
        """, (str(request_id),))
        r = fetch_one_dict(cur)
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")
        if r["lister_email"] != current_user and not is_admin(current_user):
            raise HTTPException(status_code=403, detail="Only the lister can accept")

        # Enforce Stripe onboarding (only if Stripe configured)
        onboarding_url = require_lister_ready_for_payments(r["lister_email"])
        if onboarding_url:
            raise HTTPException(status_code=409, detail={"onboarding_url": onboarding_url})

        cur.execute("UPDATE rental_requests SET status='accepted', updated_at=NOW() WHERE id=%s", (str(request_id),))
        conn.commit()

        html = f"""
            <p>Your request for <strong>{r.get('listing_name','')}</strong> was accepted.</p>
            <p>Dates: {r.get('start_date')} → {r.get('end_date')}</p>
            <p>Proceed to payment from your dashboard.</p>
        """
        send_alert_email(r.get("renter_email",""), "Request accepted", html)
        return {"ok": True, "status": "accepted"}
    finally:
        cur.close(); conn.close()

@app.post("/rental-requests/{request_id}/decline")
def decline_request(request_id: Union[int, str], payload: DeclineIn = Body(...), current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.*, COALESCE(l.name,'') AS listing_name
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id=rr.listing_id
            WHERE rr.id=%s
        """, (str(request_id),))
        r = fetch_one_dict(cur)
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")
        if r["lister_email"] != current_user and not is_admin(current_user):
            raise HTTPException(status_code=403, detail="Only the lister can decline")

        cur.execute("UPDATE rental_requests SET status='declined', updated_at=NOW() WHERE id=%s", (str(request_id),))
        conn.commit()

        reason = (payload.reason or "").strip()
        html = f"""
            <p>The rental request for <strong>{r['listing_name']}</strong> has been declined.</p>
            {('<p><em>Reason:</em> ' + reason + '</p>') if reason else ''}
        """
        send_alert_email(r.get("renter_email",""), "Rental request declined", html)
        send_alert_email(r.get("lister_email",""), "You declined a rental request", html)

        return {"ok": True, "status": "declined"}
    finally:
        cur.close(); conn.close()

# ---------------------- Messaging (with server-side PII guard) ----------------------

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
URL_RE = re.compile(r"(https?://|www\.)", re.I)

def contains_pii(text: str) -> bool:
    t = text or ""
    if EMAIL_RE.search(t): return True
    if PHONE_RE.search(t): return True
    if URL_RE.search(t): return True
    return False

@app.get("/message-threads")
def list_threads(current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT mt.id, mt.listing_id, mt.renter_email, mt.lister_email, mt.is_unlocked, mt.created_at,
                   COALESCE(l.name,'') AS listing_name
            FROM message_threads mt
            LEFT JOIN listings l ON l.id=mt.listing_id
            WHERE mt.renter_email=%s OR mt.lister_email=%s
            ORDER BY mt.created_at DESC
        """, (current_user, current_user))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.get("/messages/{thread_id}")
def list_messages(thread_id: Union[int, str], current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT renter_email, lister_email, is_unlocked FROM message_threads WHERE id=%s", (str(thread_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")
        renter, lister, unlocked = row[0], row[1], bool(row[2])
        if current_user not in (renter, lister) and not is_admin(current_user):
            raise HTTPException(status_code=403, detail="Forbidden")

        cur.execute("""
            SELECT id, thread_id, sender_email, body, created_at, COALESCE(system,false) AS system
            FROM messages
            WHERE thread_id=%s
            ORDER BY created_at ASC
        """, (str(thread_id),))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.post("/messages/send")
def send_message(payload: MessageSendIn, current_user: str = Depends(verify_token)):
    thread_id = str(payload.thread_id).strip()
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Empty message")

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT renter_email, lister_email, is_unlocked FROM message_threads WHERE id=%s", (thread_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")

        renter, lister, unlocked = row[0], row[1], bool(row[2])
        if current_user not in (renter, lister):
            raise HTTPException(status_code=403, detail="Not a participant")
        if not unlocked:
            if contains_pii(body):
                raise HTTPException(status_code=403, detail="PII (email/phone/URL) not allowed before payment")
            # else allow non-PII prepayment messages if you want:
            # return {"ok": False, "message": "Messaging locked"}  # stricter variant
        cur.execute("""
            INSERT INTO messages (thread_id, sender_email, body, created_at, system)
            VALUES (%s, %s, %s, NOW(), FALSE)
        """, (thread_id, current_user, body))
        conn.commit()
        return {"ok": True}
    finally:
        cur.close(); conn.close()

# ---------------------- Admin ----------------------

@app.get("/admin/all-rental-requests")
def admin_all_rental_requests(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: str = Depends(verify_token)
):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT rr.id, rr.listing_id, COALESCE(l.name,'') AS listing_name,
                   rr.renter_email, rr.lister_email, rr.start_date, rr.end_date,
                   COALESCE(rr.status,'') AS status, COALESCE(rr.request_time,NOW()) AS request_time
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id = rr.listing_id
            ORDER BY rr.request_time DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

# ---------------------- Stripe ----------------------

@app.post("/stripe/create-onboarding-link")
def stripe_onboard(current_user: str = Depends(verify_token)):
    if not stripe_ready():
        raise HTTPException(status_code=409, detail="Stripe not configured")
    stripe_set_key()
    acct_id = get_connect_account_id(current_user)
    if not acct_id:
        acct = stripe_sdk.Account.create(type="express", email=current_user)
        set_connect_account_id(current_user, acct.id)
        acct_id = acct.id
    link = stripe_sdk.AccountLink.create(
        account=acct_id,
        refresh_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=refresh",
        return_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=return",
        type="account_onboarding",
    )
    return {"url": link.url, "account_id": acct_id}

@app.post("/stripe/create-checkout-session")
def stripe_checkout_start(payload: CheckoutStartIn, current_user: str = Depends(verify_token)):
    if not stripe_ready():
        raise HTTPException(status_code=409, detail="Stripe not configured")
    stripe_set_key()

    # Fetch request + listing
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.id, rr.listing_id, rr.renter_email, rr.lister_email, rr.start_date, rr.end_date,
                   COALESCE(l.name,'') AS listing_name, COALESCE(l.price_per_day,0) AS price_per_day
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id=rr.listing_id
            WHERE rr.id=%s
        """, (str(payload.request_id),))
        r = fetch_one_dict(cur)
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")

        # Only renter can pay
        if r["renter_email"] != current_user and not is_admin(current_user):
            raise HTTPException(status_code=403, detail="Only the renter can pay")

        # Lister must have/complete onboarding
        onboarding_url = require_lister_ready_for_payments(r["lister_email"])
        if onboarding_url:
            raise HTTPException(status_code=409, detail={"onboarding_url": onboarding_url})

        # Amounts
        days = (r["end_date"] - r["start_date"]).days + 1 if r["start_date"] and r["end_date"] else 1
        days = max(1, days)
        base_per_day = float(r.get("price_per_day", 0) or 0)
        base_total_p = int(round(base_per_day * days * 100))
        final_total_p = int(round(base_total_p * (1.0 + PLATFORM_FEE_RATE)))
        fee_p = final_total_p - base_total_p

        # Destination charge to lister's connected account
        lister_acct = get_connect_account_id(r["lister_email"])
        if not lister_acct:
            # should not happen because require_lister_ready_for_payments already created it
            onboarding_url = require_lister_ready_for_payments(r["lister_email"])
            raise HTTPException(status_code=409, detail={"onboarding_url": onboarding_url or "Connect account missing"})

        session = stripe_sdk.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {"name": f"{r['listing_name']} x {days} day(s)"},
                    "unit_amount": final_total_p,
                },
                "quantity": 1,
            }],
            success_url=f"{FRONTEND_BASE}/dashboard.html?paid={r['id']}",
            cancel_url=f"{FRONTEND_BASE}/dashboard.html?cancel={r['id']}",
            payment_intent_data={
                "transfer_data": {"destination": lister_acct},
                "application_fee_amount": fee_p,
                "metadata": {
                    "rental_request_id": str(r["id"]),
                    "listing_id": str(r["listing_id"]),
                    "renter_email": r["renter_email"],
                    "lister_email": r["lister_email"],
                    "days": str(days),
                    "base_total_pence": str(base_total_p),
                    "platform_fee_pence": str(fee_p),
                    "final_total_pence": str(final_total_p),
                },
            },
            metadata={
                "rental_request_id": str(r["id"]),
                "listing_id": str(r["listing_id"]),
            },
        )

        cur.execute("UPDATE rental_requests SET status='payment_initiated', updated_at=NOW() WHERE id=%s", (str(r["id"]),))
        conn.commit()
        return {"url": session.url}
    finally:
        cur.close(); conn.close()

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not stripe_ready() or not STRIPE_WEBHOOK_SECRET:
        return {"ok": True, "skipped": "stripe not configured"}

    stripe_set_key()
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe_sdk.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook")

    obj = event["data"]["object"]
    meta = obj.get("metadata", {}) if isinstance(obj, dict) else {}
    event_type = event.get("type")

    if event_type in ("checkout.session.completed", "payment_intent.succeeded"):
        # Collect metadata (may be on PI)
        if event_type == "checkout.session.completed" and "payment_intent" in obj and isinstance(obj["payment_intent"], str):
            pi = stripe_sdk.PaymentIntent.retrieve(obj["payment_intent"])
            meta = pi.get("metadata", {}) or meta
            amount_received = int(pi.get("amount_received", 0))
        else:
            amount_received = int(obj.get("amount_received", 0))

        req_id = meta.get("rental_request_id")
        listing_id = meta.get("listing_id")
        renter_email = meta.get("renter_email")
        lister_email = meta.get("lister_email")
        base_total_p = int(meta.get("base_total_pence", "0") or 0)
        fee_p = int(meta.get("platform_fee_pence", "0") or 0)
        final_total_p = int(meta.get("final_total_pence", str(amount_received) if amount_received else "0") or 0)

        if req_id and listing_id and renter_email and lister_email:
            conn = get_db_connection(); cur = conn.cursor()
            try:
                # Mark paid
                cur.execute("UPDATE rental_requests SET status='paid', updated_at=NOW() WHERE id=%s", (str(req_id),))
                # Unlock thread
                cur.execute("""
                    UPDATE message_threads
                    SET is_unlocked=TRUE
                    WHERE listing_id=%s AND renter_email=%s AND lister_email=%s
                """, (str(listing_id), renter_email, lister_email))
                # System message
                cur.execute("""
                    INSERT INTO messages (thread_id, sender_email, body, created_at, system)
                    SELECT id, %s, %s, NOW(), TRUE
                    FROM message_threads
                    WHERE listing_id=%s AND renter_email=%s AND lister_email=%s
                """, ("system@rentonomic", "Payment confirmed. Messaging unlocked.", str(listing_id), renter_email, lister_email))
                # Create rentals row (idempotent via UNIQUE on request_id)
                cur.execute("""
                    INSERT INTO rentals (request_id, listing_id, renter_email, lister_email,
                                         start_date, end_date, amount_base_pence, amount_renter_pence, platform_fee_pence)
                    SELECT rr.id, rr.listing_id, rr.renter_email, rr.lister_email,
                           rr.start_date, rr.end_date, %s, %s, %s
                    FROM rental_requests rr
                    WHERE rr.id=%s
                    ON CONFLICT (request_id) DO NOTHING
                """, (base_total_p, final_total_p, fee_p, str(req_id)))
                conn.commit()
            finally:
                try: cur.close(); conn.close()
                except Exception: pass

            # Email both parties (best-effort)
            try:
                send_alert_email(
                    renter_email,
                    "Payment received — booking confirmed",
                    "<p>Your payment was successful. The chat is now unlocked.</p>"
                )
                send_alert_email(
                    lister_email,
                    "Your item has been booked",
                    "<p>The renter has paid. You can now chat and arrange handover.</p>"
                )
            except Exception:
                pass

    return {"ok": True}

# ---------------------- Health ----------------------

@app.get("/")
def root():
    return {"ok": True, "service": "rentonomic-api"}










































