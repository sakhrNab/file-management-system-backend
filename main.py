from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import os
import shutil
import aiofiles
import json
import logging
import psutil
import time
import gc
import asyncio
import zipfile
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import logging
from datetime import datetime, timedelta
import uuid
import secrets
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
import signal
import sys

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
BASE_URL = os.getenv("BASE_URL", "https://drive.aiwaverider.com")

# Upload configuration
MAX_FILE_SIZE = 250 * 1024 * 1024  # 250MB limit
CHUNK_SIZE = 1024 * 1024  # 1MB chunks
TEMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "temp_chunks")
MAX_CONCURRENT_UPLOADS = 5  # Limit concurrent uploads to prevent memory exhaustion
MEMORY_THRESHOLD = 95  # Memory usage threshold in percentage
MEMORY_LEAK_THRESHOLD = 98  # Threshold for memory leak detection

# JWT Authentication
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 180 * 24 * 60  # 6 months in minutes

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# Authentication credentials
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# Ensure upload directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)

# Folder visibility management - uses ONLY environment variables (.env)
# Configure PUBLIC_FOLDERS in .env file or Coolify environment variables
def load_folder_visibility() -> Dict[str, bool]:
    """
    Load folder visibility settings from environment variable PUBLIC_FOLDERS.
    Format: Comma-separated list of folder paths (e.g., "videos/public,videos/instagram/ai.waverider")
    """
    visibility_map = {}
    
    # Read from environment variable PUBLIC_FOLDERS
    public_folders_env = os.getenv("PUBLIC_FOLDERS", "").strip()
    if public_folders_env:
        # Parse comma-separated list of public folders
        public_folders = [folder.strip() for folder in public_folders_env.split(",") if folder.strip()]
        
        for folder_path in public_folders:
            normalized_path = folder_path.replace("\\", "/").strip("/")
            # Validate that only videos folder can be public
            if normalized_path.startswith("videos"):
                visibility_map[normalized_path] = True
                logger.info(f"Set folder '{normalized_path}' as public from PUBLIC_FOLDERS env var")
            else:
                logger.warning(f"Skipping '{normalized_path}' from PUBLIC_FOLDERS - only videos folders can be public")
        
        if public_folders:
            logger.info(f"Loaded {len(public_folders)} public folders from PUBLIC_FOLDERS environment variable")
    else:
        logger.info("PUBLIC_FOLDERS environment variable not set - no public folders configured")
    
    return visibility_map

def is_folder_public(folder_path: str) -> bool:
    """
    Check if a folder is marked as public.
    Returns True if the folder itself is public, or if any parent folder is public.
    This means subfolders of public folders are automatically public.
    """
    visibility_map = load_folder_visibility()
    # Normalize path for comparison
    normalized_path = folder_path.replace("\\", "/").strip("/")
    
    # Check if this exact folder is public
    if visibility_map.get(normalized_path, False):
        return True
    
    # Check if any parent folder is public (subfolders inherit public status)
    path_parts = normalized_path.split("/")
    for i in range(len(path_parts)):
        # Build parent path by joining parts up to current index
        parent_path = "/".join(path_parts[:i+1])
        if visibility_map.get(parent_path, False):
            return True
    
    return False

# Global state for resource management
active_uploads = set()
upload_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)

# Background task management
background_tasks = set()
cleanup_task = None

# Graceful shutdown handler
def signal_handler(signum, frame):
    """Handle graceful shutdown"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    # Clean up resources
    cleanup_all_temp_chunks()
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

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

class FileMove(BaseModel):
    file_path: str
    destination_folder: str = ""

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

class ChunkUploadRequest(BaseModel):
    filename: str
    total_chunks: int
    chunk_number: int
    folder_path: str = ""

class ChunkUploadResponse(BaseModel):
    success: bool
    message: str
    chunk_received: int
    total_chunks: int
    upload_id: str
    data: Optional[Dict[str, Any]] = None

class ChunkCompleteRequest(BaseModel):
    upload_id: str
    filename: str
    total_chunks: int
    folder_path: str = ""

class DuplicateFileInfo(BaseModel):
    filename: str
    path: str
    size: int
    modified: str
    url: str

class DuplicateFileResponse(BaseModel):
    error: str
    message: str
    duplicate_info: DuplicateFileInfo
    suggested_action: str

class BulkDownloadRequest(BaseModel):
    file_paths: List[str]
    archive_name: str = "download.zip"

# Initialize folder structure
def initialize_folder_structure():
    """Initialize the predefined folder structure"""
    structure = {
        "videos": {
            "instagram": ["ai.waverider", "ai.wave.rider", "ai.uprise"],
            "tiktok": ["ai.waverider", "ai.wave.rider", "aiwaverider9", "health"],
            "public": []  # Public videos folder - visibility controlled by PUBLIC_FOLDERS env var
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
            
            # Handle public folder specially (it has empty accounts list)
            if platform == "public" and main_folder == "videos":
                # Public folder is created - visibility is controlled by PUBLIC_FOLDERS env var
                public_folder_path = "videos/public"
                if is_folder_public(public_folder_path):
                    logger.info(f"Public folder '{public_folder_path}' configured via PUBLIC_FOLDERS env var")
                else:
                    logger.info(f"Created '{public_folder_path}' folder - set PUBLIC_FOLDERS env var to make it public")
            else:
                # Create account folders
                for account in accounts:
                    account_path = os.path.join(platform_path, account)
                    os.makedirs(account_path, exist_ok=True)
    
    logger.info("Folder structure initialized successfully")

# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        logger.info("Starting application...")
        initialize_folder_structure()
        
        # Clean up any orphaned temp chunks on startup
        cleanup_orphaned_chunks()
        
        # Start background tasks
        await start_background_tasks()
        
        logger.info("Application started successfully")
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise
    
    yield
    
    # Shutdown
    try:
        logger.info("Shutting down application...")
        
        # Stop background tasks first
        await stop_background_tasks()
        
        # Clean up any remaining temp chunks
        cleanup_all_temp_chunks()
        
        logger.info("Application shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

def cleanup_orphaned_chunks():
    """Clean up orphaned chunk files on startup"""
    try:
        if not os.path.exists(TEMP_UPLOAD_DIR):
            return
        
        current_time = time.time()
        orphaned_count = 0
        
        for filename in os.listdir(TEMP_UPLOAD_DIR):
            if filename.endswith('_chunk_'):
                file_path = os.path.join(TEMP_UPLOAD_DIR, filename)
                # Remove chunks older than 1 hour
                if current_time - os.path.getmtime(file_path) > 3600:
                    os.remove(file_path)
                    orphaned_count += 1
        
        if orphaned_count > 0:
            logger.info(f"Cleaned up {orphaned_count} orphaned chunk files")
    except Exception as e:
        logger.error(f"Error cleaning up orphaned chunks: {e}")

def cleanup_all_temp_chunks():
    """Clean up all temporary chunk files"""
    try:
        if os.path.exists(TEMP_UPLOAD_DIR):
            for filename in os.listdir(TEMP_UPLOAD_DIR):
                if filename.endswith('_chunk_'):
                    file_path = os.path.join(TEMP_UPLOAD_DIR, filename)
                    os.remove(file_path)
            logger.info("Cleaned up all temporary chunk files")
    except Exception as e:
        logger.error(f"Error cleaning up temp chunks: {e}")

def check_memory_usage():
    """Check current memory usage and return percentage"""
    try:
        memory = psutil.virtual_memory()
        return memory.percent
    except Exception as e:
        logger.error(f"Error checking memory usage: {e}")
        return 0

def force_garbage_collection():
    """Force garbage collection to free memory"""
    try:
        # Clear any cached data first
        if 'app' in globals():
            # Clear any cached responses or data
            pass
        
        # Force garbage collection multiple times
        collected = 0
        for i in range(3):  # Run GC multiple times
            collected += gc.collect()
        
        # Force collection of all generations
        gc.collect(0)  # Generation 0
        gc.collect(1)  # Generation 1  
        gc.collect(2)  # Generation 2
        
        logger.info(f"Garbage collection freed {collected} objects")
        return collected
    except Exception as e:
        logger.error(f"Error during garbage collection: {e}")
        return 0

def detect_memory_leak():
    """Detect if there's a memory leak by checking if GC is not freeing objects"""
    try:
        # Get initial memory
        initial_memory = check_memory_usage()
        
        # Force garbage collection
        collected = force_garbage_collection()
        
        # Get memory after GC
        final_memory = check_memory_usage()
        
        # If memory is high and GC freed 0 objects, it's likely a leak
        if initial_memory > MEMORY_LEAK_THRESHOLD and collected == 0:
            logger.error(f"POTENTIAL MEMORY LEAK DETECTED: Memory {initial_memory}%, GC freed {collected} objects")
            return True
        elif initial_memory > MEMORY_LEAK_THRESHOLD:
            logger.warning(f"High memory usage: {initial_memory}% -> {final_memory}% (freed {collected} objects)")
        
        return False
    except Exception as e:
        logger.error(f"Error detecting memory leak: {e}")
        return False

