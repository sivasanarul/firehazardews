@echo off
REM Start the SLIM Fire API using the local virtual environment.
cd /d "%~dp0"
REM The local API connects directly to FIRMS and GWIS. Ignore any stale shell proxy.
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found at .venv
  echo Run:  python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install fastapi uvicorn geopandas pandas pyogrio python-dotenv requests rio-tiler scikit-learn scipy pillow rasterio numpy shapely
  exit /b 1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"[ : ]8005 .*LISTENING"') do (
  echo [ERROR] Port 8005 is already in use by process %%P.
  echo Stop that process, or run .\run.ps1 -Restart to replace the local API.
  exit /b 1
)
echo Starting SLIM Fire API on http://127.0.0.1:8005 ...
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn fire_api:app --host 127.0.0.1 --port 8005 %*
