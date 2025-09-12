# File Manager Backend API

A FastAPI-based file management system with JWT authentication.

## Features

- JWT-based authentication
- File upload/download management (up to 250MB)
- **Chunked upload system** for large files
- Folder creation/deletion/renaming
- File operations (upload, download, delete, rename)
- Webhook support for integrations
- Docker containerization
- **Progress tracking** for large file uploads
- **Resumable uploads** with chunk retry capability

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
- **Health Check**: http://localhost:8003/health
- **API Documentation**: http://localhost:8003/docs (Swagger UI)

## API Endpoints

### Authentication
- `POST /auth/login` - Login and get JWT token
- `GET /test-auth` - Test authenticated endpoint

### File Management
- `POST /api/files/upload` - Upload file (up to 250MB)
- `POST /api/files/upload-chunk` - Upload file chunk (for large files)
- `POST /api/files/complete-chunked-upload` - Complete chunked upload
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
├── logs/                    # Application logs
├── main.py                  # FastAPI application
├── requirements.txt         # Python dependencies
├── docker-compose.yml       # Docker configuration
├── .gitignore              # Git ignore rules
└── README.md               # This file
```

## Security Notes

- Change default credentials in production
- Use strong SECRET_KEY for JWT signing
- Configure proper CORS origins for production
- Consider rate limiting for production use
