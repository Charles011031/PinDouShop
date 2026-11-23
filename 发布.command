#!/bin/bash
cd "$(dirname "$0")"
python3 发布.py "$@"
code=$?
echo
if [ $code -ne 0 ]; then echo "发布失败（退出码 $code），看上面日志。"; fi
read -n 1 -s -r -p "按任意键关闭窗口…"
echo
