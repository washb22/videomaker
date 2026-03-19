@echo off
echo === VideoMaker Setup ===
pip install -r requirements.txt
echo.
echo === Starting VideoMaker on port 5001 ===
python app.py
pause