async def background_cleanup_task():
    """Background task to periodically clean up resources"""
    logger.info("Starting background cleanup task...")
    
    while True:
        try:
            # Wait 5 minutes for more frequent cleanup
            await asyncio.sleep(300)  # 5 minutes
            
            logger.info("Running periodic cleanup...")
            
            # Clean up orphaned chunks
            cleanup_orphaned_chunks()
            
            # Force garbage collection
            collected = force_garbage_collection()
            
            # Check for memory leaks
            is_leak = detect_memory_leak()
            
            # Check memory usage and log if high
            memory_percent = check_memory_usage()
            if memory_percent > MEMORY_THRESHOLD:
                logger.warning(f"High memory usage during cleanup: {memory_percent}%")
                
                # If memory is still high, try more aggressive cleanup
                logger.warning("Attempting aggressive memory cleanup...")
                
                # Clear any cached data
                if hasattr(app, 'state'):
                    app.state.clear()
                
                # Force more aggressive GC
                for _ in range(5):
                    gc.collect()
                
                # Log final memory usage
                final_memory = check_memory_usage()
                logger.info(f"Aggressive cleanup: {memory_percent}% -> {final_memory}%")
                
                # If it's a leak, try to restart the application
                if is_leak and final_memory > MEMORY_LEAK_THRESHOLD:
                    logger.error("MEMORY LEAK CONFIRMED - Consider restarting the application")
                    # In production, you might want to trigger a restart here
            
            # Clean up any stale active uploads (older than 1 hour)
            current_time = time.time()
            stale_uploads = []
            for upload_id in active_uploads.copy():
                # This is a simple check - in production you'd want to track upload timestamps
                if len(upload_id) > 0:  # Basic validation
                    # Remove uploads that have been active too long (this is simplified)
                    pass
            
            logger.info("Periodic cleanup completed")
            
        except asyncio.CancelledError:
            logger.info("Background cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in background cleanup task: {e}")
            # Continue running even if there's an error
            await asyncio.sleep(300)  # Wait 5 minutes before retry

async def start_background_tasks():
    """Start background tasks"""
    global cleanup_task
    try:
        cleanup_task = asyncio.create_task(background_cleanup_task())
        background_tasks.add(cleanup_task)
        cleanup_task.add_done_callback(background_tasks.discard)
        logger.info("Background tasks started")
    except Exception as e:
        logger.error(f"Error starting background tasks: {e}")

async def stop_background_tasks():
    """Stop all background tasks"""
    logger.info("Stopping background tasks...")
    for task in background_tasks.copy():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    background_tasks.clear()
    logger.info("Background tasks stopped")

app = FastAPI(
    title="AI Wave Rider File Manager API",
    description="""
    ## 🚀 AI Wave Rider File Management System
    
    A comprehensive file management API with JWT authentication, designed for managing social media content across multiple platforms.
    
    ## 📋 **API Workflow Sequence**
    
    ### **Step 1: Authentication** 🔐
    1. **POST** `/auth/login` - Get JWT token
    2. **GET** `/test-auth` - Verify authentication works
    
    ### **Step 2: System Check** 🏠
    3. **GET** `/health` - Check API health
    4. **GET** `/` - Basic API info
    
    ### **Step 3: Folder Management** 📁
    5. **GET** `/api/folders/status` - Browse existing folders
    6. **POST** `/api/folders` - Create new folders
    7. **PUT** `/api/folders/rename` - Rename folders
    8. **DELETE** `/api/folders` - Delete folders
    
    ### **Step 4: File Operations** 📄
    9. **GET** `/api/files/check-duplicate` - Check if file exists (prevent duplicates)
    10. **POST** `/api/files/upload` - Upload files (up to 250MB, duplicate prevention)
    11. **POST** `/api/files/upload-chunk` - Upload file chunks (for large files)
    12. **POST** `/api/files/complete-chunked-upload` - Complete chunked upload (duplicate prevention)
    13. **GET** `/api/files/download/{file_path}` - Download single files
    14. **GET** `/api/files/download-token/{file_path}` - Download files with token parameter
    15. **POST** `/api/files/download-bulk` - Download multiple files as ZIP archive
    16. **PUT** `/api/files/rename` - Rename files
    17. **DELETE** `/api/files` - Delete files
    18. **GET** `/api/files/list` - List all files
    
    ### **Step 5: Webhooks** 🔗
    17. Use webhook endpoints for automated integrations
    18. **POST** `/webhook/files/check-duplicate` - Webhook duplicate check
    19. **POST** `/webhook/files/upload` - Webhook file upload
    20. **POST** `/webhook/files/upload-chunk` - Webhook chunk upload
    21. **POST** `/webhook/files/complete-chunked-upload` - Webhook complete upload
    
    ---
    
    ### 🔐 Authentication
    All protected endpoints require a JWT token obtained from `/auth/login`. Include the token in the Authorization header:
    ```
    Authorization: Bearer <your_jwt_token>
    ```
    
    ### 📁 Supported Platforms
    - **Instagram**: ai.waverider, ai.wave.rider, ai.uprise
    - **TikTok**: ai.waverider, ai.wave.rider, aiwaverider9, health
    
    ### 📂 Content Types
    - **Videos**: MP4, MOV, AVI, WebM, MKV, FLV, WMV, 3GP
    - **Images**: JPG, PNG, GIF, WebP, etc.
    
    ### 🎥 Video Operations
    All video operations use the standard file management endpoints:
    - **Upload Videos**: `POST /api/files/upload` (up to 250MB)
    - **Large Video Upload**: Use chunked upload (`/api/files/upload-chunk` + `/api/files/complete-chunked-upload`)
    - **Download Videos**: `GET /api/files/download/{file_path}` (requires authentication)
    - **Public Video Download**: `GET /videos/public/{file_path}` (no authentication required)
    - **Check Duplicate**: `GET /api/files/check-duplicate` (before uploading)
    - **Video Paths**: `/videos/{platform}/{account}/filename.mp4`
      - Platforms: `instagram`, `tiktok`
      - Accounts: See supported platforms above
    
    ### 🌐 Public Video Access
    - **Public Folder**: `videos/public/` - Automatically created on startup
    - **Check Visibility**: `GET /api/folders/visibility` - Check if folder is public
    - **Configure Public Folders**: Set `PUBLIC_FOLDERS` environment variable (e.g., `PUBLIC_FOLDERS=videos/public`)
    - **Public Endpoint**: `GET /videos/public/{file_path}` - Access videos without authentication
      - Example: `/videos/public/public/video.mp4` (for files in videos/public/)
      - Example: `/videos/public/instagram/ai.waverider/video.mp4` (if folder is marked public)
    - **Restriction**: Only folders under `/videos/` can be made public
    - **Security**: Other folders (images, etc.) remain private and require authentication
    
    ### 📦 Chunked Upload System
    - **Large Files**: Support for files up to 250MB
    - **Chunk Size**: 1MB chunks for optimal performance
    - **Reliability**: Resume failed uploads, retry individual chunks
    - **Progress Tracking**: Real-time upload progress
    - **Quality Preservation**: Bit-perfect reconstruction (zero quality loss)
    - **Webhook Support**: Full webhook integration for automated workflows
    
    ### 📥 Download System
    - **Single Downloads**: Direct file downloads with authentication
    - **Bulk Downloads**: ZIP archive creation for multiple files (up to 500MB)
    - **Token Downloads**: URL-based downloads with JWT tokens
    - **Memory Efficient**: Streaming downloads to prevent server overload
    - **Background Processing**: Automatic cleanup of temporary archives
    
    ### 🔒 Security Features
    - JWT-based authentication
    - Path traversal protection
    - File type validation
    - File size validation (250MB limit)
    - Secure file upload/download
    - Protected API documentation
    - Automatic cleanup of temporary files
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
    docs_url=None,  # Completely disable default docs
    redoc_url=None,  # Disable default redoc
    openapi_url=None,  # Completely disable default openapi
)

# Override FastAPI's default OpenAPI schema generation
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Add security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    
    # Add security requirement to all protected endpoints
    # Exclude public endpoints from requiring authentication
    public_paths = ["/videos/public/", "/health", "/", "/auth/login", "/test-simple", "/debug/env"]
    
    for path in openapi_schema["paths"]:
        for method in openapi_schema["paths"][path]:
            if method in ["get", "post", "put", "delete", "patch"]:
                endpoint = openapi_schema["paths"][path][method]
                # Skip public endpoints
                is_public = any(public_path in path for public_path in public_paths)
                if not is_public and "tags" in endpoint and "🔒 Protected" in endpoint.get("tags", []):
                    endpoint["security"] = [{"BearerAuth": []}]
                elif not is_public and path not in ["/health", "/", "/auth/login", "/test-simple", "/debug/env"]:
                    # Add security to all non-public endpoints except the ones explicitly listed
                    if "/videos/public/" not in path:
                        endpoint["security"] = [{"BearerAuth": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

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

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler to prevent 503 errors from unhandled exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "Internal server error occurred",
            "error": "internal_error",
            "timestamp": datetime.now().isoformat()
        }
    )

# Middleware to protect documentation endpoints
from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse

@app.middleware("http")
async def protect_docs_middleware(request: Request, call_next):
    """Middleware to protect documentation endpoints"""
    # Skip middleware for /docs and /openapi.json - they handle auth via JavaScript
    if request.url.path in ["/docs", "/openapi.json"]:
        response = await call_next(request)
        return response
    
    response = await call_next(request)
    return response

# Security
security = HTTPBearer()

# Utility functions
def get_full_path(relative_path: str) -> str:
    """Get full path from relative path"""
    return os.path.join(UPLOAD_DIR, relative_path.lstrip("/"))

def is_safe_path(basedir: str, path: str) -> bool:
    """Check if path is safe (no directory traversal)"""
    try:
        # Resolve both paths to absolute paths
        basedir_abs = os.path.abspath(basedir)
        path_abs = os.path.abspath(path)
        # Check if the resolved path is within the base directory
        common_path = os.path.commonpath([basedir_abs, path_abs])
        return common_path == basedir_abs
    except ValueError:
        # commonpath raises ValueError if paths are on different drives (Windows)
        # In this case, check if the path starts with the base directory
        try:
            basedir_abs = os.path.abspath(basedir)
            path_abs = os.path.abspath(path)
            # Use pathlib for better cross-platform handling
            from pathlib import Path
            return Path(path_abs).is_relative_to(Path(basedir_abs))
        except (AttributeError, ValueError):
            # Fallback: check if normalized paths match
            basedir_norm = os.path.normpath(os.path.abspath(basedir))
            path_norm = os.path.normpath(os.path.abspath(path))
            return path_norm.startswith(basedir_norm + os.sep) or path_norm == basedir_norm

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

def get_chunk_path(upload_id: str, chunk_number: int) -> str:
    """Get the path for a specific chunk file"""
    return os.path.join(TEMP_UPLOAD_DIR, f"{upload_id}_chunk_{chunk_number}")

def cleanup_chunks(upload_id: str, total_chunks: int):
    """Clean up temporary chunk files"""
    try:
        for i in range(1, total_chunks + 1):
            chunk_path = get_chunk_path(upload_id, i)
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
        logger.info(f"Cleaned up chunks for upload_id: {upload_id}")
    except Exception as e:
        logger.error(f"Error cleaning up chunks: {e}")

def validate_file_size(file_size: int) -> bool:
    """Validate if file size is within limits"""
    return file_size <= MAX_FILE_SIZE

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

# Override default FastAPI docs to require authentication
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi


@app.get("/", 
         tags=["2️⃣ System"],
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
         tags=["2️⃣ System"],
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
    - Resource usage
    - Active uploads count
    
    **Use Case**: 
    - Load balancer health checks
    - Monitoring systems
    - Docker health checks
    
    **Authentication**: None required
    """
    try:
        # Check memory usage and determine health status
        memory_percent = check_memory_usage()
        status = "healthy"
        
        # Determine health based on resource usage
        if memory_percent > 90:
            status = "unhealthy"
        elif memory_percent > MEMORY_THRESHOLD:
            status = "degraded"
        
        # Get system resources with error handling
        system_info = {}
        try:
            memory = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=1)
            system_info.update({
                "memory_percent": memory.percent,
                "memory_available_gb": round(memory.available / (1024**3), 2),
                "memory_used_gb": round(memory.used / (1024**3), 2),
                "cpu_percent": cpu_percent
            })
        except Exception as e:
            logger.warning(f"Could not get system info: {e}")
            system_info.update({
                "memory_percent": "unavailable",
                "memory_available_gb": "unavailable",
                "memory_used_gb": "unavailable",
                "cpu_percent": "unavailable"
            })
        
        try:
            disk = psutil.disk_usage(UPLOAD_DIR)
            system_info.update({
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "disk_used_gb": round(disk.used / (1024**3), 2)
            })
        except Exception as e:
            logger.warning(f"Could not get disk info: {e}")
            system_info.update({
                "disk_percent": "unavailable",
                "disk_free_gb": "unavailable",
                "disk_used_gb": "unavailable"
            })
        
        # Count temp chunks safely
        temp_chunks_count = 0
        try:
            if os.path.exists(TEMP_UPLOAD_DIR):
                temp_chunks_count = len([f for f in os.listdir(TEMP_UPLOAD_DIR) if f.endswith('_chunk_')])
        except Exception as e:
            logger.warning(f"Could not count temp chunks: {e}")
            temp_chunks_count = "unavailable"
        
        system_info.update({
            "temp_chunks_count": temp_chunks_count,
            "active_uploads": len(active_uploads),
            "max_concurrent_uploads": MAX_CONCURRENT_UPLOADS
        })
        
        # Force garbage collection if memory usage is high
        if memory_percent > MEMORY_THRESHOLD:
            force_garbage_collection()
            logger.warning(f"High memory usage detected: {memory_percent}%. Forced garbage collection.")
        
        response = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "version": "ENHANCED_VERSION_2025_09_22",
            "upload_dir": UPLOAD_DIR,
            "system": system_info
        }
        
        # Log health status periodically
        if memory_percent > MEMORY_THRESHOLD or temp_chunks_count > 50:
            logger.warning(f"Health check - Status: {status}, Memory: {memory_percent}%, Temp chunks: {temp_chunks_count}")
        
        return response
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
            "version": "ENHANCED_VERSION_2025_09_22"
        }

