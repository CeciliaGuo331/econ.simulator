#!/bin/bash

# 格式化项目中的所有 Python 和模板文件

# 确保你已经安装了 black 和 djlint
# 可以使用以下命令安装：
# pip install black djlint

# cd 到项目根目录并运行下面这句命令
# bash code_formatter.sh

echo "格式化 Python 文件..."
black .

echo "格式化模板文件..."
djlint . --reformat

echo "格式化完成！"