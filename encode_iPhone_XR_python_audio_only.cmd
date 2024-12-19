chcp 65001
call "C:\Users\lapi\anaconda3\Scripts\activate.bat"
call activate Encode
python -O "C:\Tools\Python\encode\iPhone_encode_main.py" --processes 5 --not-rename --audio-only --move-raw-file
pause