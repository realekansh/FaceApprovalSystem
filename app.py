"""
Face Approval System - FastAPI Backend
A secure face recognition platform for member access management
"""

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict
import secrets
import os
import asyncio

# ✅ NEW: Face recognition imports
import face_recognition
import cv2
import numpy as np
import base64
from io import BytesIO
from PIL import Image

# ========== CONFIGURATION ==========
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = "face_approval_system"
ADMIN_USERNAME = "root"
ADMIN_PASSWORD = "ssh"

# ========== PYDANTIC MODELS ==========
class FaceCaptureRequest(BaseModel):
    """Model for face capture requests"""
    face_image: str

class RegisterEntryRequest(BaseModel):
    """Model for new user registration"""
    model_config = ConfigDict(populate_by_name=True)
    name: str
    class_name: str = Field(..., alias='class')
    roll: str
    face_image: str = ""

class ApproveFaceRequest(BaseModel):
    """Model for face approval requests"""
    face_image: str

class EndSessionRequest(BaseModel):
    """Model for ending sessions"""
    session_id: str

class AdminLoginRequest(BaseModel):
    """Model for admin login"""
    username: str
    password: str

class DeleteUserRequest(BaseModel):
    """Model for deleting users"""
    name: str

class EditUserRequest(BaseModel):
    """Model for editing user information"""
    model_config = ConfigDict(populate_by_name=True)
    old_name: str
    name: str
    class_name: str = Field(..., alias='class')
    roll: str

# ========== GLOBAL VARIABLES ==========
mongodb_client: Optional[AsyncIOMotorClient] = None
database = None
registered_faces_collection = None
active_sessions_collection = None
console_logs_collection = None
temp_faces_collection = None

# Fallback in-memory storage
in_memory_storage = {
    'registered_faces': {},
    'active_sessions': {},
    'console_logs': [],
    'temp_faces': {}
}

use_mongodb = True

# ========== HELPER FUNCTIONS ==========
async def log_action(action: str) -> None:
    """Log action to MongoDB or in-memory storage"""
    try:
        timestamp = datetime.now()
        log_entry = {
            "timestamp": timestamp,
            "action": action,
            "formatted": f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {action}"
        }

        if use_mongodb and console_logs_collection is not None:
            await console_logs_collection.insert_one(log_entry)
            count = await console_logs_collection.count_documents({})
            if count > 100:
                oldest_logs = await console_logs_collection.find().sort("timestamp", 1).limit(count - 100).to_list(length=count)
                for log in oldest_logs:
                    await console_logs_collection.delete_one({"_id": log["_id"]})
        else:
            in_memory_storage['console_logs'].append(log_entry['formatted'])
            if len(in_memory_storage['console_logs']) > 100:
                in_memory_storage['console_logs'] = in_memory_storage['console_logs'][-100:]
    except Exception as e:
        print(f"⚠️ Error logging action: {e}")

def get_or_create_session_id(request: Request) -> str:
    """Get existing session ID from cookies or create new one"""
    session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = secrets.token_hex(16)
    return session_id

async def clear_temp_face(session_id: str) -> None:
    """Clear temporary face data for a session"""
    try:
        if use_mongodb and temp_faces_collection is not None:
            await temp_faces_collection.delete_one({"session_id": session_id})
        else:
            if session_id in in_memory_storage['temp_faces']:
                del in_memory_storage['temp_faces'][session_id]
    except Exception as e:
        print(f"⚠️ Error clearing temp face: {e}")

