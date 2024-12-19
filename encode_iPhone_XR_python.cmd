set conda_cmd_path="C:\Tools\cmds\lunch_conda.cmd"
call %conda_cmd_path%
call activate Encode
git pull
pip install -r requirements.txt
python -O "C:\Tools\Python\encode\iPhone_encode_main.py" --processes 1 --not-rename
pause