base = "hxxps://node.example.invalid"
' generated-noise
unused_generated = "AAAA"

suffix = "/api"
If True Then
    cur = base & suffix
Else
    cur = "hxxps://noise.invalid" & suffix
End If
x = cur & "?id=1"
y = cur & "?id=1"
WScript.Echo x
WScript.Echo y