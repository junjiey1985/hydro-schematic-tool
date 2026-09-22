#!/usr/bin/env bash
# P7 浏览器验收：先链式率定出结果 → ④ 结果 Tab → GLUE 区块（运行 → 置信带 + 行为参数范围）
set -u
cd "C:/Users/giser/WorkBuddy/水系概化图工具" || exit 1
export PATH="C:/Users/giser/.workbuddy/binaries/node/versions/22.22.2-2:$PATH"
mkdir -p debug_shots
LOG=debug_shots/ab7.log
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

echo "############ 打开应用与面板，进 ② 配置 ############"
ab open "http://127.0.0.1:8013"
ab wait --load load
sleep 5
ab click ".mdl-open"
sleep 3
ab click ".tabs button:nth-child(2)"
sleep 2

echo
echo "############ [1] 链式模式降低评估次数并启动率定 ############"
ab scrollintoview "#cal-max-evals"
ab eval "(function(){var el=document.querySelector('#cal-max-evals'); el.value=200; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return 'set='+el.value})()"
sleep 1
ab scrollintoview "#cal-start"
ab click "#cal-start"
echo "  已点击「启动率定」（链式 200 evals）"
sleep 6
for i in $(seq 1 15); do
  agent-browser snapshot > debug_shots/snap7_0.txt 2>&1
  if grep -q "查看率定结果" debug_shots/snap7_0.txt; then
    echo "  第 $((i*5+6))s：率定已结束"
    break
  fi
  sleep 5
done
chk "率定已结束" "查看率定结果" debug_shots/snap7_0.txt

echo
echo "############ [2] 进 ④ 结果 Tab（率定视图） ############"
ab scrollintoview ".tabs button:nth-child(4)"
sleep 1
ab click ".tabs button:nth-child(4)"
sleep 5
agent-browser snapshot > debug_shots/snap7_1.txt 2>&1
chk "GLUE 区块标题" "参数不确定性（GLUE）" debug_shots/snap7_1.txt
chk "GLUE 运行按钮" "运行 GLUE 分析" debug_shots/snap7_1.txt
chk "采样数输入" "采样数" debug_shots/snap7_1.txt
chk "阈值输入" "阈值 NSE" debug_shots/snap7_1.txt
shot p7_01_glue_form

echo
echo "############ [3] 运行 GLUE 分析（500 样本 ≈ 8s） ############"
ab scrollintoview "#glue-run"
sleep 1
ab click "#glue-run"
echo "  已点击「运行 GLUE 分析」"
sleep 15
agent-browser snapshot > debug_shots/snap7_3.txt 2>&1
chk "行为参数后验范围表" "行为参数后验范围" debug_shots/snap7_3.txt
chk "置信带图例（note）" "90% 置信区间" debug_shots/snap7_3.txt
chk "q50 说明（note）" "50% 分位（中心趋势）" debug_shots/snap7_3.txt
chk "NSE 最优图例" "NSE 最优" debug_shots/snap7_3.txt
chk "实测说明（note）" "黑点为实测" debug_shots/snap7_3.txt
chk "运行统计" "用时" debug_shots/snap7_3.txt
chk "覆盖率" "区间覆盖率" debug_shots/snap7_3.txt
chk "参数行 K" "K" debug_shots/snap7_3.txt
chk "行为样本 chips" "行为" debug_shots/snap7_3.txt
shot p7_02_glue_result
# 滚动到 GLUE 置信带图再截一张（确认 ECharts canvas 实际渲染）
ab eval "(function(){var boxes=document.querySelectorAll('.chart-box'); if(!boxes.length) return 'no-chart'; boxes[boxes.length-1].scrollIntoView({block:'center'}); return 'scrolled'})()" >>"$LOG" 2>&1
sleep 1
shot p7_04_band_chart

echo
echo "############ [4] 切换站点 chips ############"
ab eval "(function(){var btns=document.querySelectorAll('.ubtn'); if(btns.length>1){btns[btns.length-1].click(); return 'clicked:'+btns.length}else{return 'only-one'}})()"
sleep 1
agent-browser snapshot > debug_shots/snap7_4.txt 2>&1
chk "站点切换后图表仍在" "90% 置信带" debug_shots/snap7_4.txt
shot p7_03_glue_station2

echo
echo "############ [5] 预报 Tab 回归 ############"
ab eval "(function(){var ts=document.querySelectorAll('.tabs button'); for(var i=0;i<ts.length;i++){if(ts[i].textContent.indexOf('预报')>=0){ts[i].click(); return 'clicked-tab5'}} return 'not-found'})()"
sleep 3
agent-browser snapshot > debug_shots/snap7_5.txt 2>&1
chk "预报页正常" "预见期与情景雨情" debug_shots/snap7_5.txt

echo
echo "############ 汇总 ############"
echo "FAIL 计数 = $FAIL"
ls -1 debug_shots/p7_*.png 2>/dev/null
ab close
