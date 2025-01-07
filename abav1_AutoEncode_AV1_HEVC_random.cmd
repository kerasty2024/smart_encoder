set conda_cmd_path="C:\Tools\cmds\lunch_conda.cmd"
call %conda_cmd_path%
call activate Encode
python -O "C:\Tools\Python\encode\main.py" --processes 3 --random --move-raw-file --not-rename
pause