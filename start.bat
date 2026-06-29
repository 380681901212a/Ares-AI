@echo off
echo ==========================================
echo Starting Ares AI Ecosystem...
echo Make sure Ollama is running in the background!
echo ==========================================

echo Starting SearxNG Docker container...
docker start searxng

if not exist main_env\Scripts\activate.bat (
    echo ERROR: Virtual environment not found!
    echo Please run: python -m venv main_env
    pause
    exit /b 1
)

call main_env\Scripts\activate
echo Installing/Updating dependencies from requirements.txt...
pip install -r requirements.txt -q
echo Launching UI...
streamlit run ui.py
pause