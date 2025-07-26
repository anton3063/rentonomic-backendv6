from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
import bcrypt
import jwt
import uuid
import os
from datetime import datetime, timedelta

app = FastAPI()

# CORS (adjust for frontend domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database connection
DATABASE_URL = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# JWT config
JWT_SECRET = os.environ.get("JWT_SECRET", "secret123")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60

# === Models ===
class SignupData(BaseModel):
    email: str
    password: str

class LoginData(BaseModel):
    email: str
    password: str

# === Auth Middleware ===
class JWTBearer(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)

    async def __call__(self, request: Request):
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        if credentials:
            try:
                payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                request.state.user = payload
                return credentials.credentials
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token expired")
            except jwt.InvalidTokenError:
                raise HTTPException(status_code=401, detail="Invalid token")
        raise HTTPException(status_code=403, detail="Authorization required")

# === Utils ===
def create_token(email: str):
    expiration = datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES)
    payload = {"sub": email, "exp": expiration}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

# === Endpoints ===

@app.post("/signup")
def signup(data: SignupData):
    cursor.execute("SELECT * FROM users WHERE email = %s", (data.email,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt())
    cursor.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (
        str(uuid.uuid4()), data.email, hashed.decode()))
    conn.commit()

    # 🔐 Return token like login does
    token = create_token(data.email)
    return {"token": token}

@app.post("/login")
def login(data: LoginData):
    cursor.execute("SELECT password_hash FROM users WHERE email = %s", (data.email,))
    user = cursor.fetchone()
    if not user or not bcrypt.checkpw(data.password.encode(), user[0].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(data.email)
    return {"token": token}

@app.get("/me")
def get_me(request: Request, token: str = Depends(JWTBearer())):
    return {"email": request.state.user["sub"]}


























































