Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\PostgreSQL\windows_archive_server.ps1 -Action Start", 0, False
