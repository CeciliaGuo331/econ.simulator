@echo off
REM 为了正确显示中文字符，设置代码页为 UTF-8
chcp 65001 > nul

REM 格式化项目中的所有 Python 和模板文件

REM 确保你已经安装了 black 和 djlint
REM 可以使用以下命令在 CMD 或 PowerShell 中安装：
REM pip install black djlint

REM cd 到项目根目录并运行这个 .bat 文件
REM 你可以通过在命令行输入 "format_code.bat" 或直接双击该文件来运行

echo 格式化 Python 文件...
black .

echo 格式化模板文件...
djlint .

echo 格式化完成！

REM 暂停脚本，方便在双击运行时查看输出结果
pause