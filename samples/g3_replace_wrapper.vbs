Function Clean(x)
    Clean = Replace(Replace(x, "@", ""), "[.]", ".")
End Function
url = Clean("hxxps://cdn@1[.]example[.]invalid/stage")
obj = Clean("WS@cript[.]Shell")
WScript.Echo url
WScript.Echo obj