@app.post("/admin/cleanup",
          tags=["2️⃣ System"],
          summary="Manual Cleanup",
          description="Manually trigger cleanup and garbage collection.")
async def manual_cleanup(current_user: str = Depends(verify_token)):
    """
    ## 🧹 Manual Cleanup Endpoint
    
    Manually triggers cleanup operations and garbage collection.
    
    **Use Case**: 
    - Force cleanup when memory usage is high
    - Troubleshooting performance issues
    - Manual maintenance operations
    
    **Authentication**: JWT token required
    """
    try:
        logger.info("Manual cleanup triggered by user")
        
        # Clean up orphaned chunks
        cleanup_orphaned_chunks()
        
        # Force garbage collection
        force_garbage_collection()
        
        # Get current memory usage
        memory_percent = check_memory_usage()
        
        return {
            "success": True,
            "message": "Cleanup completed successfully",
            "timestamp": datetime.now().isoformat(),
            "memory_usage_after": f"{memory_percent}%"
        }
    except Exception as e:
        logger.error(f"Manual cleanup failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Cleanup failed: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }

@app.get("/health/detailed",
         tags=["2️⃣ System"],
         summary="Detailed Health Check",
         description="Comprehensive health check that includes 503 error detection and automatic recovery.")
async def detailed_health_check():
    """
    ## 🔍 Detailed Health Check Endpoint
    
    This endpoint performs a comprehensive health check including:
    - Service reachability
    - Memory and resource usage
    - Database connectivity
    - File system access
    - Automatic recovery attempts
    
    **Returns**: Detailed health status with recovery actions
    """
    try:
        # Check basic health first
        basic_health = await health_check()
        
        # Additional checks for 503 error conditions
        checks = {
            "basic_health": basic_health.get("status"),
            "memory_usage": check_memory_usage(),
            "disk_space": shutil.disk_usage(UPLOAD_DIR).free / (1024**3),  # GB
            "temp_chunks": len(list(Path(UPLOAD_DIR, "temp_chunks").glob("*"))),
            "active_uploads": len(active_uploads),
            "background_tasks": len(background_tasks)
        }
        
        # Determine overall health
        overall_status = "healthy"
        issues = []
        
        if checks["memory_usage"] > 90:
            overall_status = "degraded"
            issues.append("High memory usage")
            # Trigger cleanup
            cleanup_orphaned_chunks()
            force_garbage_collection()
        
        if checks["disk_space"] < 1:  # Less than 1GB free
            overall_status = "degraded"
            issues.append("Low disk space")
        
        if checks["temp_chunks"] > 50:
            overall_status = "degraded"
            issues.append("Too many temp chunks")
            cleanup_orphaned_chunks()
        
        if checks["active_uploads"] > MAX_CONCURRENT_UPLOADS:
            overall_status = "degraded"
            issues.append("Too many active uploads")
        
        # If we have issues, try to recover
        if issues:
            logger.warning(f"Health issues detected: {issues}")
            # Force cleanup
            cleanup_orphaned_chunks()
            force_garbage_collection()
        
        return {
            "status": overall_status,
            "timestamp": datetime.now().isoformat(),
            "checks": checks,
            "issues": issues,
            "recovery_attempted": len(issues) > 0,
            "version": "ENHANCED_VERSION_2025_09_22"
        }
        
    except Exception as e:
        logger.error(f"Detailed health check failed: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
            "recovery_attempted": False,
            "version": "ENHANCED_VERSION_2025_09_22"
        }

@app.get("/test-auth", 
         tags=["1️⃣ Authentication"],
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
         tags=["2️⃣ System"],
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
         tags=["2️⃣ System"],
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
          tags=["1️⃣ Authentication"],
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
          tags=["3️⃣ Folder Management"],
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
            tags=["3️⃣ Folder Management"],
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
          tags=["3️⃣ Folder Management"],
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

@app.get("/api/folders/visibility",
         tags=["3️⃣ Folder Management"],
         summary="Get Folder Visibility Status",
         description="Check if a folder is public or private based on PUBLIC_FOLDERS environment variable.")
async def get_folder_visibility(
    folder_path: str,
    current_user: str = Depends(verify_token)
):
    """
    ## 🔍 Get Folder Visibility Status Endpoint
    
    Checks whether a folder is currently set to public or private.
    Visibility is controlled via PUBLIC_FOLDERS environment variable.
    
    **Query Parameters**:
    - `folder_path`: Path to the folder (relative to upload directory)
      - Example: `videos/instagram/ai.waverider`
      - Example: `videos/public`
    
    **Response**:
    - `folder_path`: The folder path
    - `is_public`: Boolean indicating if folder is public
    - `public_url`: Public access URL (if public, null if private)
    
    **Use Case**: 
    - Checking folder visibility status
    - Verifying public access configuration
    - Debugging public video access issues
    
    **Authentication**: JWT token required
    **Configuration**: Set PUBLIC_FOLDERS environment variable to configure public folders
    """
    try:
        normalized_path = folder_path.replace("\\", "/").strip("/")
        is_public = is_folder_public(normalized_path)
        
        return {
            "folder_path": normalized_path,
            "is_public": is_public,
            "public_url": f"{BASE_URL}/videos/public/{normalized_path.replace('videos/', '')}" if is_public and normalized_path.startswith("videos/") else None
        }
    except Exception as e:
        logger.error(f"Error getting folder visibility: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# File Management
@app.post("/api/files/upload",
          tags=["4️⃣ File Management"],
          summary="Upload File",
          description="Upload a file to the specified folder with automatic duplicate handling.")
async def upload_file(
    file: UploadFile = File(...),
    folder_path: str = Form(""),
    webhook: bool = Form(False),
    custom_filename: str = None,
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
    - `custom_filename`: Custom filename for the uploaded file (optional, uses original filename if not provided)
    
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
    **Duplicate Prevention**: Returns 409 error if file already exists
    **Path Security**: Protected against directory traversal attacks
    """
    # Check memory usage before upload
    memory_percent = check_memory_usage()
    if memory_percent > 90:
        logger.error(f"Upload rejected due to high memory usage: {memory_percent}%")
        raise HTTPException(
            status_code=503, 
            detail="Server is under heavy load. Please try again later."
        )
    
    # Use semaphore to limit concurrent uploads
    async with upload_semaphore:
        upload_id = str(uuid.uuid4())
        active_uploads.add(upload_id)
        
        try:
            target_folder = get_full_path(folder_path)
            
            if not is_safe_path(UPLOAD_DIR, target_folder):
                raise HTTPException(status_code=400, detail="Invalid path")
            
            os.makedirs(target_folder, exist_ok=True)
            
            # Determine the filename to use
            final_filename = custom_filename if custom_filename else file.filename
            
            # Check for duplicate file
            file_path = os.path.join(target_folder, final_filename)
            if os.path.exists(file_path):
                # Get file info for duplicate
                existing_file_info = get_file_info(file_path)
                duplicate_response = {
                    "error": "duplicate_file",
                    "message": f"File '{final_filename}' already exists in the specified folder",
                    "duplicate_info": {
                        "filename": existing_file_info.name,
                        "path": existing_file_info.path,
                        "size": existing_file_info.size,
                        "modified": existing_file_info.modified,
                        "url": f"{BASE_URL}/api/files/download{existing_file_info.path}"
                    },
                    "suggested_action": "Use a different filename or delete the existing file first"
                }
                
                if webhook:
                    return WebhookResponse(
                        success=False,
                        message=f"Duplicate file detected: '{final_filename}'",
                        data=duplicate_response
                    )
                else:
                    raise HTTPException(
                        status_code=409, 
                        detail=duplicate_response
                    )
        
            # Save file with streaming to prevent memory exhaustion
            total_size = 0
            async with aiofiles.open(file_path, 'wb') as f:
                while True:
                    chunk = await file.read(8192)  # Read in 8KB chunks
                    if not chunk:
                        break
                    
                    total_size += len(chunk)
                    
                    # Validate file size during streaming
                    if not validate_file_size(total_size):
                        await f.close()  # Close file before raising exception
                        if os.path.exists(file_path):
                            os.remove(file_path)  # Clean up partial file
                        raise HTTPException(
                            status_code=413, 
                            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.0f}MB"
                        )
                    
                    await f.write(chunk)
                    
                    # Yield control periodically to prevent blocking
                    if total_size % (1024 * 1024) == 0:  # Every 1MB
                        await asyncio.sleep(0)  # Yield control
            
            relative_path = file_path.replace(UPLOAD_DIR, '').replace('\\', '/')
            file_url = f"{BASE_URL}/api/files/download{relative_path}"
            
            logger.info(f"Uploaded file: {file_path} ({total_size} bytes)")
            
            response_data = {
                "filename": final_filename,
                "path": file_path.replace(UPLOAD_DIR, "").replace("\\", "/"),
                "size": total_size,
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
                
        except HTTPException:
            # Re-raise HTTPExceptions (like 409 for duplicates) without modification
            raise
        except Exception as e:
            logger.error(f"Error uploading file: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            # Always remove from active uploads
            active_uploads.discard(upload_id)
            
            # Force garbage collection after large uploads
            if memory_percent > MEMORY_THRESHOLD:
                force_garbage_collection()

# Chunked Upload Endpoints
@app.post("/api/files/upload-chunk",
          response_model=ChunkUploadResponse,
          tags=["4️⃣ File Management"],
          summary="Upload File Chunk",
          description="Upload a single chunk of a file for chunked upload process.")
async def upload_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_number: int = Form(...),
    total_chunks: int = Form(...),
    folder_path: str = Form(""),
    current_user: str = Depends(verify_token)
):
    """
    ## 📤 Upload File Chunk Endpoint
    
    Uploads a single chunk of a file as part of a chunked upload process.
    This allows for uploading large files by breaking them into smaller pieces.
    
    **Form Data**:
    - `file`: The chunk file (multipart/form-data)
    - `upload_id`: Unique identifier for this upload session
    - `chunk_number`: Number of this chunk (1-based)
    - `total_chunks`: Total number of chunks for this file
    - `folder_path`: Target folder path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `chunk_received`: Number of chunks received so far
    - `total_chunks`: Total number of chunks expected
    - `upload_id`: Upload session identifier
    - `data`: Additional information
    
    **Use Case**: 
    - Uploading large files (>50MB)
    - Resumable uploads
    - Better error handling for large files
    - Progress tracking
    
    **Authentication**: JWT token required
    **Chunk Size**: Recommended 1MB per chunk
    **Max File Size**: 250MB total
    """
    try:
        # Validate chunk number
        if chunk_number < 1 or chunk_number > total_chunks:
            raise HTTPException(status_code=400, detail="Invalid chunk number")
        
        # Generate upload ID if not provided
        if not upload_id:
            upload_id = str(uuid.uuid4())
        
        # Save chunk to temporary directory
        chunk_path = get_chunk_path(upload_id, chunk_number)
        
        # Save chunk file
        async with aiofiles.open(chunk_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"Saved chunk {chunk_number}/{total_chunks} for upload_id: {upload_id}")
        
        # Check if all chunks are received
        received_chunks = 0
        for i in range(1, total_chunks + 1):
            if os.path.exists(get_chunk_path(upload_id, i)):
                received_chunks += 1
        
        return ChunkUploadResponse(
            success=True,
            message=f"Chunk {chunk_number} uploaded successfully",
            chunk_received=received_chunks,
            total_chunks=total_chunks,
            upload_id=upload_id,
            data={
                "chunk_size": len(content),
                "chunk_path": chunk_path
            }
        )
        
    except Exception as e:
        logger.error(f"Error uploading chunk: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/files/complete-chunked-upload",
          response_model=WebhookResponse,
          tags=["4️⃣ File Management"],
          summary="Complete Chunked Upload",
          description="Combine all chunks into the final file and complete the upload process.")
async def complete_chunked_upload(
    request: ChunkCompleteRequest,
    current_user: str = Depends(verify_token)
):
    """
    ## 🔗 Complete Chunked Upload Endpoint
    
    Combines all uploaded chunks into the final file and completes the upload process.
    This should be called after all chunks have been uploaded.
    
    **Request Body**:
    - `upload_id`: Upload session identifier
    - `filename`: Final filename for the complete file
    - `total_chunks`: Total number of chunks
    - `folder_path`: Target folder path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains file information (filename, path, size, url)
    
    **Use Case**: 
    - Finalizing chunked uploads
    - Combining file chunks
    - Completing large file uploads
    
    **Authentication**: JWT token required
    **File Validation**: Checks that all chunks are present
    **Duplicate Prevention**: Returns error if file already exists
    **Cleanup**: Automatically removes temporary chunk files
    """
    try:
        # Validate that all chunks exist
        missing_chunks = []
        total_size = 0
        
        for i in range(1, request.total_chunks + 1):
            chunk_path = get_chunk_path(request.upload_id, i)
            if not os.path.exists(chunk_path):
                missing_chunks.append(i)
            else:
                total_size += os.path.getsize(chunk_path)
        
        if missing_chunks:
            raise HTTPException(
                status_code=400, 
                detail=f"Missing chunks: {missing_chunks}"
            )
        
        # Validate total file size
        if not validate_file_size(total_size):
            cleanup_chunks(request.upload_id, request.total_chunks)
            raise HTTPException(
                status_code=413, 
                detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.0f}MB"
            )
        
        # Prepare target folder
        target_folder = get_full_path(request.folder_path)
        
        if not is_safe_path(UPLOAD_DIR, target_folder):
            cleanup_chunks(request.upload_id, request.total_chunks)
            raise HTTPException(status_code=400, detail="Invalid path")
        
        os.makedirs(target_folder, exist_ok=True)
        
        # Check for duplicate file
        file_path = os.path.join(target_folder, request.filename)
        if os.path.exists(file_path):
            # Get file info for duplicate
            existing_file_info = get_file_info(file_path)
            duplicate_response = {
                "error": "duplicate_file",
                "message": f"File '{request.filename}' already exists in the specified folder",
                "duplicate_info": {
                    "filename": existing_file_info.name,
                    "path": existing_file_info.path,
                    "size": existing_file_info.size,
                    "modified": existing_file_info.modified,
                    "url": f"{BASE_URL}/api/files/download{existing_file_info.path}"
                },
                "suggested_action": "Use a different filename or delete the existing file first"
            }
            
            # Clean up chunks before returning error
            cleanup_chunks(request.upload_id, request.total_chunks)
            
            return WebhookResponse(
                success=False,
                message=f"Duplicate file detected: '{request.filename}'",
                data=duplicate_response
            )
        
        # Combine chunks into final file asynchronously
        async with aiofiles.open(file_path, 'wb') as final_file:
            for i in range(1, request.total_chunks + 1):
                chunk_path = get_chunk_path(request.upload_id, i)
                async with aiofiles.open(chunk_path, 'rb') as chunk_file:
                    while True:
                        chunk_data = await chunk_file.read(8192)  # Read in 8KB chunks
                        if not chunk_data:
                            break
                        await final_file.write(chunk_data)
        
        # Clean up temporary chunks
        cleanup_chunks(request.upload_id, request.total_chunks)
        
        # Generate response data
        relative_path = file_path.replace(UPLOAD_DIR, '').replace('\\', '/')
        file_url = f"{BASE_URL}/api/files/download{relative_path}"
        
        logger.info(f"Completed chunked upload: {file_path}")
        
        response_data = {
            "filename": os.path.basename(file_path),
            "path": file_path.replace(UPLOAD_DIR, "").replace("\\", "/"),
            "size": total_size,
            "url": file_url,
            "chunks_combined": request.total_chunks
        }
        
        return WebhookResponse(
            success=True,
            message="Chunked upload completed successfully",
            data=response_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing chunked upload: {e}")
        # Clean up chunks on error
        cleanup_chunks(request.upload_id, request.total_chunks)
        raise HTTPException(status_code=500, detail=str(e))

# Public Video Endpoint (No Authentication Required)
@app.get("/videos/public/{file_path:path}",
         tags=["6️⃣ Public Videos"],
         summary="Public Video Download",
         description="Download videos from public folders without authentication. Only folders marked as public via PUBLIC_FOLDERS env var are accessible.")
async def download_public_video(file_path: str):
    """
    ## 🌐 Public Video Download Endpoint
    
    Downloads videos from folders that have been marked as public via PUBLIC_FOLDERS environment variable.
    **No authentication required** - this endpoint is publicly accessible.
    
    **Path Parameters**:
    - `file_path`: Relative path to the video file within the videos folder
      - Example for public folder: `public/video.mp4` (files in `videos/public/`)
      - Example for platform folders: `instagram/ai.waverider/video.mp4` (if folder is marked public)
      - Full paths: `/videos/public/video.mp4` or `/videos/instagram/ai.waverider/video.mp4`
    
    **Public Video Download Examples**:
    ```bash
    # Download from public folder (if PUBLIC_FOLDERS=videos/public)
    curl -X GET "https://drive.aiwaverider.com/videos/public/public/video.mp4" \\
      --output video.mp4
    
    # Download from platform folder (if PUBLIC_FOLDERS includes the folder)
    curl -X GET "https://drive.aiwaverider.com/videos/public/instagram/ai.waverider/video.mp4" \\
      --output video.mp4
    ```
    
    **Response**:
    - Video file content with appropriate Content-Type header
    - File name in Content-Disposition header
    - Video MIME types: `video/mp4`, `video/quicktime`, etc.
    
    **Use Case**: 
    - Public video sharing
    - Embedding videos in websites
    - Direct video access without authentication
    - CDN-like video serving
    
    **Authentication**: None required (public endpoint)
    **Security**: Only folders listed in PUBLIC_FOLDERS environment variable are accessible
    **Path Security**: Protected against directory traversal attacks
    **Restriction**: Only works for files under `/videos/` folder
    """
    try:
        # Construct full path within videos folder
        # file_path should be relative to videos folder (e.g., "instagram/ai.waverider/video.mp4" or "public/video.mp4")
        # OR it could be a subfolder path like "thumbnails/file.png" which means "public/thumbnails/file.png"
        normalized_file_path = file_path.replace("\\", "/").lstrip("/")
        logger.info(f"Public video request - original file_path: '{file_path}', normalized: '{normalized_file_path}'")
        
        # Remove any "videos/public/" prefix if present (handles cases where full path is included)
        if normalized_file_path.startswith("videos/public/"):
            normalized_file_path = normalized_file_path[14:]  # Remove "videos/public/" prefix
            logger.info(f"Removed videos/public/ prefix, new normalized: '{normalized_file_path}'")
        elif normalized_file_path.startswith("videos/"):
            normalized_file_path = normalized_file_path[7:]  # Remove "videos/" prefix if present
            logger.info(f"Removed videos/ prefix, new normalized: '{normalized_file_path}'")
        
        # Remove "videos/public/" from anywhere in the path (handles malformed URLs with duplicate paths)
        # This handles cases like "thumbnails/videos/public/thumbnails/file.png" -> "thumbnails/file.png"
        if "/videos/public/" in normalized_file_path:
            # Find the last occurrence and extract everything after it
            parts = normalized_file_path.split("/videos/public/")
            if len(parts) > 1:
                # Take everything after the last "/videos/public/" occurrence
                normalized_file_path = parts[-1]
                logger.info(f"Removed /videos/public/ from path, new normalized: '{normalized_file_path}'")
            else:
                normalized_file_path = normalized_file_path.replace("/videos/public/", "/")
                logger.info(f"Removed /videos/public/ from path, new normalized: '{normalized_file_path}'")
        # Also handle if it appears at the start after previous processing
        if normalized_file_path.startswith("videos/public/"):
            normalized_file_path = normalized_file_path[14:]
            logger.info(f"Removed videos/public/ prefix (second pass), new normalized: '{normalized_file_path}'")
        
        # Get the folder path (parent directory of the file)
        path_parts = normalized_file_path.split("/")
        logger.info(f"Path parts: {path_parts}, length: {len(path_parts)}")
        
        # Handle different path formats:
        # 1. Files directly in public folder: "public/video.mp4" or just "video.mp4" (if in public)
        # 2. Files in public subfolders: "public/thumbnails/video.png" or "thumbnails/video.png"
        # 3. Files in platform/account folders: "instagram/ai.waverider/video.mp4"
        
        if len(path_parts) == 1:
            # Single filename - assume it's in the public folder
            folder_path = "public"
            normalized_file_path = f"public/{normalized_file_path}"
            logger.info(f"Single filename case - folder_path: '{folder_path}'")
        elif path_parts[0] == "public":
            # File is in public folder or its subfolders: "public/video.mp4" or "public/thumbnails/video.png"
            # Get all parts except the filename
            folder_path_parts = path_parts[:-1]
            folder_path = "/".join(folder_path_parts)  # This will be "public" or "public/thumbnails" etc.
            logger.info(f"Public folder case - folder_path: '{folder_path}'")
        elif len(path_parts) >= 2:
            # Since this endpoint is /videos/public/, any path here is assumed to be a subfolder of public
            # Prepend "public/" to the path to handle cases like "thumbnails/file.png" -> "public/thumbnails/file.png"
            logger.info(f"Entering subfolder case - path_parts: {path_parts}, path_parts[:-1]: {path_parts[:-1]}")
            folder_path_parts = ["public"] + path_parts[:-1]
            folder_path = "/".join(folder_path_parts)  # e.g., "public/thumbnails"
            normalized_file_path = "/".join(["public"] + path_parts)  # e.g., "public/thumbnails/file.png"
            logger.info(f"Subfolder case - folder_path_parts: {folder_path_parts}, folder_path: '{folder_path}', normalized_file_path: '{normalized_file_path}'")
        else:
            raise HTTPException(status_code=400, detail="Invalid video path format. Expected: platform/account/filename.mp4 or public/filename.mp4")
        
        # Check if folder is public
        full_folder_path = f"videos/{folder_path}"
        logger.info(f"Checking public access for folder: '{full_folder_path}', original file_path was: '{file_path}'")
        if not is_folder_public(full_folder_path):
            raise HTTPException(
                status_code=403,
                detail=f"Folder '{full_folder_path}' is not public. Set PUBLIC_FOLDERS environment variable to make it public."
            )
        
        # Construct full file path
        full_file_path = os.path.join(UPLOAD_DIR, "videos", normalized_file_path)
        
        # Security check
        if not is_safe_path(os.path.join(UPLOAD_DIR, "videos"), full_file_path):
            raise HTTPException(status_code=400, detail="Invalid path - directory traversal detected")
        
        if not os.path.exists(full_file_path):
            raise HTTPException(status_code=404, detail="Video file not found")
        
        # Verify it's actually a file (not a directory)
        if not os.path.isfile(full_file_path):
            raise HTTPException(status_code=400, detail="Path points to a directory, not a file")
        
        logger.info(f"Public video download: {full_file_path}")
        return FileResponse(full_file_path)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading public video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/download/{file_path:path}",
          tags=["4️⃣ File Management"],
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

@app.get("/api/files/download-token/{file_path:path}",
          tags=["4️⃣ File Management"],
          summary="Download File with Token",
          description="Download a file by its path using token as query parameter.")
async def download_file_with_token(
    file_path: str,
    token: str,
    current_user: str = Depends(verify_token)
):
    """
    ## 📥 Download File with Token Endpoint
    
    Downloads a file by its relative path using a JWT token provided as a query parameter.
    This is useful for direct links and embedding in HTML.
    
    **Path Parameters**:
    - `file_path`: Relative path to the file (e.g., `/thumbnails/edited/63c5545a-60ae-4d4d-88a5-3d5f089fd331`)
    
    **Query Parameters**:
    - `token`: JWT token for authentication
    
    **Response**:
    - File content with appropriate Content-Type header
    - File name in Content-Disposition header
    
    **Use Case**: 
    - Direct file links in emails
    - Embedding in HTML img tags
    - Sharing files via URL
    
    **Example**:
    ```
    https://drive-backend.aiwaverider.com/api/files/download-token/thumbnails/edited/63c5545a-60ae-4d4d-88a5-3d5f089fd331?token=YOUR_JWT_TOKEN
    ```
    
    **Authentication**: JWT token required (via query parameter)
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
        logger.error(f"Error downloading file with token: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/files/download-bulk",
          tags=["4️⃣ File Management"],
          summary="Bulk Download Files",
          description="Download multiple files as a ZIP archive.")
async def download_bulk_files(
    request: BulkDownloadRequest,
    current_user: str = Depends(verify_token)
):
    """
    ## 📦 Bulk Download Files Endpoint
    
    Downloads multiple files as a ZIP archive. Useful for downloading entire folders or selected files.
    
    **Request Body**:
    - `file_paths`: List of relative file paths to include in the archive
    - `archive_name`: Name of the ZIP file (optional, defaults to "download.zip")
    
    **Response**:
    - ZIP file containing all requested files
    
    **Use Case**: 
    - Downloading multiple files at once
    - Folder downloads
    - Batch file operations
    - Content archiving
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    **File Size Limit**: Combined files must not exceed available memory
    """
    try:
        # Check memory usage before creating archive
        memory_percent = check_memory_usage()
        if memory_percent > 85:
            logger.error(f"Bulk download rejected due to high memory usage: {memory_percent}%")
            raise HTTPException(
                status_code=503, 
                detail="Server is under heavy load. Please try again later."
            )
        
        # Validate file paths and calculate total size
        valid_files = []
        total_size = 0
        
        for file_path in request.file_paths:
            full_path = get_full_path(file_path)
            
            if not is_safe_path(UPLOAD_DIR, full_path):
                logger.warning(f"Unsafe path rejected: {file_path}")
                continue
            
            if not os.path.exists(full_path) or not os.path.isfile(full_path):
                logger.warning(f"File not found: {file_path}")
                continue
            
            file_size = os.path.getsize(full_path)
            total_size += file_size
            valid_files.append((file_path, full_path, file_size))
        
        if not valid_files:
            raise HTTPException(status_code=404, detail="No valid files found")
        
        # Check if total size is reasonable (limit to 500MB for bulk downloads)
        max_bulk_size = 500 * 1024 * 1024  # 500MB
        if total_size > max_bulk_size:
            raise HTTPException(
                status_code=413, 
                detail=f"Total file size too large. Maximum: {max_bulk_size / (1024*1024):.0f}MB"
            )
        
        # Create temporary ZIP file
        temp_zip_path = None
        try:
            # Create temporary file
            temp_fd, temp_zip_path = tempfile.mkstemp(suffix='.zip', prefix='bulk_download_')
            os.close(temp_fd)  # Close the file descriptor, we'll use the path
            
            # Create ZIP archive
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path, full_path, file_size in valid_files:
                    # Use the relative path as the archive name
                    archive_name = file_path.lstrip('/')
                    zipf.write(full_path, archive_name)
                    
                    # Yield control periodically for large files
                    if file_size > 10 * 1024 * 1024:  # Files larger than 10MB
                        await asyncio.sleep(0)
            
            logger.info(f"Created bulk download archive with {len(valid_files)} files ({total_size} bytes)")
            
            # Return the ZIP file
            return FileResponse(
                temp_zip_path,
                filename=request.archive_name,
                media_type='application/zip',
                background=lambda: os.unlink(temp_zip_path) if os.path.exists(temp_zip_path) else None
            )
            
        except Exception as e:
            # Clean up temporary file on error
            if temp_zip_path and os.path.exists(temp_zip_path):
                os.unlink(temp_zip_path)
            raise e
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating bulk download: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/files",
            tags=["4️⃣ File Management"],
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
          tags=["4️⃣ File Management"],
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

@app.put("/api/files/move",
         tags=["4️⃣ File Management"],
         summary="Move File",
         description="Move a file from one folder to another.")
async def move_file(
    move_data: FileMove,
    webhook: bool = False,
    current_user: str = Depends(verify_token)
):
    """
    ## 📦 Move File Endpoint
    
    Moves a file from its current location to a different folder.
    
    **Request Body**:
    - `file_path`: Current path to the file
    - `destination_folder`: Destination folder path (empty string for root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains new file path
    
    **Use Case**: 
    - Organizing files across folders
    - Content management workflows
    - File reorganization
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    **Duplicate Prevention**: Returns error if file already exists in destination
    """
    try:
        # Get source file path
        source_full_path = get_full_path(move_data.file_path)
        
        # Validate source path
        if not is_safe_path(UPLOAD_DIR, source_full_path):
            raise HTTPException(status_code=400, detail="Invalid source path")
        
        if not os.path.exists(source_full_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        if not os.path.isfile(source_full_path):
            raise HTTPException(status_code=400, detail="Path is not a file")
        
        # Get destination folder path
        destination_folder = get_full_path(move_data.destination_folder)
        
        # Validate destination path
        if not is_safe_path(UPLOAD_DIR, destination_folder):
            raise HTTPException(status_code=400, detail="Invalid destination path")
        
        # Create destination folder if it doesn't exist
        os.makedirs(destination_folder, exist_ok=True)
        
        # Get filename from source path
        filename = os.path.basename(source_full_path)
        
        # Check for duplicate in destination
        destination_file_path = os.path.join(destination_folder, filename)
        if os.path.exists(destination_file_path):
            existing_file_info = get_file_info(destination_file_path)
            duplicate_response = {
                "error": "duplicate_file",
                "message": f"File '{filename}' already exists in destination folder",
                "duplicate_info": {
                    "filename": existing_file_info.name,
                    "path": existing_file_info.path,
                    "size": existing_file_info.size,
                    "modified": existing_file_info.modified,
                    "url": f"{BASE_URL}/api/files/download{existing_file_info.path}"
                },
                "suggested_action": "Use a different filename or delete the existing file first"
            }
            
            if webhook:
                return WebhookResponse(
                    success=False,
                    message=f"Duplicate file detected in destination: '{filename}'",
                    data=duplicate_response
                )
            else:
                raise HTTPException(status_code=409, detail=duplicate_response)
        
        # Move the file
        os.rename(source_full_path, destination_file_path)
        logger.info(f"Moved file from {source_full_path} to {destination_file_path}")
        
        # Generate response
        relative_path = destination_file_path.replace(UPLOAD_DIR, '').replace('\\', '/')
        if not relative_path.startswith('/'):
            relative_path = '/' + relative_path
        
        if webhook:
            return WebhookResponse(
                success=True,
                message=f"File moved successfully to '{move_data.destination_folder}'",
                data={
                    "new_path": relative_path,
                    "filename": filename,
                    "destination_folder": move_data.destination_folder
                }
            )
        else:
            return {
                "message": "File moved successfully",
                "new_path": relative_path,
                "filename": filename
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Status and listing endpoints
@app.get("/api/folders/status", 
         response_model=FolderStatus,
         tags=["3️⃣ Folder Management"],
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

@app.get("/api/files/check-duplicate",
         tags=["4️⃣ File Management"],
         summary="Check File Duplicate",
         description="Check if a file with the given name already exists in the specified folder.")
async def check_file_duplicate(
    filename: str,
    folder_path: str = "",
    current_user: str = Depends(verify_token)
):
    """
    ## 🔍 Check File Duplicate Endpoint
    
    Checks if a file with the given name already exists in the specified folder.
    Useful for preventing duplicate uploads before attempting to upload.
    
    **Query Parameters**:
    - `filename`: Name of the file to check
    - `folder_path`: Path to the folder (optional, defaults to root)
    
    **Response**:
    - `exists`: Boolean indicating if file exists
    - `file_info`: File information if exists (null if not exists)
    - `suggested_action`: Action recommendation if file exists
    
    **Use Case**: 
    - Pre-upload validation
    - Duplicate prevention
    - User interface feedback
    - Upload workflow optimization
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        target_folder = get_full_path(folder_path)
        
        if not is_safe_path(UPLOAD_DIR, target_folder):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(target_folder):
            raise HTTPException(status_code=404, detail="Folder not found")
        
        file_path = os.path.join(target_folder, filename)
        
        if os.path.exists(file_path):
            file_info = get_file_info(file_path)
            return {
                "exists": True,
                "file_info": {
                    "filename": file_info.name,
                    "path": file_info.path,
                    "size": file_info.size,
                    "modified": file_info.modified,
                    "url": f"{BASE_URL}/api/files/download{file_info.path}"
                },
                "suggested_action": "Use a different filename or delete the existing file first"
            }
        else:
            return {
                "exists": False,
                "file_info": None,
                "suggested_action": "File can be uploaded safely"
            }
            
    except Exception as e:
        logger.error(f"Error checking file duplicate: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/list",
         tags=["4️⃣ File Management"],
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

@app.get("/api/folders/list",
         tags=["3️⃣ Folder Management"],
         summary="List All Folders",
         description="Recursively list all folders in the upload directory.")
async def list_all_folders(folder_path: str = "", current_user: str = Depends(verify_token)):
    """
    ## 📁 List All Folders Endpoint
    
    Recursively lists all folders in the upload directory. Useful for folder selection
    in file operations like move, copy, etc.
    
    **Query Parameters**:
    - `folder_path`: Path to start listing from (optional, defaults to root)
    
    **Response**:
    - `folders`: Array of folder paths (relative to upload directory)
    - `count`: Total number of folders found
    
    **Use Case**: 
    - Folder selection in file operations
    - Building folder browsers
    - File organization workflows
    
    **Authentication**: JWT token required
    **Path Security**: Protected against directory traversal attacks
    """
    try:
        full_path = get_full_path(folder_path)
        
        if not is_safe_path(UPLOAD_DIR, full_path):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="Folder not found")
        
        folders = []
        # Walk through all directories
        for root, dirs, filenames in os.walk(full_path):
            # Get relative path from UPLOAD_DIR
            relative_root = os.path.relpath(root, UPLOAD_DIR)
            # Normalize path separators
            if relative_root == ".":
                relative_root = ""
            else:
                relative_root = relative_root.replace("\\", "/")
            
            # Add current directory if it's not root
            if relative_root and relative_root not in folders:
                folders.append(relative_root)
            
            # Add all subdirectories
            for dir_name in dirs:
                subfolder_path = os.path.join(relative_root, dir_name) if relative_root else dir_name
                subfolder_path = subfolder_path.replace("\\", "/")
                if subfolder_path not in folders:
                    folders.append(subfolder_path)
        
        # Sort folders for easier browsing
        folders.sort()
        
        return {"folders": folders, "count": len(folders)}
    except Exception as e:
        logger.error(f"Error listing folders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Webhook endpoints

@app.post("/webhook/files/upload", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook File Upload",
          description="Webhook endpoint for file upload with standardized response format.")
async def webhook_upload_file(
    file: UploadFile = File(...),
    folder_path: str = Form(""),
    filename: str = Form(None),
    current_user: str = Depends(verify_token)
):
    """
    ## 🔗 Webhook File Upload Endpoint
    
    Webhook version of file upload that returns a standardized webhook response format.
    
    **Form Data**:
    - `file`: The file to upload (multipart/form-data)
    - `folder_path`: Target folder path (optional, defaults to root)
    - `filename`: Custom filename for the uploaded file (optional, uses original filename if not provided)
    
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
    return await upload_file(file, folder_path, webhook=True, custom_filename=filename)

@app.post("/webhook/files/delete", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook File Delete",
          description="Webhook endpoint for file deletion with standardized response format.")
async def webhook_delete_file(file_path: str = Form(...), current_user: str = Depends(verify_token)):
    """Webhook for file deletion"""
    return await delete_file(file_path, webhook=True)

@app.post("/webhook/files/rename", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook File Rename",
          description="Webhook endpoint for file renaming with standardized response format.")
async def webhook_rename_file(
    old_path: str = Form(...),
    new_name: str = Form(...),
    current_user: str = Depends(verify_token)
):
    """Webhook for file rename"""
    return await rename_file(old_path, new_name, webhook=True)

@app.post("/webhook/files/move",
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook File Move",
          description="Webhook endpoint for moving files with standardized response format.")
async def webhook_move_file(
    move_data: FileMove,
    current_user: str = Depends(verify_token)
):
    """Webhook for file move"""
    return await move_file(move_data, webhook=True)

@app.post("/webhook/files/upload-chunk",
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook Chunk Upload",
          description="Webhook endpoint for chunked file upload with standardized response format.")
async def webhook_upload_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_number: int = Form(...),
    total_chunks: int = Form(...),
    folder_path: str = Form(""),
    current_user: str = Depends(verify_token)
):
    """
    ## 🔗 Webhook Chunk Upload Endpoint
    
    Webhook version of chunk upload that returns a standardized webhook response format.
    
    **Form Data**:
    - `file`: The chunk file (multipart/form-data)
    - `upload_id`: Upload session identifier
    - `chunk_number`: Number of this chunk (1-based)
    - `total_chunks`: Total number of chunks
    - `folder_path`: Target folder path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains chunk information and progress
    
    **Use Case**: 
    - Third-party chunked upload integrations
    - Automated large file processing
    - External system chunk management
    - Webhook-based chunked workflows
    
    **Authentication**: JWT token required
    **Response Format**: Standardized webhook format
    """
    try:
        result = await upload_chunk(file, upload_id, chunk_number, total_chunks, folder_path)
        return WebhookResponse(
            success=result.success,
            message=result.message,
            data={
                "chunk_received": result.chunk_received,
                "total_chunks": result.total_chunks,
                "upload_id": result.upload_id,
                "chunk_data": result.data
            }
        )
    except Exception as e:
        logger.error(f"Error in webhook chunk upload: {e}")
        return WebhookResponse(
            success=False,
            message=str(e)
        )

@app.post("/webhook/files/complete-chunked-upload",
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook Complete Chunked Upload",
          description="Webhook endpoint for completing chunked upload with standardized response format.")
async def webhook_complete_chunked_upload(
    request: ChunkCompleteRequest,
    current_user: str = Depends(verify_token)
):
    """
    ## 🔗 Webhook Complete Chunked Upload Endpoint
    
    Webhook version of complete chunked upload that returns a standardized webhook response format.
    
    **Request Body**:
    - `upload_id`: Upload session identifier
    - `filename`: Final filename for the complete file
    - `total_chunks`: Total number of chunks
    - `folder_path`: Target folder path (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains final file information
    
    **Use Case**: 
    - Third-party chunked upload completion
    - Automated large file finalization
    - External system file assembly
    - Webhook-based upload completion
    
    **Authentication**: JWT token required
    **Response Format**: Standardized webhook format
    """
    return await complete_chunked_upload(request)

@app.post("/webhook/folders/create", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook Folder Create",
          description="Webhook endpoint for folder creation with standardized response format.")
async def webhook_create_folder(folder: FolderCreate, current_user: str = Depends(verify_token)):
    """Webhook for folder creation"""
    return await create_folder(folder)

@app.post("/webhook/folders/delete", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook Folder Delete",
          description="Webhook endpoint for folder deletion with standardized response format.")
async def webhook_delete_folder(folder_path: str = Form(...), current_user: str = Depends(verify_token)):
    """Webhook for folder deletion"""
    return await delete_folder(folder_path)

@app.post("/webhook/folders/rename", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook Folder Rename",
          description="Webhook endpoint for folder renaming with standardized response format.")
async def webhook_rename_folder(rename_data: FolderRename, current_user: str = Depends(verify_token)):
    """Webhook for folder rename"""
    return await rename_folder(rename_data)

@app.post("/webhook/files/check-duplicate", 
          response_model=WebhookResponse,
          tags=["5️⃣ Webhooks"],
          summary="Webhook Check Duplicate",
          description="Webhook endpoint for checking file duplicates with standardized response format.")
async def webhook_check_duplicate(
    filename: str = Form(...),
    folder_path: str = Form(""),
    current_user: str = Depends(verify_token)
):
    """
    ## 🔗 Webhook Check Duplicate Endpoint
    
    Webhook version of duplicate check that returns a standardized webhook response format.
    
    **Form Data**:
    - `filename`: Name of the file to check
    - `folder_path`: Path to the folder (optional, defaults to root)
    
    **Response**:
    - `success`: Boolean indicating success
    - `message`: Success/error message
    - `data`: Contains duplicate check results
    
    **Use Case**: 
    - Third-party duplicate checking
    - Automated upload validation
    - External system file management
    - Webhook-based duplicate prevention
    
    **Authentication**: JWT token required
    **Response Format**: Standardized webhook format
    """
    try:
        result = await check_file_duplicate(filename, folder_path)
        return WebhookResponse(
            success=True,
            message="Duplicate check completed successfully",
            data=result
        )
    except Exception as e:
        logger.error(f"Error in webhook duplicate check: {e}")
        return WebhookResponse(
            success=False,
            message=str(e)
        )

@app.get("/webhook/folders/status", 
         response_model=WebhookResponse,
         tags=["5️⃣ Webhooks"],
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

# Custom Protected Documentation Endpoints
# These must be defined after all other routes to ensure they override any defaults

@app.get("/login", 
         tags=["🔒 Protected"],
         summary="Documentation Login Page",
         description="Login page for accessing API documentation.")
async def get_login_page():
    """
    ## 🔐 Documentation Login Page
    
    Interactive login page for accessing the API documentation.
    
    **Use Case**: Browser-based access to protected documentation
    """
    login_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AI Wave Rider API - Authentication Required</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
            .container { background: #f5f5f5; padding: 30px; border-radius: 10px; }
            .form-group { margin: 15px 0; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input[type="text"], input[type="password"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
            .error { color: red; margin-top: 10px; }
            .success { color: green; margin-top: 10px; }
            .info { background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 AI Wave Rider API Documentation</h1>
            <p>Authentication required to access the API documentation.</p>
            
            <div class="info">
                <strong>📋 How to access:</strong><br>
                1. Enter your credentials below<br>
                2. Click "Get Access Token"<br>
                3. You'll be redirected to the documentation
            </div>
            
            <form id="loginForm">
                <div class="form-group">
                    <label for="username">Username:</label>
                    <input type="text" id="username" name="username" value="admin" required>
                </div>
                <div class="form-group">
                    <label for="password">Password:</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <button type="submit">Get Access Token</button>
            </form>
            
            <div id="message"></div>
            
            <script>
                document.getElementById('loginForm').addEventListener('submit', async function(e) {
                    e.preventDefault();
                    console.log('Form submitted');
                    
                    const username = document.getElementById('username').value;
                    const password = document.getElementById('password').value;
                    const messageDiv = document.getElementById('message');
                    
                    console.log('Username:', username);
                    console.log('Password length:', password.length);
                    
                    // Show loading message
                    messageDiv.innerHTML = '<div class="info">🔄 Authenticating...</div>';
                    
                    try {
                        console.log('Making request to /auth/login');
                        const response = await fetch('/auth/login', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username, password })
                        });
                        
                        console.log('Response status:', response.status);
                        console.log('Response ok:', response.ok);
                        
                        if (response.ok) {
                            const data = await response.json();
                            console.log('Token received:', data.access_token ? 'Yes' : 'No');
                            
                            // Store token in sessionStorage
                            sessionStorage.setItem('api_token', data.access_token);
                            console.log('Token stored in sessionStorage');
                            
                            // Show success message
                            messageDiv.innerHTML = '<div class="success">✅ Authentication successful! Redirecting...</div>';
                            
                            // Small delay before redirect
                            setTimeout(() => {
                                window.location.href = '/docs';
                            }, 1000);
                        } else {
                            const error = await response.json();
                            console.error('Login error:', error);
                            messageDiv.innerHTML = '<div class="error">❌ ' + error.detail + '</div>';
                        }
                    } catch (error) {
                        console.error('Fetch error:', error);
                        messageDiv.innerHTML = '<div class="error">❌ Login failed: ' + error.message + '</div>';
                    }
                });
            </script>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=login_html)

@app.get("/docs", 
         tags=["🔒 Protected"],
         summary="API Documentation (Protected)",
         description="Swagger UI documentation - requires authentication.")
async def get_docs():
    """
    ## 📚 Protected API Documentation
    
    Access to the Swagger UI documentation requires authentication.
    
    **Authentication**: JWT token required
    **Use Case**: Secure API documentation access
    """
    from fastapi.responses import HTMLResponse
    
    # Custom Swagger UI with authentication
    swagger_html = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="AI Wave Rider File Manager API - Documentation",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )
    
    # Add custom JavaScript to handle authentication
    custom_js = """
    <script>
        console.log('Docs page loaded');
        
        // Check if token exists in sessionStorage
        const token = sessionStorage.getItem('api_token');
        console.log('Token in sessionStorage:', token ? 'Found' : 'Not found');
        
        if (!token) {
            console.log('No token found, redirecting to login');
            // Redirect to login if no token
            window.location.href = '/login';
            return;
        }
        
        console.log('Token found, configuring Swagger UI');
        
        // Configure Swagger UI with authentication
        window.onload = function() {
            console.log('Window loaded, initializing Swagger UI');
            const ui = SwaggerUIBundle({
                url: '/openapi.json',
                dom_id: '#swagger-ui',
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.presets.standalone
                ],
                requestInterceptor: function(request) {
                    console.log('Adding authorization header to request:', request.url);
                    // Add authorization header to all requests
                    request.headers['Authorization'] = 'Bearer ' + token;
                    return request;
                },
                responseInterceptor: function(response) {
                    console.log('Response received:', response.status, response.url);
                    // Handle 401 responses by redirecting to login
                    if (response.status === 401) {
                        console.log('401 response, redirecting to login');
                        sessionStorage.removeItem('api_token');
                        window.location.href = '/login';
                    }
                    return response;
                }
            });
        };
    </script>
    """
    
    # Inject custom JavaScript into the HTML
    html_content = swagger_html.body.decode('utf-8')
    html_content = html_content.replace('</body>', custom_js + '</body>')
    
    return HTMLResponse(content=html_content)

@app.get("/openapi.json", 
         tags=["🔒 Protected"],
         summary="OpenAPI Schema (Protected)",
         description="OpenAPI JSON schema - requires authentication.")
async def get_openapi_schema():
    """
    ## 📋 Protected OpenAPI Schema
    
    Access to the OpenAPI JSON schema requires authentication.
    
    **Authentication**: JWT token required
    **Use Case**: Programmatic API schema access
    """
    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