async def initialize_mongodb() -> bool:
    """Initialize MongoDB connection and collections"""
    global mongodb_client, database
    global registered_faces_collection, active_sessions_collection
    global console_logs_collection, temp_faces_collection

    try:
        mongodb_client = AsyncIOMotorClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
        database = mongodb_client[DATABASE_NAME]

        await database.command('ping')

        registered_faces_collection = database["registered_faces"]
        active_sessions_collection = database["active_sessions"]
        console_logs_collection = database["console_logs"]
        temp_faces_collection = database["temp_faces"]

        await registered_faces_collection.create_index("name", unique=True)
        await active_sessions_collection.create_index("name", unique=True)
        await active_sessions_collection.create_index("session_id", unique=True)
        await console_logs_collection.create_index("timestamp")
        await temp_faces_collection.create_index("session_id", unique=True)
        await temp_faces_collection.create_index("created_at", expireAfterSeconds=3600)

        print("\n" + "="*60)
        print("✅ Face Approval System Started Successfully!")
        print("="*60)
        print("🗄️ Database: MongoDB Connected")
        print(f"🌐 Server: http://localhost:8000")
        print(f"📚 API Docs: http://localhost:8000/docs")
        print(f"🔐 Admin: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
        print("="*60 + "\n")

        return True
    except Exception as e:
        print("\n" + "="*60)
        print("⚠️ MongoDB Connection Failed!")
        print(f"❌ Error: {e}")
        print("="*60)
        print("🗄️ Fallback: Using In-Memory Storage")
        print(f"🌐 Server: http://localhost:8000")
        print(f"📚 API Docs: http://localhost:8000/docs")
        print(f"🔐 Admin: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
        print("⚠️ Note: Data will be lost on server restart!")
        print("="*60 + "\n")

        return False

# ========== LIFESPAN MANAGEMENT ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    global use_mongodb

    use_mongodb = await initialize_mongodb()
    if use_mongodb:
        await log_action("=== SYSTEM STARTED WITH MONGODB ===")
    else:
        await log_action("=== SYSTEM STARTED WITH IN-MEMORY STORAGE ===")

    yield

    if mongodb_client and use_mongodb:
        await log_action("=== SYSTEM SHUTDOWN ===")
        mongodb_client.close()
        print("\n✅ MongoDB connection closed gracefully\n")

# ========== FASTAPI APP INITIALIZATION ==========
app = FastAPI(
    title="Face Approval System",
    description="Secure face recognition platform for member access management",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for directory in ["static", "templates"]:
    if not os.path.exists(directory):
        os.makedirs(directory)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ========== API ROUTES ==========
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render main dashboard page"""
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        return HTMLResponse(content=f"""
            <h1>✅ Backend is running successfully!</h1>
            <p>⚠️ Frontend template not found. Please create <code>templates/index.html</code></p>
            <p>🗄️ Storage Mode: <strong>{'MongoDB' if use_mongodb else 'In-Memory'}</strong></p>
        """)

@app.post("/api/capture-face")
async def capture_face(request: Request, data: FaceCaptureRequest):
    """✅ FIXED: Capture and validate face image with face detection"""
    try:
        face_image = data.face_image

        if not face_image or len(face_image) < 100:
            raise HTTPException(status_code=400, detail="Invalid face data - image too small or empty")

        try:
            if "base64," in face_image:
                face_image_data = face_image.split("base64,")[1]
            else:
                face_image_data = face_image

            image_bytes = base64.b64decode(face_image_data)
            nparr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if image is None:
                raise HTTPException(status_code=400, detail="Failed to decode image. Please try capturing again.")

            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        except Exception as decode_error:
            await log_action(f"ERROR: Image decode failed - {str(decode_error)}")
            raise HTTPException(status_code=400, detail=f"Image decoding error: {str(decode_error)}")

        # ✅ Detect faces
        face_locations = face_recognition.face_locations(rgb_image)

        if len(face_locations) == 0:
            await log_action("ERROR: No face detected in captured image")
            raise HTTPException(
                status_code=400,
                detail="No face detected in the image. Please ensure your face is clearly visible, well-lit, and centered in the camera."
            )

        if len(face_locations) > 1:
            await log_action(f"WARNING: Multiple faces detected ({len(face_locations)})")
            raise HTTPException(
                status_code=400,
                detail=f"Multiple faces detected ({len(face_locations)}). Please ensure only one person is in frame."
            )

        # ✅ Extract face encoding
        face_encodings = face_recognition.face_encodings(rgb_image, face_locations)

        if len(face_encodings) == 0:
            await log_action("ERROR: Failed to generate face encoding")
            raise HTTPException(status_code=400, detail="Failed to process face. Please try again with better lighting.")

        face_encoding = face_encodings[0].tolist()
        session_id = get_or_create_session_id(request)

        # ✅ Store both image and encoding
        if use_mongodb and temp_faces_collection is not None:
            await temp_faces_collection.update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "session_id": session_id,
                        "face_image": data.face_image,
                        "face_encoding": face_encoding,
                        "created_at": datetime.now()
                    }
                },
                upsert=True
            )
        else:
            in_memory_storage['temp_faces'][session_id] = {
                'face_image': data.face_image,
                'face_encoding': face_encoding,
                'created_at': datetime.now()
            }

        await log_action(f"✅ Face captured and validated for registration (Session: {session_id[:8]}...)")

        response = JSONResponse(content={"success": True, "message": "Face captured and validated successfully"})
        response.set_cookie(key="session_id", value=session_id, httponly=True, samesite="lax")

        return response

    except HTTPException:
        raise
    except Exception as e:
        await log_action(f"ERROR: Face capture failed - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Face capture error: {str(e)}")

@app.post("/api/clear-face")
async def clear_face(request: Request):
    """Clear temporary face data"""
    try:
        session_id = get_or_create_session_id(request)
        await clear_temp_face(session_id)
        return {"success": True, "message": "Face data cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/register-entry")
async def register_entry(request: Request, data: RegisterEntryRequest):
    """✅ FIXED: Register new user with validated face data"""
    try:
        name = data.name.strip()
        class_name = data.class_name.strip()
        roll = data.roll.strip()

        if not name or not class_name or not roll:
            raise HTTPException(status_code=400, detail="All fields are required (name, class, roll)")

        session_id = get_or_create_session_id(request)

        # ✅ Get face data AND encoding
        face_data = None
        face_encoding = None

        if use_mongodb and temp_faces_collection is not None:
            temp_face_doc = await temp_faces_collection.find_one({"session_id": session_id})
            if temp_face_doc:
                face_data = temp_face_doc.get("face_image")
                face_encoding = temp_face_doc.get("face_encoding")
        else:
            temp_face = in_memory_storage['temp_faces'].get(session_id, {})
            face_data = temp_face.get("face_image")
            face_encoding = temp_face.get("face_encoding")

        # ✅ Check if BOTH exist
        if not face_data or not face_encoding:
            raise HTTPException(
                status_code=400,
                detail="No face captured. Please capture your face first using the camera."
            )

        # Check if user exists
        if use_mongodb and registered_faces_collection is not None:
            existing_user = await registered_faces_collection.find_one({"name": name})
            if existing_user:
                raise HTTPException(status_code=400, detail=f"User '{name}' is already registered.")
        else:
            if name in in_memory_storage['registered_faces']:
                raise HTTPException(status_code=400, detail=f"User '{name}' is already registered.")

        code = secrets.token_hex(6).upper()

        # ✅ Store with face encoding
        user_document = {
            "name": name,
            "face_encoding": face_encoding,
            "face_image_preview": face_data[:500],
            "class": class_name,
            "roll": roll,
            "code": code,
            "registered_at": datetime.now()
        }

        if use_mongodb and registered_faces_collection is not None:
            await registered_faces_collection.insert_one(user_document)
        else:
            in_memory_storage['registered_faces'][name] = user_document

        await clear_temp_face(session_id)
        await log_action(f"✅ NEW REGISTRATION: {name} | Class: {class_name} | Roll: {roll} | Code: {code}")

        return {"success": True, "code": code, "name": name, "message": "Registration successful!"}

    except HTTPException:
        raise
    except Exception as e:
        await log_action(f"ERROR: Registration failed - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Registration error: {str(e)}")

@app.post("/api/approve-face")
async def approve_face(data: ApproveFaceRequest):
    """✅ FIXED: Approve face using face recognition matching"""
    try:
        face_image = data.face_image

        if not face_image or len(face_image) < 100:
            raise HTTPException(status_code=400, detail="Invalid face data received")

        try:
            if "base64," in face_image:
                face_image_data = face_image.split("base64,")[1]
            else:
                face_image_data = face_image

            image_bytes = base64.b64decode(face_image_data)
            nparr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if image is None:
                raise HTTPException(status_code=400, detail="Failed to decode image")

            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        except Exception as decode_error:
            await log_action(f"ERROR: Image decode failed - {str(decode_error)}")
            raise HTTPException(status_code=400, detail=f"Image decoding error: {str(decode_error)}")

        face_locations = face_recognition.face_locations(rgb_image)

        if len(face_locations) == 0:
            await log_action("ERROR: No face detected for approval")
            raise HTTPException(status_code=400, detail="No face detected. Please position your face clearly in front of the camera.")

        if len(face_locations) > 1:
            await log_action(f"WARNING: Multiple faces detected ({len(face_locations)}) for approval")
            raise HTTPException(status_code=400, detail=f"Multiple faces detected. Only one person should be in frame.")

        face_encodings = face_recognition.face_encodings(rgb_image, face_locations)

        if len(face_encodings) == 0:
            raise HTTPException(status_code=400, detail="Failed to process face")

        current_face_encoding = face_encodings[0]

        # ✅ Compare with registered faces
        matched_user = None
        best_match_distance = 1.0

        if use_mongodb and registered_faces_collection is not None:
            async for user in registered_faces_collection.find():
                if "face_encoding" in user:
                    stored_encoding = np.array(user["face_encoding"])
                    face_distance = face_recognition.face_distance([stored_encoding], current_face_encoding)[0]

                    if face_distance < 0.6 and face_distance < best_match_distance:
                        best_match_distance = face_distance
                        matched_user = user
        else:
            for name, user in in_memory_storage['registered_faces'].items():
                if "face_encoding" in user:
                    stored_encoding = np.array(user["face_encoding"])
                    face_distance = face_recognition.face_distance([stored_encoding], current_face_encoding)[0]

                    if face_distance < 0.6 and face_distance < best_match_distance:
                        best_match_distance = face_distance
                        matched_user = user

        if not matched_user:
            await log_action("❌ APPROVAL DENIED: Face not recognized")
            raise HTTPException(status_code=404, detail="Face not recognized. Please register first or try again.")

        session_id = secrets.token_hex(16)

        session_data = {
            "session_id": session_id,
            "name": matched_user["name"],
            "class": matched_user["class"],
            "roll": matched_user["roll"],
            "code": matched_user["code"],
            "start_time": datetime.now(),
            "match_confidence": round((1 - best_match_distance) * 100, 2)
        }

        if use_mongodb and active_sessions_collection is not None:
            await active_sessions_collection.delete_many({"name": matched_user["name"]})
            await active_sessions_collection.insert_one(session_data)
        else:
            to_remove = [sid for sid, sess in in_memory_storage['active_sessions'].items() 
                        if sess.get('name') == matched_user["name"]]
            for sid in to_remove:
                del in_memory_storage['active_sessions'][sid]
            in_memory_storage['active_sessions'][session_id] = session_data

        await log_action(
            f"✅ APPROVAL SUCCESS: {matched_user['name']} | "
            f"Class: {matched_user['class']} | Roll: {matched_user['roll']} | "
            f"Confidence: {session_data['match_confidence']}%"
        )

        return {
            "success": True,
            "session_id": session_id,
            "name": matched_user["name"],
            "class": matched_user["class"],
            "roll": matched_user["roll"],
            "code": matched_user["code"],
            "confidence": session_data['match_confidence']
        }

    except HTTPException:
        raise
    except Exception as e:
        await log_action(f"ERROR: Approval failed - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Approval error: {str(e)}")

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    """Get active session information"""
    try:
        if use_mongodb and active_sessions_collection is not None:
            session = await active_sessions_collection.find_one({"session_id": session_id})
            if session:
                session['_id'] = str(session['_id'])
                session['start_time'] = session['start_time'].isoformat()
                return session
        else:
            session = in_memory_storage['active_sessions'].get(session_id)
            if session:
                formatted_session = session.copy()
                formatted_session['start_time'] = session['start_time'].isoformat()
                return formatted_session

        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/end-session")
async def end_session(data: EndSessionRequest):
    """End an active session"""
    try:
        session_id = data.session_id

        if use_mongodb and active_sessions_collection is not None:
            result = await active_sessions_collection.delete_one({"session_id": session_id})
            if result.deleted_count == 0:
                raise HTTPException(status_code=404, detail="Session not found")
            await log_action(f"🔚 SESSION ENDED: {session_id[:8]}...")
        else:
            if session_id in in_memory_storage['active_sessions']:
                del in_memory_storage['active_sessions'][session_id]
                await log_action(f"🔚 SESSION ENDED: {session_id[:8]}...")
            else:
                raise HTTPException(status_code=404, detail="Session not found")

        return {"success": True, "message": "Session ended successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/login")
async def admin_login(data: AdminLoginRequest):
    """Admin login endpoint"""
    try:
        if data.username == ADMIN_USERNAME and data.password == ADMIN_PASSWORD:
            await log_action(f"🔐 ADMIN LOGIN: {data.username}")
            return {"success": True, "message": "Login successful"}
        else:
            await log_action(f"❌ FAILED ADMIN LOGIN ATTEMPT: {data.username}")
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/users")
async def get_all_users():
    """Get all registered users"""
    try:
        users = []

        if use_mongodb and registered_faces_collection is not None:
            async for user in registered_faces_collection.find():
                users.append({
                    "name": user["name"],
                    "class": user["class"],
                    "roll": user["roll"],
                    "code": user["code"],
                    "registered_at": user.get("registered_at", datetime.now()).isoformat()
                })
        else:
            for name, user in in_memory_storage['registered_faces'].items():
                users.append({
                    "name": user["name"],
                    "class": user["class"],
                    "roll": user["roll"],
                    "code": user["code"],
                    "registered_at": user.get("registered_at", datetime.now()).isoformat()
                })

        return {"users": users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/logs")
async def get_logs():
    """Get console logs"""
    try:
        logs = []

        if use_mongodb and console_logs_collection is not None:
            async for log in console_logs_collection.find().sort("timestamp", -1).limit(100):
                logs.append(log["formatted"])
        else:
            logs = list(reversed(in_memory_storage['console_logs'][-100:]))

        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/user")
async def delete_user(data: DeleteUserRequest):
    """Delete a registered user"""
    try:
        name = data.name

        if use_mongodb and registered_faces_collection is not None:
            result = await registered_faces_collection.delete_one({"name": name})
            if result.deleted_count == 0:
                raise HTTPException(status_code=404, detail="User not found")
        else:
            if name in in_memory_storage['registered_faces']:
                del in_memory_storage['registered_faces'][name]
            else:
                raise HTTPException(status_code=404, detail="User not found")

        await log_action(f"🗑️ USER DELETED: {name}")
        return {"success": True, "message": f"User '{name}' deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/user")
async def edit_user(data: EditUserRequest):
    """Edit user information"""
    try:
        old_name = data.old_name
        new_name = data.name.strip()
        new_class = data.class_name.strip()
        new_roll = data.roll.strip()

        if not new_name or not new_class or not new_roll:
            raise HTTPException(status_code=400, detail="All fields are required")

        if use_mongodb and registered_faces_collection is not None:
            user = await registered_faces_collection.find_one({"name": old_name})
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            if old_name != new_name:
                existing = await registered_faces_collection.find_one({"name": new_name})
                if existing:
                    raise HTTPException(status_code=400, detail=f"User '{new_name}' already exists")

            await registered_faces_collection.update_one(
                {"name": old_name},
                {"$set": {"name": new_name, "class": new_class, "roll": new_roll}}
            )
        else:
            if old_name not in in_memory_storage['registered_faces']:
                raise HTTPException(status_code=404, detail="User not found")

            if old_name != new_name and new_name in in_memory_storage['registered_faces']:
                raise HTTPException(status_code=400, detail=f"User '{new_name}' already exists")

            user = in_memory_storage['registered_faces'][old_name]
            user["name"] = new_name
            user["class"] = new_class
            user["roll"] = new_roll

            if old_name != new_name:
                in_memory_storage['registered_faces'][new_name] = user
                del in_memory_storage['registered_faces'][old_name]

        await log_action(f"✏️ USER EDITED: {old_name} → {new_name} | Class: {new_class} | Roll: {new_roll}")
        return {"success": True, "message": "User updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "storage": "mongodb" if use_mongodb else "in-memory",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
