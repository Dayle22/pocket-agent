Set fso = CreateObject("Scripting.FileSystemObject")
repoDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = repoDir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw"
End If
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = repoDir
shell.Run """" & pythonw & """ -m pocket_agent", 0, False
