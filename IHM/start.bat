@echo off
setlocal

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Erro: Python nao encontrado. Por favor, instale o Python em python.org
    pause
    exit /b
)

:: Navigate to script directory
cd /d %~dp0

:: Create virtual environment if it doesn't exist
if not exist venv (
    echo Criando ambiente virtual...
    python -m venv venv
)

:: Activate virtual environment
call venv\Scripts\activate

:: Install requirements
echo Instalando dependencias...
pip install -r requirements.txt -q

:: Run the app
echo Iniciando aplicacao...
python main.py

pause
