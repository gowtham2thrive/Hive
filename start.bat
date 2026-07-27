@echo off
echo Starting Hive...

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    echo Installing dependencies...
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo The application will open in your default browser.
echo Press CTRL+C to stop the server.

:: Wait for the server to start, then open the browser
start /b .\venv\Scripts\python.exe -c "import time; import webbrowser; time.sleep(2); webbrowser.open('http://127.0.0.1:8080')"

:: Start the FastAPI server using the virtual environment
.\venv\Scripts\python.exe -m uvicorn backend.main:app --port 8080
