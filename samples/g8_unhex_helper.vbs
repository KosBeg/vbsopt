Function Unhex(v)
    Unhex = Chr(CLng("&H" & Mid(v, 1, 2))) & _
            Chr(CLng("&H" & Mid(v, 3, 2))) & _
            Chr(CLng("&H" & Mid(v, 5, 2))) & _
            Chr(CLng("&H" & Mid(v, 7, 2))) & _
            Chr(CLng("&H" & Mid(v, 9, 2))) & _
            Chr(CLng("&H" & Mid(v, 11, 2))) & _
            Chr(CLng("&H" & Mid(v, 13, 2))) & _
            Chr(CLng("&H" & Mid(v, 15, 2))) & _
            Chr(CLng("&H" & Mid(v, 17, 2))) & _
            Chr(CLng("&H" & Mid(v, 19, 2))) & _
            Chr(CLng("&H" & Mid(v, 21, 2))) & _
            Chr(CLng("&H" & Mid(v, 23, 2))) & _
            Chr(CLng("&H" & Mid(v, 25, 2)))
End Function
obj = Unhex("575363726970742E5368656C6C")
WScript.Echo obj
