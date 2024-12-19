set root_cmd_path="C:\Tools\Python\encode\abav1_AutoEncode_AV1_root.cmd"
call %root_cmd_path%
python -O "C:\Tools\Python\encode\iPhone_encode_main.py" --processes 5 --not-rename --audio-only --move-raw-file
pause