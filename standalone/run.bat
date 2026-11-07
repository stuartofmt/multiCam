@echo off
rem Start the program with its venv (made by Prepare in Prep_and_Package). With no venv - e.g. just
rem unzipped from a release - it is made first and plugin3.x.x\dsf\requirements.txt (with plugin3.x.x\dsf\requirements-windows.txt, if there is
rem one) is installed into it.
cd /d "%~dp0"
set REQS=-r "plugin3.x.x\dsf\requirements.txt"
if exist "plugin3.x.x\dsf\requirements-windows.txt" set REQS=%REQS% -r "plugin3.x.x\dsf\requirements-windows.txt"
if not exist venv\Scripts\python.exe (
  echo First run: making the venv and installing plugin3.x.x\dsf\requirements.txt into it
  py -3 -m venv venv 2>nul || python -m venv venv || goto novenv
  venv\Scripts\python.exe -m pip install %REQS% || goto nopip
)
venv\Scripts\python.exe -u plugin3.x.x\dsf\multiCam.py %*
set CODE=%errorlevel%
if not %CODE%==0 (
  echo.
  echo plugin3.x.x\dsf\multiCam.py stopped with an error ^(exit code %CODE%^) - see the messages above
  pause
)
exit /b %CODE%
:novenv
echo Could not make the venv: install Python 3 from python.org ^(tick "Add python.exe to PATH"^) and run this again
goto failed
:nopip
echo Could not install the requirements into the venv - see the pip messages above
:failed
rmdir /s /q venv 2>nul
pause
exit /b 1
