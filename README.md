# File Manager Backend API

A FastAPI-based file management system with JWT authentication.

## Features

- JWT-based authentication
- File upload/download management
- Folder creation/deletion/renaming
- File operations (upload, download, delete, rename)
- Webhook support for integrations
- Docker containerization

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
- `POST /api/files/upload` - Upload file
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
- `POST /webhook/files/delete` - Webhook for file deletion
- `POST /webhook/files/rename` - Webhook for file rename
- `POST /webhook/folders/create` - Webhook for folder creation
- `POST /webhook/folders/delete` - Webhook for folder deletion
- `POST /webhook/folders/rename` - Webhook for folder rename
- `GET /webhook/folders/status` - Webhook for folder status

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
