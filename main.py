from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import os
import shutil
import aiofiles
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import logging
from datetime import datetime, timedelta
import uuid
import secrets
from jose import JWTError, jwt
from passlib.context import CryptContext

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
BASE_URL = os.getenv("BASE_URL", "https://drive.aiwaverider.com")

# JWT Authentication
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# Authentication credentials
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# Ensure upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# JWT Authentication functions
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication not configured. AUTH_USERNAME and AUTH_PASSWORD must be set.",
        )
    
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return username
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

# Pydantic models
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class FolderCreate(BaseModel):
    name: str
    parent_path: str = ""

class FolderRename(BaseModel):
    old_name: str
    new_name: str
    parent_path: str = ""

class FileInfo(BaseModel):
    name: str
    path: str
    size: int
    modified: str
    type: str

class FolderStatus(BaseModel):
    path: str
    files: List[FileInfo]
    subfolders: List[str]

class WebhookResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

# Initialize folder structure
def initialize_folder_structure():
    """Initialize the predefined folder structure"""
    structure = {
        "videos": {
            "instagram": ["ai.waverider", "ai.wave.rider", "ai.uprise"],
            "tiktok": ["ai.waverider", "ai.wave.rider", "aiwaverider9", "health"]
        },
        "images": {
            "instagram": ["ai.waverider", "ai.wave.rider", "ai.uprise"],
            "tiktok": ["ai.waverider", "ai.wave.rider", "aiwaverider9", "health"]
        }
    }
    
    for main_folder, platforms in structure.items():
        main_path = os.path.join(UPLOAD_DIR, main_folder)
        os.makedirs(main_path, exist_ok=True)
        
        for platform, accounts in platforms.items():
            platform_path = os.path.join(main_path, platform)
            os.makedirs(platform_path, exist_ok=True)
            
            for account in accounts:
                account_path = os.path.join(platform_path, account)
                os.makedirs(account_path, exist_ok=True)
    
    logger.info("Folder structure initialized successfully")

# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    initialize_folder_structure()
    yield
    # Shutdown (if needed)
    pass

app = FastAPI(
    title="AI Wave Rider File Manager API",
    description="""
    ## 🚀 AI Wave Rider File Management System
    
    A comprehensive file management API with JWT authentication, designed for managing social media content across multiple platforms.
    
    ### 🔐 Authentication
    All protected endpoints require a JWT token obtained from `/auth/login`. Include the token in the Authorization header:
    ```
    Authorization: Bearer <your_jwt_token>
    ```
    
    ### 📁 Supported Platforms
    - **Instagram**: ai.waverider, ai.wave.rider, ai.uprise
    - **TikTok**: ai.waverider, ai.wave.rider, aiwaverider9, health
    
    ### 📂 Content Types
    - **Videos**: MP4, MOV, AVI, etc.
    - **Images**: JPG, PNG, GIF, WebP, etc.
    
    ### 🔒 Security Features
    - JWT-based authentication
    - Path traversal protection
    - File type validation
    - Secure file upload/download
    """,
    version="1.0.0",
    lifespan=lifespan,
    contact={
        "name": "AI Wave Rider Support",
        "url": "https://aiwaverider.com",
        "email": "support@aiwaverider.com",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://drive.aiwaverider.com",
        "https://www.drive.aiwaverider.com",
        "https://aiwaverider.com",
        "https://www.aiwaverider.com",
        "https://app.aiwaverider.com",
        "https://admin.aiwaverider.com",
        "https://api.aiwaverider.com",
        "http://localhost:3003",
        "http://127.0.0.1:3003",  # Development frontend
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Content-Language",
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "X-CSRF-Token",
        "X-API-Key",
    ],
)

# Security
security = HTTPBearer()

# Utility functions
def get_full_path(relative_path: str) -> str:
    """Get full path from relative path"""
    return os.path.join(UPLOAD_DIR, relative_path.lstrip("/"))

def is_safe_path(basedir: str, path: str) -> bool:
    """Check if path is safe (no directory traversal)"""
    return os.path.commonpath([basedir, os.path.abspath(path)]) == basedir

def get_file_info(file_path: str) -> FileInfo:
    """Get file information"""
    stat = os.stat(file_path)
    return FileInfo(
        name=os.path.basename(file_path),
        path=file_path.replace(UPLOAD_DIR, "").replace("\\", "/"),
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        type="file"
    )

