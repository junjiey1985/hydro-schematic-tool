#!/usr/bin/env bash
# P4 四步式率定工作台 —— 真实浏览器验收（一次性批量执行）
#
# 注意：agent-browser 的命令**绝不能**接管道 —— daemon 会继承 stdout 并长期持有，
# 导致 `| tail` 之类的下游永远等不到 EOF 而挂死（曾因此卡 13 分钟）。
# 所有调用一律重定向到文件。
set -u
cd "C:/Users/giser/WorkBuddy/水系概化图工具" || exit 1
export PATH="C:/Users/giser/.workbuddy/binaries/node/versions/22.22.2-2:$PATH"
mkdir -p debug_shots
LOG=debug_shots/ab.log
: > "$LOG"
FAIL=0

ab() { agent-browser "$@" >>"$LOG" 2>&1; }

shot() {
  agent-browser screenshot --screenshot-dir debug_shots >>"$LOG" 2>&1
  f=$(ls -t debug_shots/*.png 2>/dev/null | head -1)
  [ -n "$f" ] && mv "$f" "debug_shots/$1.png" && echo "  [截图] $1.png"
}
chk() { # chk <名称> <期望子串> <快照文件>
  if grep -q "$2" "$3" 2>/dev/null; then echo "  [PASS] $1"; else echo "  [FAIL] $1（未找到 '$2'）"; FAIL=$((FAIL+1)); fi
}

echo "############ 打开应用 ############"
ab open "http://127.0.0.1:8013"
ab wait --load load
sleep 5

echo
echo "############ [1] 打开「模型与率定」面板 ############"
ab click ".mdl-open"
sleep 3
agent-browser snapshot > debug_shots/snap_1.txt 2>&1
chk "顶栏按钮已改名" "模型与率定" debug_shots/snap_1.txt
chk "Tab 数据检查" "数据检查" debug_shots/snap_1.txt
chk "Tab 配置" "配置" debug_shots/snap_1.txt
chk "Tab 运行" "运行" debug_shots/snap_1.txt
chk "Tab 结果" "结果" debug_shots/snap_1.txt
chk "默认落在配置页（参数集）" "参数集" debug_shots/snap_1.txt
chk "锁定列已渲染" "锁定" debug_shots/snap_1.txt
chk "率定区间列已渲染" "率定区间" debug_shots/snap_1.txt
chk "模拟时段区已渲染" "模拟时段" debug_shots/snap_1.txt
chk "率定设置区已渲染" "率定设置" debug_shots/snap_1.txt
chk "启动率定按钮" "启动率定" debug_shots/snap_1.txt
shot p4_01_config

echo
echo "############ [2] Tab 数据检查 ############"
ab click ".tabs button:nth-child(1)"
sleep 3
agent-browser snapshot > debug_shots/snap_2.txt 2>&1
chk "逐单元覆盖率表" "逐单元覆盖率" debug_shots/snap_2.txt
chk "面雨量覆盖列" "面雨量覆盖" debug_shots/snap_2.txt
chk "结论列（可率定）" "可率定" debug_shots/snap_2.txt
chk "序列清单" "序列清单" debug_shots/snap_2.txt
chk "缺测率列" "缺测率" debug_shots/snap_2.txt
chk "可率定单元卡片" "可率定单元" debug_shots/snap_2.txt
shot p4_02_check

echo
echo "############ [3] Tab 运行（空态） ############"
ab click ".tabs button:nth-child(3)"
sleep 2
agent-browser snapshot > debug_shots/snap_3.txt 2>&1
chk "运行页空态提示" "还没有率定任务" debug_shots/snap_3.txt
chk "历次任务列表" "历次任务" debug_shots/snap_3.txt
shot p4_03_run_empty

echo
echo "############ [4] 回配置页，启动率定（3000 次评估/单元） ############"
ab click ".tabs button:nth-child(2)"
sleep 2
ab scrollintoview "#cal-start"
sleep 1
ab click "#cal-start"
echo "  已点击「启动率定」"
sleep 10
agent-browser snapshot > debug_shots/snap_4.txt 2>&1
chk "自动跳到运行页" "当前任务" debug_shots/snap_4.txt
chk "进度卡片（当前单元）" "当前单元" debug_shots/snap_4.txt
chk "收敛曲线区" "收敛曲线" debug_shots/snap_4.txt
shot p4_04_running

echo
echo "############ [5] 等率定跑完 ############"
for i in $(seq 1 18); do
  sleep 10
  agent-browser snapshot > debug_shots/snap_5.txt 2>&1
  if grep -q "查看率定结果" debug_shots/snap_5.txt; then
    echo "  第 $((i*10))s：任务已结束"
    break
  fi
  echo "  第 $((i*10))s：仍在运行…"
done
chk "率定已结束（出现「查看率定结果」）" "查看率定结果" debug_shots/snap_5.txt
chk "状态徽标已结束" "已完成" debug_shots/snap_5.txt
chk "历次任务表已出结果" "已生成" debug_shots/snap_5.txt
shot p4_05_run_done

echo
echo "############ [6] Tab 结果 ############"
ab scrollintoview ".tabs button:nth-child(4)"
sleep 1
ab click ".tabs button:nth-child(4)"
sleep 5
agent-browser snapshot > debug_shots/snap_6.txt 2>&1
chk "自动切到率定结果数据源" "率定结果" debug_shots/snap_6.txt
chk "单元率定情况表" "单元率定情况" debug_shots/snap_6.txt
chk "真值参数回收对比" "真值参数回收对比" debug_shots/snap_6.txt
chk "单元过程线区" "单元过程线" debug_shots/snap_6.txt
chk "洪峰明细（全时段按钮）" "全时段" debug_shots/snap_6.txt
chk "水量平衡表" "水量平衡" debug_shots/snap_6.txt
chk "采纳按钮" "采纳为项目参数" debug_shots/snap_6.txt
shot p4_06_result

echo
echo "############ [7] 模拟结果数据源（无模拟数据时应为禁用态） ############"
ab scrollintoview ".src-btn"
sleep 1
ab click ".src-btn:nth-child(1)"
sleep 2
agent-browser snapshot > debug_shots/snap_7.txt 2>&1
# 设计如此：无模拟数据时「模拟结果」按钮 disabled（而非显示空态），防止用户切到无内容的页签
chk "无模拟数据时按钮禁用" '模拟结果" \[disabled' debug_shots/snap_7.txt
chk "仍停留在率定结果" "单元率定情况" debug_shots/snap_7.txt
shot p4_07_result_sim_empty

echo
echo "############ [8] 关闭面板 ############"
ab scrollintoview ".foot .btn.primary"
sleep 1
ab click ".foot .btn.primary"
sleep 2
agent-browser snapshot > debug_shots/snap_8.txt 2>&1
if grep -q "单元率定情况" debug_shots/snap_8.txt; then
  echo "  [FAIL] 面板未关闭"
  FAIL=$((FAIL+1))
else
  echo "  [PASS] 面板已关闭"
fi

echo
echo "############ 汇总 ############"
echo "FAIL 计数 = $FAIL"
ls -1 debug_shots/p4_*.png 2>/dev/null
echo "--- agent-browser 日志尾部 ---"
tail -12 "$LOG"
ab close
