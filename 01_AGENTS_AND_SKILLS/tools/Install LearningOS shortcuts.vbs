Option Explicit
Dim files, shell, tools, root, desktop, target, shortcut, destination
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
tools = files.GetParentFolderName(WScript.ScriptFullName)
root = files.GetParentFolderName(files.GetParentFolderName(tools))
desktop = shell.SpecialFolders("Desktop")
target = files.BuildPath(tools, "LearningOS.cmd")
destination = files.BuildPath(desktop, "LearningOS.lnk")
If Not files.FileExists(destination) Then
  Set shortcut = shell.CreateShortcut(destination)
  shortcut.TargetPath = target
  shortcut.WorkingDirectory = root
  shortcut.Description = "Refresh and open your offline Learning Atlas"
  shortcut.IconLocation = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\imageres.dll,174"
  shortcut.Save
End If
destination = files.BuildPath(desktop, "LearningOS Study Menu.lnk")
If Not files.FileExists(destination) Then
  Set shortcut = shell.CreateShortcut(destination)
  shortcut.TargetPath = target
  shortcut.Arguments = "menu"
  shortcut.WorkingDirectory = root
  shortcut.Description = "Set up exams, add material, practise and review results"
  shortcut.IconLocation = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\imageres.dll,174"
  shortcut.Save
End If
WScript.Echo "Ready. Use LearningOS for your Atlas, or LearningOS Study Menu for numbered study choices. Existing shortcuts were left unchanged."