def get_folder_contents(folder_path: str) -> FolderStatus:
    """Get folder contents including files and subfolders"""
    files = []
    subfolders = []
    
    logger.info(f"Getting contents for folder: {folder_path}")
    logger.info(f"UPLOAD_DIR: {UPLOAD_DIR}")
    
    try:
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path):
                files.append(get_file_info(item_path))
            elif os.path.isdir(item_path):
                subfolders.append(item)
    except Exception as e:
        logger.error(f"Error reading folder {folder_path}: {e}")
    
    relative_path = folder_path.replace(UPLOAD_DIR, "").replace("\\", "/")
    if not relative_path.startswith("/"):
        relative_path = "/" + relative_path
    
    logger.info(f"Relative path: {relative_path}")
    logger.info(f"Found {len(files)} files and {len(subfolders)} subfolders")
    
    return FolderStatus(
        path=relative_path,
        files=files,
        subfolders=subfolders
    )

# API Endpoints

@app.get("/", 
         tags=["🏠 System"],
         summary="API Root",
         description="Returns basic API information and version details.")
async def root():
    """
    ## 🏠 API Root Endpoint
    
    Returns basic information about the File Manager API including version and status.
    
    **Use Case**: Health check and API discovery
    **Authentication**: None required
    """
    return {"message": "File Manager API", "version": "1.0.0"}

@app.get("/health", 
         tags=["🏠 System"],
         summary="Health Check",
         description="Comprehensive health check endpoint for monitoring and load balancers.")
