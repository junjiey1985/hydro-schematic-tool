#!/usr/bin/env bash
# P5 浏览器验收：率定模式切换（链式/联合）+ 参数共享 + 联合率定全流程 + 导出按钮
#
# 注意：agent-browser 的命令绝不能接管道（daemon 继承 stdout 导致下游等不到 EOF），
# 一律重定向到文件。
set -u
cd "C:/Users/giser/WorkBuddy/水系概化图工具" || exit 1
export PATH="C:/Users/giser/.workbuddy/binaries/node/versions/22.22.2-2:$PATH"
mkdir -p debug_shots
LOG=debug_shots/ab5.log
: > "$LOG"
FAIL=0

ab() { agent-browser "$@" >>"$LOG" 2>&1; }
shot() {
  agent-browser screenshot --screenshot-dir debug_shots >>"$LOG" 2>&1
  f=$(ls -t debug_shots/*.png 2>/dev/null | head -1)
  [ -n "$f" ] && mv "$f" "debug_shots/$1.png" && echo "  [截图] $1.png"
}
chk() {
  if grep -q "$2" "$3" 2>/dev/null; then echo "  [PASS] $1"; else echo "  [FAIL] $1（未找到 '$2'）"; FAIL=$((FAIL+1)); fi
}

echo "############ 打开应用与面板 ############"
ab open "http://127.0.0.1:8013"
ab wait --load load
sleep 5
ab click ".mdl-open"
sleep 3
agent-browser snapshot > debug_shots/snap5_1.txt 2>&1

echo
echo "############ [1] 配置页：模式切换与共享列 ############"
chk "模式-链式按钮" 'button "链式"' debug_shots/snap5_1.txt
chk "模式-联合按钮" 'button "联合"' debug_shots/snap5_1.txt
chk "共享列表头" "联合模式生效" debug_shots/snap5_1.txt
chk "默认链式提示" "自上而下逐单元优化" debug_shots/snap5_1.txt
shot p5_01_config_chain

echo
echo "############ [2] 切到联合模式 ############"
ab scrollintoview ".seg button:nth-child(2)"
ab click ".seg button:nth-child(2)"
sleep 1
agent-browser snapshot > debug_shots/snap5_2.txt 2>&1
chk "联合模式提示" "全站同时优化" debug_shots/snap5_2.txt
chk "维度提示" "建议最大评估 ≥" debug_shots/snap5_2.txt

echo
echo "############ [3] 勾选 K 共享（首参数行第 3 列 checkbox） ############"
ab scrollintoview ".param-wrap"
ab eval "const cb=document.querySelector('.param-wrap tbody tr:nth-child(1) td:nth-child(3) input'); cb?(()=>{cb.click();return 'clicked:'+cb.checked})():'not-found'"
sleep 1
agent-browser snapshot > debug_shots/snap5_3.txt 2>&1
shot p5_02_config_joint

echo
echo "############ [4] 降低评估次数并启动联合率定 ############"
ab scrollintoview "#cal-max-evals"
ab eval "const el=document.querySelector('#cal-max-evals'); el.value=600; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); 'set='+el.value"
sleep 1
ab scrollintoview "#cal-start"
ab click "#cal-start"
echo "  已点击「启动率定」"
sleep 8
agent-browser snapshot > debug_shots/snap5_4.txt 2>&1
chk "自动跳运行页" "当前任务" debug_shots/snap5_4.txt
chk "联合任务显示 JOINT" "JOINT" debug_shots/snap5_4.txt
chk "联合优化提示" "联合优化 3 个单元" debug_shots/snap5_4.txt
shot p5_03_running

echo
echo "############ [5] 等联合率定结束（600 评估约 15-25s） ############"
for i in $(seq 1 15); do
  sleep 6
  agent-browser snapshot > debug_shots/snap5_5.txt 2>&1
  if grep -q "查看率定结果" debug_shots/snap5_5.txt; then
    echo "  第 $((i*6))s：任务已结束"
    break
  fi
  echo "  第 $((i*6))s：仍在运行…"
done
chk "联合率定已结束" "查看率定结果" debug_shots/snap5_5.txt
chk "收敛曲线已绘制" "收敛曲线" debug_shots/snap5_5.txt
shot p5_04_run_done

echo
echo "############ [6] 结果页：联合标识与导出按钮 ############"
ab scrollintoview ".tabs button:nth-child(4)"
sleep 1
ab click ".tabs button:nth-child(4)"
sleep 5
agent-browser snapshot > debug_shots/snap5_6.txt 2>&1
chk "联合率定说明" "联合率定：全站同时优化" debug_shots/snap5_6.txt
chk "导出参数 CSV 按钮" "导出参数 CSV" debug_shots/snap5_6.txt
chk "导出过程线 CSV 按钮" "导出过程线 CSV" debug_shots/snap5_6.txt
chk "K 共享标注" "共享" debug_shots/snap5_6.txt
shot p5_05_result

echo
echo "############ [7] 导出参数 CSV 可下载 ############"
PID=$(ab eval "location.search" >/dev/null 2>&1; echo "")
curl -s -o debug_shots/p5_params_export.csv "http://127.0.0.1:8013/api/projects/p_dee39fceeab6/calibration/export?what=params"
head -2 debug_shots/p5_params_export.csv | sed 's/^\xef\xbb\xbf//'
if grep -q "unit_code,param,value" debug_shots/p5_params_export.csv; then
  echo "  [PASS] 参数 CSV 内容正确"
else
  echo "  [FAIL] 参数 CSV 内容异常"
  FAIL=$((FAIL+1))
fi

echo
echo "############ [8] 关闭面板 ############"
ab scrollintoview ".foot .btn.primary"
sleep 1
ab click ".foot .btn.primary"
sleep 2
agent-browser snapshot > debug_shots/snap5_8.txt 2>&1
if grep -q "单元率定情况" debug_shots/snap5_8.txt; then
  echo "  [FAIL] 面板未关闭"
  FAIL=$((FAIL+1))
else
  echo "  [PASS] 面板已关闭"
fi

echo
echo "############ 汇总 ############"
echo "FAIL 计数 = $FAIL"
ls -1 debug_shots/p5_*.png 2>/dev/null
ab close
