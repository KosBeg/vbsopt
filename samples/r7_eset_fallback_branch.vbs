Dim feed, backup, c2
feed = Replace("hxxps://telegraph.example.invalid/example-primary", "hxxps", "https")
backup = ChrW(104) & "ttps://backup.example.invalid"
If use_feed Then
  c2 = feed
Else
  c2 = backup
End If
WScript.Echo c2