async def health_check():
    """
    ## 🏥 Health Check Endpoint
    
    Returns detailed health status including:
    - Application status
    - Timestamp
    - Version information
    - Upload directory path
    
    **Use Case**: 
    - Load balancer health checks
    - Monitoring systems
    - Docker health checks
    
    **Authentication**: None required
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "FIXED_VERSION_2025_09_11",
        "upload_dir": UPLOAD_DIR
    }

@app.get("/test-auth", 
         tags=["🔐 Authentication"],
         summary="Test Authentication",
         description="Test endpoint to verify JWT authentication is working correctly.")
async def test_auth(current_user: str = Depends(verify_token)):
    """
    ## 🔐 Authentication Test Endpoint
    
    Verifies that JWT authentication is working correctly and returns the authenticated user's username.
    
    **Use Case**: 
    - Testing authentication setup
    - Verifying token validity
    - Debugging auth issues
    
    **Authentication**: JWT token required
    **Headers**: `Authorization: Bearer <token>`
    """
    return {"message": f"Hello {current_user}, authentication is working!"}

@app.get("/test-simple", 
         tags=["🏠 System"],
         summary="Simple Test",
         description="Basic test endpoint without authentication requirements.")
async def test_simple():
    """
    ## 🧪 Simple Test Endpoint
    
    Basic test endpoint that doesn't require authentication.
    
    **Use Case**: 
    - API connectivity testing
    - Basic functionality verification
    - Development testing
    
    **Authentication**: None required
    """
    return {"message": "Simple test endpoint is working!"}

@app.get("/debug/env", 
         tags=["🔧 Debug"],
         summary="Environment Debug",
         description="Debug endpoint to check environment variables (sensitive values are masked).")
async def debug_environment():
    """
    ## 🔧 Environment Debug Endpoint
    
    Returns current environment configuration with sensitive values masked for security.
    
    **Use Case**: 
    - Debugging configuration issues
    - Verifying environment setup
    - Troubleshooting deployment problems
    
    **Authentication**: None required
    **Security**: Sensitive values are masked with `***`
    """
    return {
        "AUTH_USERNAME": AUTH_USERNAME,
        "AUTH_PASSWORD": "***" if AUTH_PASSWORD else None,
        "SECRET_KEY": "***" if SECRET_KEY else None,
        "BASE_URL": BASE_URL,
        "UPLOAD_DIR": UPLOAD_DIR
    }

@app.post("/auth/login", 
          response_model=TokenResponse,
          tags=["🔐 Authentication"],
          summary="User Login",
          description="Authenticate user and receive JWT token for API access.")
async def login(login_data: LoginRequest):
    """
    ## 🔐 User Login Endpoint
    
    Authenticates a user with username and password, returning a JWT token for subsequent API calls.
    
    **Request Body**:
    - `username`: User's username
    - `password`: User's password
    
    **Response**:
    - `access_token`: JWT token for authentication
    - `token_type`: Always "bearer"
    - `expires_in`: Token expiration time in seconds (1800 = 30 minutes)
    
    **Use Case**: 
    - Initial user authentication
    - Getting API access token
    - Session management
    
    **Authentication**: None required (this is the login endpoint)
    **Token Expiry**: 30 minutes
    """
    # Debug logging
    logger.info(f"Login attempt for username: {login_data.username}")
    logger.info(f"AUTH_USERNAME from env: {AUTH_USERNAME}")
    logger.info(f"AUTH_PASSWORD from env: {'***' if AUTH_PASSWORD else 'None'}")
    
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        logger.error("Authentication not configured - missing AUTH_USERNAME or AUTH_PASSWORD")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication not configured. AUTH_USERNAME and AUTH_PASSWORD must be set.",
        )
    
    # Verify credentials
    if not (secrets.compare_digest(login_data.username, AUTH_USERNAME) and 
            secrets.compare_digest(login_data.password, AUTH_PASSWORD)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": login_data.username}, expires_delta=access_token_expires
    )
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


# Folder Management
@app.post("/api/folders", 
          response_model=WebhookResponse,
          tags=["📁 Folder Management"],
          summary="Create Folder",
          description="Create a new folder in the specified parent directory.")
async def create_folder(folder: FolderCreate, current_user: str = Depends(verify_token)):
    """
    ## 📁 Create Folder Endpoint
    
    Creates a new folder in the specified parent directory. If no parent_path is provided, creates in root.
    
    **Request Body**:
    - `name`: Name of the new folder
    - `parent_path`: Parent directory path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains the new folder path
    
    **Use Case**: 
    - Organizing content by platform/account
    - Creating project directories
    - Setting up folder structure
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        # Handle empty parent_path (root)
        if not folder.parent_path or folder.parent_path.strip() == "":
            parent_path = UPLOAD_DIR
        else:
            parent_path = get_full_path(folder.parent_path)
        
        new_folder_path = os.path.join(parent_path, folder.name)
        
        if not is_safe_path(UPLOAD_DIR, new_folder_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        os.makedirs(new_folder_path, exist_ok=True)
        logger.info(f"Created folder: {new_folder_path}")
        
        return WebhookResponse(
            success=True,
            message=f"Folder '{folder.name}' created successfully",
            data={"path": new_folder_path.replace(UPLOAD_DIR, "").replace("\\", "/")}
        )
    except Exception as e:
        logger.error(f"Error creating folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/folders", 
            tags=["📁 Folder Management"],
            summary="Delete Folder",
            description="Delete a folder and all its contents recursively.")
async def delete_folder(folder_path: str, current_user: str = Depends(verify_token)):
    """
    ## 🗑️ Delete Folder Endpoint
    
    Permanently deletes a folder and all its contents (files and subfolders).
    
    **Query Parameters**:
    - `folder_path`: Path to the folder to delete
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    
    **Use Case**: 
    - Cleaning up old content
    - Removing unused directories
    - Content management
    
    **Authentication**: JWT token required
    **Warning**: This action is irreversible
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        full_path = get_full_path(folder_path)
        
        if not is_safe_path(UPLOAD_DIR, full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="Folder not found")
        
        shutil.rmtree(full_path)
        logger.info(f"Deleted folder: {full_path}")
        
        return WebhookResponse(
            success=True,
            message=f"Folder '{folder_path}' deleted successfully"
        )
    except Exception as e:
        logger.error(f"Error deleting folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/folders/rename", 
          response_model=WebhookResponse,
          tags=["📁 Folder Management"],
          summary="Rename Folder",
          description="Rename an existing folder.")
async def rename_folder(rename_data: FolderRename, current_user: str = Depends(verify_token)):
    """
    ## 📝 Rename Folder Endpoint
    
    Renames an existing folder to a new name within the same parent directory.
    
    **Request Body**:
    - `old_name`: Current name of the folder
    - `new_name`: New name for the folder
    - `parent_path`: Parent directory path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    
    **Use Case**: 
    - Updating folder names
    - Content organization
    - Project restructuring
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        parent_path = get_full_path(rename_data.parent_path)
        old_path = os.path.join(parent_path, rename_data.old_name)
        new_path = os.path.join(parent_path, rename_data.new_name)
        
        if not is_safe_path(UPLOAD_DIR, old_path) or not is_safe_path(UPLOAD_DIR, new_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(old_path):
            raise HTTPException(status_code=404, detail="Folder not found")
        
        os.rename(old_path, new_path)
        logger.info(f"Renamed folder from {old_path} to {new_path}")
        
        return WebhookResponse(
            success=True,
            message=f"Folder renamed from '{rename_data.old_name}' to '{rename_data.new_name}'"
        )
    except Exception as e:
        logger.error(f"Error renaming folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# File Management
@app.post("/api/files/upload",
          tags=["📄 File Management"],
          summary="Upload File",
          description="Upload a file to the specified folder with automatic duplicate handling.")
async def upload_file(
    file: UploadFile = File(...),
    folder_path: str = Form(""),
    webhook: bool = Form(False),
    current_user: str = Depends(verify_token)
):
    """
    ## 📤 Upload File Endpoint
    
    Uploads a file to the specified folder. If a file with the same name exists, 
    it automatically appends a number to create a unique filename.
    
    **Form Data**:
    - `file`: The file to upload (multipart/form-data)
    - `folder_path`: Target folder path (optional, defaults to root)
    - `webhook`: Return webhook response format (optional, defaults to false)
    
    **Response**:
    - `filename`: Name of the uploaded file
    - `path`: Relative path to the uploaded file
    - `size`: File size in bytes
    - `url`: Direct download URL for the file
    
    **Use Case**: 
    - Uploading social media content
    - Adding new files to projects
    - Content management workflows
    
    **Authentication**: JWT token required
    **File Types**: All file types supported
    **Duplicate Handling**: Automatic filename conflict resolution
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        target_folder = get_full_path(folder_path)
        
        if not is_safe_path(UPLOAD_DIR, target_folder):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        os.makedirs(target_folder, exist_ok=True)
        
        # Generate unique filename if file exists
        file_path = os.path.join(target_folder, file.filename)
        counter = 1
        while os.path.exists(file_path):
            name, ext = os.path.splitext(file.filename)
            file_path = os.path.join(target_folder, f"{name}_{counter}{ext}")
            counter += 1
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        relative_path = file_path.replace(UPLOAD_DIR, '').replace('\\', '/')
        file_url = f"{BASE_URL}/api/files/download{relative_path}"
        
        logger.info(f"Uploaded file: {file_path}")
        
        response_data = {
            "filename": os.path.basename(file_path),
            "path": file_path.replace(UPLOAD_DIR, "").replace("\\", "/"),
            "size": len(content),
            "url": file_url
        }
        
        if webhook:
            return WebhookResponse(
                success=True,
                message="File uploaded successfully",
                data=response_data
            )
        else:
            return response_data
            
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/download/{file_path:path}",
          tags=["📄 File Management"],
          summary="Download File",
          description="Download a file by its path with proper MIME type handling.")
async def download_file(file_path: str, current_user: str = Depends(verify_token)):
    """
    ## 📥 Download File Endpoint
    
    Downloads a file by its relative path. Returns the file with appropriate MIME type headers.
    
    **Path Parameters**:
    - `file_path`: Relative path to the file (e.g., `/videos/instagram/ai.waverider/video.mp4`)
    
    **Response**:
    - File content with appropriate Content-Type header
    - File name in Content-Disposition header
    
    **Use Case**: 
    - Accessing uploaded content
    - Serving files to frontend applications
    - Direct file downloads
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    **MIME Types**: Automatically detected based on file extension
    """
    try:
        full_path = get_full_path(file_path)
        
        if not is_safe_path(UPLOAD_DIR, full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        return FileResponse(full_path)
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/files",
            tags=["📄 File Management"],
            summary="Delete File",
            description="Delete a file by its path.")
async def delete_file(file_path: str, webhook: bool = False, current_user: str = Depends(verify_token)):
    """
    ## 🗑️ Delete File Endpoint
    
    Permanently deletes a file from the storage.
    
    **Query Parameters**:
    - `file_path`: Path to the file to delete
    - `webhook`: Return webhook response format (optional, defaults to false)
    
    **Response**:
    - `success`: Boolean indicating success (webhook format)
    - `message`: Success/error message (webhook format)
    - OR simple success message (standard format)
    
    **Use Case**: 
    - Removing outdated content
    - Cleaning up storage
    - Content lifecycle management
    
    **Authentication**: JWT token required
    **Warning**: This action is irreversible
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        full_path = get_full_path(file_path)
        
        if not is_safe_path(UPLOAD_DIR, full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        os.remove(full_path)
        logger.info(f"Deleted file: {full_path}")
        
        if webhook:
            return WebhookResponse(
                success=True,
                message="File deleted successfully"
            )
        else:
            return {"message": "File deleted successfully"}
            
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/files/rename",
          tags=["📄 File Management"],
          summary="Rename File",
          description="Rename an existing file.")
async def rename_file(
    old_path: str,
    new_name: str,
    webhook: bool = False,
    current_user: str = Depends(verify_token)
):
    """
    ## 📝 Rename File Endpoint
    
    Renames an existing file to a new name within the same directory.
    
    **Query Parameters**:
    - `old_path`: Current path to the file
    - `new_name`: New name for the file
    - `webhook`: Return webhook response format (optional, defaults to false)
    
    **Response**:
    - `success`: Boolean indicating success (webhook format)
    - `message`: Success/error message (webhook format)
    - `data`: Contains new file path (webhook format)
    - OR simple success message (standard format)
    
    **Use Case**: 
    - Updating file names
    - Content organization
    - File management workflows
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        old_full_path = get_full_path(old_path)
        new_full_path = os.path.join(os.path.dirname(old_full_path), new_name)
        
        if not is_safe_path(UPLOAD_DIR, old_full_path) or not is_safe_path(UPLOAD_DIR, new_full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(old_full_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        os.rename(old_full_path, new_full_path)
        logger.info(f"Renamed file from {old_full_path} to {new_full_path}")
        
        if webhook:
            return WebhookResponse(
                success=True,
                message="File renamed successfully",
                data={"new_path": new_full_path.replace(UPLOAD_DIR, "").replace("\\", "/")}
            )
        else:
            return {"message": "File renamed successfully"}
            
    except Exception as e:
        logger.error(f"Error renaming file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Status and listing endpoints
@app.get("/api/folders/status", 
         response_model=FolderStatus,
         tags=["📊 Status & Discovery"],
         summary="Get Folder Status",
         description="Get detailed information about a folder including files and subfolders.")
async def get_folder_status(folder_path: str = "", current_user: str = Depends(verify_token)):
    """
    ## 📊 Folder Status Endpoint
    
    Returns detailed information about a folder including all files and subfolders.
    
    **Query Parameters**:
    - `folder_path`: Path to the folder (optional, defaults to root)
    
    **Response**:
    - `path`: Relative path to the folder
    - `files`: Array of file information objects
    - `subfolders`: Array of subfolder names
    
    **File Information**:
    - `name`: File name
    - `path`: Relative file path
    - `size`: File size in bytes
    - `modified`: Last modified timestamp (ISO format)
    - `type`: Always "file"
    
    **Use Case**: 
    - Browsing folder contents
    - Building file explorers
    - Content discovery
    - Dashboard displays
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        logger.info(f"get_folder_status called with folder_path: '{folder_path}'")
        
        # Handle empty path (root)
        if not folder_path or folder_path.strip() == "":
            full_path = UPLOAD_DIR
        else:
            full_path = get_full_path(folder_path)
        
        logger.info(f"get_full_path result: '{full_path}'")
        logger.info(f"UPLOAD_DIR: '{UPLOAD_DIR}'")
        
        if not is_safe_path(UPLOAD_DIR, full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(full_path):
            logger.warning(f"Path does not exist: '{full_path}'")
            raise HTTPException(status_code=404, detail="Folder not found")
        
        logger.info(f"Path exists, calling get_folder_contents with: '{full_path}'")
        return get_folder_contents(full_path)
    except Exception as e:
        logger.error(f"Error getting folder status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/list",
         tags=["📊 Status & Discovery"],
         summary="List All Files",
         description="List all files recursively in a folder and its subfolders.")
async def list_all_files(folder_path: str = "", current_user: str = Depends(verify_token)):
    """
    ## 📋 List All Files Endpoint
    
    Recursively lists all files in a folder and its subfolders.
    
    **Query Parameters**:
    - `folder_path`: Path to the folder (optional, defaults to root)
    
    **Response**:
    - `files`: Array of all file information objects
    - `count`: Total number of files found
    
    **File Information**:
    - `name`: File name
    - `path`: Relative file path
    - `size`: File size in bytes
    - `modified`: Last modified timestamp (ISO format)
    - `type`: Always "file"
    
    **Use Case**: 
    - Search functionality
    - File indexing
    - Content analysis
    - Bulk operations
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    **Performance**: May be slow for large directory structures
    """
    try:
        full_path = get_full_path(folder_path)
        
        if not is_safe_path(UPLOAD_DIR, full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="Folder not found")
        
        files = []
        for root, dirs, filenames in os.walk(full_path):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                files.append(get_file_info(file_path))
        
        return {"files": files, "count": len(files)}
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Webhook endpoints

@app.post("/webhook/files/upload", 
          response_model=WebhookResponse,
          tags=["🔗 Webhooks"],
          summary="Webhook File Upload",
          description="Webhook endpoint for file upload with standardized response format.")
async def webhook_upload_file(
    file: UploadFile = File(...),
    folder_path: str = Form(""),
    current_user: str = Depends(verify_token)
):
    """
    ## 🔗 Webhook File Upload Endpoint
    
    Webhook version of file upload that returns a standardized webhook response format.
    
    **Form Data**:
    - `file`: The file to upload (multipart/form-data)
    - `folder_path`: Target folder path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains file information (filename, path, size, url)
    
    **Use Case**: 
    - Third-party integrations
    - Automated workflows
    - External system notifications
    - Webhook-based file processing
    
    **Authentication**: JWT token required
    **Response Format**: Standardized webhook format
    """
    return await upload_file(file, folder_path, webhook=True)

@app.post("/webhook/files/delete", 
          response_model=WebhookResponse,
          tags=["🔗 Webhooks"],
          summary="Webhook File Delete",
          description="Webhook endpoint for file deletion with standardized response format.")
async def webhook_delete_file(file_path: str = Form(...), current_user: str = Depends(verify_token)):
    """Webhook for file deletion"""
    return await delete_file(file_path, webhook=True)

@app.post("/webhook/files/rename", 
          response_model=WebhookResponse,
          tags=["🔗 Webhooks"],
          summary="Webhook File Rename",
          description="Webhook endpoint for file renaming with standardized response format.")
async def webhook_rename_file(
    old_path: str = Form(...),
    new_name: str = Form(...),
    current_user: str = Depends(verify_token)
):
    """Webhook for file rename"""
    return await rename_file(old_path, new_name, webhook=True)

@app.post("/webhook/folders/create", 
          response_model=WebhookResponse,
          tags=["🔗 Webhooks"],
          summary="Webhook Folder Create",
          description="Webhook endpoint for folder creation with standardized response format.")
async def webhook_create_folder(folder: FolderCreate, current_user: str = Depends(verify_token)):
    """Webhook for folder creation"""
    return await create_folder(folder)

@app.post("/webhook/folders/delete", 
          response_model=WebhookResponse,
          tags=["🔗 Webhooks"],
          summary="Webhook Folder Delete",
          description="Webhook endpoint for folder deletion with standardized response format.")
async def webhook_delete_folder(folder_path: str = Form(...), current_user: str = Depends(verify_token)):
    """Webhook for folder deletion"""
    return await delete_folder(folder_path)

@app.post("/webhook/folders/rename", 
          response_model=WebhookResponse,
          tags=["🔗 Webhooks"],
          summary="Webhook Folder Rename",
          description="Webhook endpoint for folder renaming with standardized response format.")
async def webhook_rename_folder(rename_data: FolderRename, current_user: str = Depends(verify_token)):
    """Webhook for folder rename"""
    return await rename_folder(rename_data)

@app.get("/webhook/folders/status", 
         response_model=WebhookResponse,
         tags=["🔗 Webhooks"],
         summary="Webhook Folder Status",
         description="Webhook endpoint for folder status with standardized response format.")
async def webhook_folder_status(folder_path: str = "", current_user: str = Depends(verify_token)):
    """Webhook for folder status"""
    try:
        status_data = await get_folder_status(folder_path)
        return WebhookResponse(
            success=True,
            message="Folder status retrieved successfully",
            data=status_data.dict()
        )
    except Exception as e:
        logger.error(f"Error in webhook folder status: {e}")
        return WebhookResponse(
            success=False,
            message=str(e)
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
