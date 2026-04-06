Function Clean(x)
' generated-noise
unused_generated = "AAAA"

    Clean = Replace(Replace(x, "@", ""), "[.]", ".")
End Function
url = Clean("hxxps://cdn@1[.]exa" & "mple[.]invalid/stage")
obj = Clean("WS@cript" & "[.]Shell")
WScript.Echo url
WScript.Echo obj