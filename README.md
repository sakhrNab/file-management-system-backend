# File Manager Backend API

A FastAPI-based file management system with JWT authentication.

## Features

- JWT-based authentication
- File upload/download management (up to 250MB)
- **Chunked upload system** for large files
- **Duplicate file prevention** with detailed error responses
- Folder creation/deletion/renaming
- File operations (upload, download, delete, rename)
- Webhook support for integrations
- Docker containerization
- **Progress tracking** for large file uploads
- **Resumable uploads** with chunk retry capability
- **Enhanced health monitoring** with system metrics

## Environment Variables

Create a `.env` file in the root directory with the following variables:

```bash
# Authentication
AUTH_USERNAME=admin
AUTH_PASSWORD=your_secure_password_here
SECRET_KEY=your-secret-key-change-this-in-production

# Application Configuration
BASE_URL=http://localhost:8003
UPLOAD_DIR=./uploads
PORT=8003
```

## Docker Setup

### Build and Run

```bash
# Build and start the container
docker-compose up --build -d

# Check container status
docker ps

# View logs
docker-compose logs -f backend

# Stop the container
docker-compose down
```

### Access the API

- **API Base URL**: http://localhost:8003
- **Health Check**: http://localhost:8003/health (with system metrics)
- **API Documentation**: http://localhost:8003/docs (Swagger UI)
- **Simple Test**: http://localhost:8003/test-simple (no auth required)
- **Environment Debug**: http://localhost:8003/debug/env (no auth required)

## API Endpoints

### Authentication
- `POST /auth/login` - Login and get JWT token
- `GET /test-auth` - Test authenticated endpoint

### System
- `GET /` - API root and basic information
- `GET /health` - Enhanced health check with system metrics
- `GET /test-simple` - Simple test endpoint (no auth required)
- `GET /debug/env` - Environment variables debug (no auth required)

### File Management
- `GET /api/files/check-duplicate` - Check if file exists (prevent duplicates)
- `POST /api/files/upload` - Upload file (up to 250MB, duplicate prevention)
- `POST /api/files/upload-chunk` - Upload file chunk (for large files)
- `POST /api/files/complete-chunked-upload` - Complete chunked upload (duplicate prevention)
- `GET /api/files/download/{file_path}` - Download file
- `DELETE /api/files` - Delete file
- `PUT /api/files/rename` - Rename file
- `GET /api/files/list` - List all files

### Folder Management
- `POST /api/folders` - Create folder
- `DELETE /api/folders` - Delete folder
- `PUT /api/folders/rename` - Rename folder
- `GET /api/folders/status` - Get folder contents

### Webhooks
- `POST /webhook/files/check-duplicate` - Webhook for duplicate checking
- `POST /webhook/files/upload` - Webhook for file upload
- `POST /webhook/files/upload-chunk` - Webhook for chunk upload
- `POST /webhook/files/complete-chunked-upload` - Webhook for complete upload
- `POST /webhook/files/delete` - Webhook for file deletion
- `POST /webhook/files/rename` - Webhook for file rename
- `POST /webhook/folders/create` - Webhook for folder creation
- `POST /webhook/folders/delete` - Webhook for folder deletion
- `POST /webhook/folders/rename` - Webhook for folder rename
- `GET /webhook/folders/status` - Webhook for folder status

## Chunked Upload System

For large files (>10MB), use the chunked upload system for better reliability:

### How it works:
1. **Split file** into 1MB chunks
2. **Upload chunks** individually using `/api/files/upload-chunk`
3. **Complete upload** by calling `/api/files/complete-chunked-upload`

### Benefits:
- **Reliability**: Resume failed uploads, retry individual chunks
- **Progress tracking**: Real-time upload progress
- **Large files**: Support up to 250MB
- **Quality preservation**: Zero quality loss (bit-perfect reconstruction)

### Example Usage:
```python
# Upload chunks
for chunk_num in range(1, total_chunks + 1):
    response = requests.post('/api/files/upload-chunk', 
                           files={'file': chunk_data},
                           data={'upload_id': upload_id, 
                                 'chunk_number': chunk_num,
                                 'total_chunks': total_chunks})

# Complete upload
response = requests.post('/api/files/complete-chunked-upload',
                        json={'upload_id': upload_id,
                              'filename': 'video.mp4',
                              'total_chunks': total_chunks})
```

### Client Script:
Use the provided `chunked_upload_client.py` for easy chunked uploads:
```bash
python chunked_upload_client.py
```

