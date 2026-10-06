' Dispatch Background Silent Launcher (Zero Terminal Popups)
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

' Current directory resolution
strScriptPath = FSO.GetParentFolderName(WScript.ScriptFullName)
strProjectRoot = FSO.GetParentFolderName(strScriptPath)

' Launch Python dispatch master daemon silently (Window style 0 = Hidden)
WshShell.CurrentDirectory = strProjectRoot
strCommand = "cmd.exe /c python -m dispatch.main"
WshShell.Run strCommand, 0, False
