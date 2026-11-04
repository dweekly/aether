# Recorded demo for the Hackers 2026 talk. Run from research/FlightSchoolMapping:
#   vhs scripts/demo_extract.tape
# Makes one live model call (about $0.02) and writes docs/hackers-2026-charts/demo_extract.{mp4,gif}.
Output ../../docs/hackers-2026-charts/demo_extract.mp4
Output ../../docs/hackers-2026-charts/demo_extract.gif

Set Shell "bash"
Set FontSize 22
Set Width 1600
Set Height 540
Set Padding 30
Set Theme "Builtin Dark"
Set TypingSpeed 45ms

Hide
Type "cd scripts && alias python=.venv/bin/python && export PS1='$ ' && clear" Enter
Show

Sleep 800ms
Type "python demo_extract.py https://skybnd.com"
Sleep 500ms
Enter
Wait+Line@60s /^\$$/
Sleep 7s
