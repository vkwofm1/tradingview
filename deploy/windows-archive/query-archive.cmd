@echo off
set PGCLIENTENCODING=UTF8
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\PostgreSQL\windows_archive_server.ps1 -Action Start
if errorlevel 1 exit /b 1
D:\PostgreSQL\16.15\pgsql\bin\psql.exe -X -w -h 127.0.0.1 -p 55432 -U archive_reader -d tradingview_archive
