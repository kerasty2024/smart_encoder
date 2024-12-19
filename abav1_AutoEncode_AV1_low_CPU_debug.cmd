set conda_cmd_path="C:\Tools\cmds\lunch_conda.cmd"
call %conda_cmd_path%
call activate Encode
git pull
pip install -r requirements.txt
python "C:\Tools\Python\encode\main.py" --processes 1 --move-raw-file
pause