#!/bin/bash

# Load environment variables from .env file
set -a
source .env
set +a

# Start the backend
python -m pip install -r requirements.txt --index-url https://pypi.org/simple
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload