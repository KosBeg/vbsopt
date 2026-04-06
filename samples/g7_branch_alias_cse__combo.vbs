base = "hxxps://node.e" & "xample.invalid"
' generated-noise
unused_generated = "AAAA"

suffix = "/api"
If True Then
    cur = base & suffix
Else
    cur = "hxxps://no" & "ise.invalid" & suffix
End If
x = cur & "?id=1"
y = cur & "?id=1"
WScript.Echo x
WScript.Echo y