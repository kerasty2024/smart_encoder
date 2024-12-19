chcp 65001
echo %0
if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" (
    set anaconda_path="%USERPROFILE%\anaconda3\Scripts\activate.bat"
)
if exist "%ALLUSERSPROFILE%\anaconda3\Scripts\activate.bat" (
    set anaconda_path="%ALLUSERSPROFILE%\anaconda3\Scripts\activate.bat"
)
call %anaconda_path%
call activate Encode
git pull
