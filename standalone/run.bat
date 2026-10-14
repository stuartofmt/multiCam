@echo off
rem Start the program with its venv (made by Prepare in Prep_and_Package). With no venv - e.g. just
rem unzipped from a release - it is made first and dsf\requirements.txt is installed into it.
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo First run: making the venv and installing dsf\requirements.txt into it
  py -3 -m venv venv 2>nul || python -m venv venv || goto failed
  venv\Scripts\python.exe -m pip install -r dsf\requirements.txt || goto failed
)
venv\Scripts\python.exe -u dsf\multiCam.py %*
exit /b
:failed
echo Could not make the venv: install Python 3 from python.org and run this again
rmdir /s /q venv 2>nul
exit /b 1
