# README demo GIF. Record from the repo root (asciifx installed and on PATH):
#   vhs docs/demo.tape
# https://github.com/charmbracelet/vhs  (needs ttyd + ffmpeg)

Output docs/demo.gif

Require asciifx

Set Shell "bash"
Set Theme "Dracula"
# Any monospace font with braille (U+2800) and block (U+2580) glyphs works.
Set FontFamily "DejaVu Sans Mono"
Set FontSize 16
# About 100x32 cells at this font size.
Set Width 1000
Set Height 660
Set Padding 16
Set TypingSpeed 40ms
Set Framerate 30

Env COLORTERM "truecolor"
Env ASCIIFX_NO_BANNER "1"
Env ASCIIFX_NO_SCREENSAVER "1"

Hide
Type "clear"
Enter
Show

Type "asciifx banner 'asciifx' --effect decode"
Sleep 300ms
Enter
Sleep 3s

Type "asciifx summon --logo skull_logo.png --name asciifx --finale eyes --seed 1 --no-banner"
Sleep 300ms
Enter
Sleep 15s
