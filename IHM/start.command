#!/bin/bash
cd "$(dirname "$0")"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment. This might take a few seconds on first run..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install requirements if not already satisfied
pip install -r requirements.txt -q

# Run the app
python main.py
