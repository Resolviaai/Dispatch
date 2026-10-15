' Dispatch Background Silent Launcher (Zero Terminal Popups)
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

' Current directory resolution
strScriptPath = FSO.GetParentFolderName(WScript.ScriptFullName)
strProjectRoot = FSO.GetParentFolderName(strScriptPath)

' Check for virtual environment python
strVenvPython = strProjectRoot & "\.venv\Scripts\python.exe"
If FSO.FileExists(strVenvPython) Then
    strPyExe = """" & strVenvPython & """"
Else
    strPyExe = "python"
End If

' Ensure storage directory exists for logs
strStorageDir = strProjectRoot & "\storage"
If Not FSO.FolderExists(strStorageDir) Then
    FSO.CreateFolder(strStorageDir)
End If

strLogFile = strStorageDir & "\dispatch_daemon.log"
WshShell.CurrentDirectory = strProjectRoot

' Launch Python dispatch master daemon silently with stderr/stdout redirection
strCommand = "cmd.exe /c " & strPyExe & " -m dispatch.main >> """ & strLogFile & """ 2>&1"
WshShell.Run strCommand, 0, False
