#!/usr/bin/env bash
# P6 浏览器验收：⑤ 预报 Tab（设计雨型 → 运行预报 → 过程线与洪峰摘要）
set -u
cd "C:/Users/giser/WorkBuddy/水系概化图工具" || exit 1
export PATH="C:/Users/giser/.workbuddy/binaries/node/versions/22.22.2-2:$PATH"
mkdir -p debug_shots
LOG=debug_shots/ab6.log
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

echo
echo "############ [1] 进入 ⑤ 预报 Tab ############"
ab click ".tabs button:nth-child(5)"
sleep 2
agent-browser snapshot > debug_shots/snap6_1.txt 2>&1
chk "Tab 预报" "预见期与情景雨情" debug_shots/snap6_1.txt
chk "设计雨型按钮" 'button "设计雨型"' debug_shots/snap6_1.txt
chk "逐日序列按钮" 'button "逐日序列"' debug_shots/snap6_1.txt
chk "运行预报按钮" "运行预报" debug_shots/snap6_1.txt
chk "空态提示" "尚未运行预报" debug_shots/snap6_1.txt
shot p6_01_tab

echo
echo "############ [2] 默认设计雨型（100mm/7天）运行预报 ############"
ab scrollintoview "#fc-run"
sleep 1
ab click "#fc-run"
echo "  已点击「运行预报」"
sleep 6
agent-browser snapshot > debug_shots/snap6_2.txt 2>&1
chk "预报过程线区" "预报过程线与降雨驱动" debug_shots/snap6_2.txt
chk "预报段洪峰摘要" "预报段洪峰摘要" debug_shots/snap6_2.txt
chk "情景描述" "设计雨型 100 mm / 7 天" debug_shots/snap6_2.txt
chk "洪峰单位" "m³/s" debug_shots/snap6_2.txt
chk "降雨摘要-历史段实测" "历史段实测面雨量" debug_shots/snap6_2.txt
chk "降雨摘要-预报段情景" "预报段情景降雨" debug_shots/snap6_2.txt
chk "单元切换 chips" "S01" debug_shots/snap6_2.txt
shot p6_02_result
# 滚动到图表再截一张（确认降雨柱与流量线 canvas 渲染）
ab eval "(function(){var f=document.querySelector('.chart-box'); if(!f) return 'no-chart'; f.scrollIntoView({block:'center'}); return 'ok'})()" >>"$LOG" 2>&1
sleep 1
shot p6_04_rain_chart

echo
echo "############ [3] 切逐日序列并重跑 ############"
ab scrollintoview ".seg button:nth-child(2)"
ab click ".seg button:nth-child(2)"
sleep 1
ab eval "const els=document.querySelectorAll('.series-in'); els.length?(()=>{const el=els[0]; el.value='60, 40, 20'; el.dispatchEvent(new Event('input',{bubbles:true})); return 'set'})():'not-found'"
sleep 1
ab scrollintoview "#fc-run"
ab click "#fc-run"
sleep 6
agent-browser snapshot > debug_shots/snap6_3.txt 2>&1
chk "逐日序列情景描述" "逐日预测序列" debug_shots/snap6_3.txt
shot p6_03_series

echo
echo "############ [4] 其他 Tab 正常返回 ############"
ab click ".tabs button:nth-child(2)"
sleep 2
agent-browser snapshot > debug_shots/snap6_4.txt 2>&1
chk "回到配置页" "参数集" debug_shots/snap6_4.txt

echo
echo "############ 汇总 ############"
echo "FAIL 计数 = $FAIL"
ls -1 debug_shots/p6_*.png 2>/dev/null
ab close
