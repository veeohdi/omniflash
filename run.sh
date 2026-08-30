#!/usr/bin/env bash

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 could not be found. Please install Python 3 first."
    exit 1
fi

echo "====================================================="
echo "Starting OmniFlash — Pixel 4 XL (coral) Flasher..."
echo "====================================================="

# Ensure we are in the script's directory
cd "$(dirname "$0")" || exit 1

# Run server in background
python3 server.py &
SERVER_PID=$!

# Wait for server to become responsive
python3 wait_for_server.py

# Open web browser
if command -v xdg-open &> /dev/null; then
    xdg-open "http://127.0.0.1:8086"
elif command -v open &> /dev/null; then
    open "http://127.0.0.1:8086"
else
    echo "Please open your browser manually at: http://127.0.0.1:8086"
fi

echo ""
echo "OmniFlash is now running at http://127.0.0.1:8086"
echo "Server PID: $SERVER_PID"
echo "Press [Ctrl+C] to stop the server."
echo ""

# Trap Ctrl+C (SIGINT) to kill background server
trap "echo 'Shutting down OmniFlash server...'; kill $SERVER_PID; exit" INT

wait $SERVER_PID
