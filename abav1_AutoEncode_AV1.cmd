set root_cmd_path="C:\Tools\Python\encode\abav1_AutoEncode_AV1_root.cmd"
call %root_cmd_path%
python -O "C:\Tools\Python\encode\main.py" --processes 3 --move-raw-file
pause