## Duplicate File Prevention System

The API automatically prevents duplicate file uploads with comprehensive error handling and detailed responses.

### How it works:
1. **Pre-upload check**: Use `/api/files/check-duplicate` to check if file exists
2. **Upload prevention**: Upload endpoints return 409 Conflict if file already exists
3. **Detailed errors**: Error responses include existing file information and suggestions

### Features:
- **409 Conflict Status**: Proper HTTP status code for duplicates
- **Detailed Error Responses**: Complete file information in error responses
- **Pre-upload Validation**: Check for duplicates before attempting upload
- **Webhook Support**: All webhook endpoints include duplicate prevention
- **Chunked Upload Prevention**: Chunked uploads also prevent duplicates

### Error Response Format:
```json
{
  "error": "duplicate_file",
  "message": "File 'video.mp4' already exists in the specified folder",
  "duplicate_info": {
    "filename": "video.mp4",
    "path": "/videos/instagram/ai.uprise/video.mp4",
    "size": 27456789,
    "modified": "2025-09-13T21:15:30.123456",
    "url": "https://drive.aiwaverider.com/api/files/download/videos/instagram/ai.uprise/video.mp4"
  },
  "suggested_action": "Use a different filename or delete the existing file first"
}
```

### Example Usage:
```python
# Check for duplicates before upload
response = requests.get('/api/files/check-duplicate', 
                       params={'filename': 'video.mp4', 'folder_path': '/videos'})

if response.json()['exists']:
    print("File already exists!")
    print(f"Existing file: {response.json()['file_info']['filename']}")
else:
    # Safe to upload
    upload_file('video.mp4')
```

### Testing:
Use the provided test script to verify duplicate prevention:
```bash
python test_duplicate_final.py
```

## Monitoring & Maintenance

The API includes comprehensive monitoring and maintenance tools for production use.

### Health Monitoring
- **Enhanced Health Endpoint**: `/health` provides detailed system metrics
- **Memory Usage**: Real-time memory consumption tracking
- **Disk Usage**: Storage space monitoring
- **Temp Chunks**: Count of temporary upload chunks
- **System Status**: Overall health status

### Cleanup Scripts
- **`cleanup_temp_files.py`**: Automated cleanup of temporary files
- **`monitor_health.py`**: Continuous health monitoring with alerts
- **Automatic Cleanup**: Chunked uploads automatically clean up temp files

### Monitoring Features
- **Real-time Metrics**: Memory, disk, and temp file tracking
- **Alert Thresholds**: Configurable warnings for resource usage
- **Logging**: Comprehensive application and health logging
- **Docker Integration**: Health checks for container orchestration

### Example Usage:
```bash
# Monitor system health
python monitor_health.py

# Clean up temporary files
python cleanup_temp_files.py

# Test all functionality
python test_duplicate_final.py
```

## Authentication

All protected endpoints require a JWT token in the Authorization header:

```
Authorization: Bearer <your_jwt_token>
```

## Development

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

### Project Structure

```
backend/
├── infrastructure/
│   └── Dockerfile.backend
├── uploads/                 # User uploaded files
│   └── temp_chunks/         # Temporary chunk files
├── logs/                    # Application logs
├── main.py                  # FastAPI application
├── requirements.txt         # Python dependencies
├── docker-compose.yml       # Docker configuration
├── .gitignore              # Git ignore rules
├── README.md               # This file
├── chunked_upload_client.py # Chunked upload client script
├── cleanup_temp_files.py   # Cleanup utility script
├── monitor_health.py       # Health monitoring script
├── test_duplicate_final.py # Comprehensive duplicate testing
├── test_duplicate_local.py # Local Docker testing
└── test_duplicate_prevention.py # Basic duplicate testing
```

## Supported Platforms & Content Types

### Social Media Platforms
- **Instagram**: ai.waverider, ai.wave.rider, ai.uprise
- **TikTok**: ai.waverider, ai.wave.rider, aiwaverider9, health

### Content Types
- **Videos**: MP4, MOV, AVI, WebM, etc.
- **Images**: JPG, PNG, GIF, WebP, SVG, etc.
- **Documents**: PDF, DOC, DOCX, TXT, etc.
- **All file types supported** with proper MIME type detection

## Security Notes

- Change default credentials in production
- Use strong SECRET_KEY for JWT signing
- Configure proper CORS origins for production
- Consider rate limiting for production use
- File uploads are validated for size and type
- Path traversal protection enabled
