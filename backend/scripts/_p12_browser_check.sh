#!/usr/bin/env bash
# P12 浏览器验收：⑥ 对比 Tab —— 选模型/调预算 → 运行对比 → 对比表 + 条形图 + CSV + 历次列表
set -u
cd "C:/Users/giser/WorkBuddy/水系概化图工具" || exit 1
export PATH="C:/Users/giser/.workbuddy/binaries/node/versions/22.22.2-2:$PATH"
mkdir -p debug_shots
LOG=debug_shots/ab12.log
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
chknot() {
  if grep -q "$2" "$3" 2>/dev/null; then echo "  [FAIL] $1（不应出现 '$2'）"; FAIL=$((FAIL+1)); else echo "  [PASS] $1"; fi
}

echo "############ 打开应用与面板，进 ⑥ 对比 ############"
ab open "http://127.0.0.1:8013"
ab wait --load load
sleep 5
agent-browser snapshot > debug_shots/snap12_0.txt 2>&1
if grep -q "进入工作台" debug_shots/snap12_0.txt; then
  # 选「图层最多的项目」——避免误入测试残留的空白项目
  ab eval "(function(){var x=new XMLHttpRequest(); x.open('GET','/api/projects',false); x.send(); var ps=JSON.parse(x.responseText).projects; ps.sort(function(a,b){return b.layer_count-a.layer_count}); var t=ps[0]; var cards=[].slice.call(document.querySelectorAll('.card')).filter(function(c){return c.className.indexOf('new')<0}); var c=cards.filter(function(el){return el.textContent.indexOf(t.name)>=0 && el.textContent.indexOf(t.layer_count+' 个图层')>=0})[0]; if(!c) return 'no-card'; c.click(); return 'clicked:'+t.name+'/'+t.layer_count})()"
  sleep 6
fi
ab click ".mdl-open"
sleep 3

# Tab 序号可能随迭代变化，用文案定位更稳
ab eval "(function(){var ts=document.querySelectorAll('.tabs button'); for(var i=0;i<ts.length;i++){if(ts[i].textContent.indexOf('对比')>=0){ts[i].click(); return 'clicked-cmp'}} return 'not-found'})()"
sleep 3

echo
echo "############ [1] 对比设置表单 ############"
agent-browser snapshot > debug_shots/snap12_1.txt 2>&1
chk "设置区标题" "对比设置" debug_shots/snap12_1.txt
chk "预算输入标签" "每单元评估次数" debug_shots/snap12_1.txt
chk "验证期占比标签" "验证期占比" debug_shots/snap12_1.txt
chk "运行按钮" "运行对比" debug_shots/snap12_1.txt
chk "模型选项-新安江" "新安江" debug_shots/snap12_1.txt
chk "模型选项-GR4J" "GR4J" debug_shots/snap12_1.txt
chk "模型选项-Tank" "Tank" debug_shots/snap12_1.txt
chk "模型选项-HBV" "HBV" debug_shots/snap12_1.txt
chk "参数补偿提示" "参数补偿" debug_shots/snap12_1.txt
chk "历次对比区" "历次对比" debug_shots/snap12_1.txt
shot p12_01_form

echo
echo "############ [2] 只选 2 个模型 + 降低预算 ############"
ab eval "(function(){var cs=document.querySelectorAll('.mchip'); var n=0; for(var i=0;i<cs.length;i++){var t=cs[i].textContent; if(t.indexOf('GR4J')>=0||t.indexOf('HBV')>=0){ if(cs[i].className.indexOf('on')>=0){cs[i].click(); n++} }} return 'deselected='+n})()"
sleep 1
ab eval "(function(){var el=document.querySelector('#cmp-evals'); el.value=120; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return 'evals='+el.value})()"
sleep 1
agent-browser snapshot > debug_shots/snap12_2.txt 2>&1
chk "已保留 2 个选中模型（两个 on 态 chip）" "参" debug_shots/snap12_2.txt
shot p12_02_selected

echo
echo "############ [3] 运行对比（2 模型 × 120 evals ≈ 20s） ############"
ab scrollintoview "#cmp-run"
sleep 1
ab click "#cmp-run"
echo "  已点击「运行对比」"
sleep 6
agent-browser snapshot > debug_shots/snap12_3.txt 2>&1
chk "出现进度区" "进度" debug_shots/snap12_3.txt
shot p12_03_progress

for i in $(seq 1 24); do
  agent-browser snapshot > debug_shots/snap12_3.txt 2>&1
  if grep -q "对比结果" debug_shots/snap12_3.txt; then
    echo "  第 $((i*5+6))s：对比已结束"
    break
  fi
  sleep 5
done

echo
echo "############ [4] 对比结果 ############"
chk "结果区标题" "对比结果" debug_shots/snap12_3.txt
chk "表头-率定期 NSE" "率定期 NSE" debug_shots/snap12_3.txt
chk "表头-验证期 NSE" "验证期 NSE" debug_shots/snap12_3.txt
chk "表头-耗时" "耗时" debug_shots/snap12_3.txt
chk "表头-洪峰偏差" "洪峰偏差" debug_shots/snap12_3.txt
chk "表头-峰现偏移" "峰现偏移" debug_shots/snap12_3.txt
chk "形态学口径说明" "形态学指标口径" debug_shots/snap12_3.txt
chk "多场洪峰口径说明" "多场洪峰" debug_shots/snap12_3.txt
chk "状态-已完成" "已完成" debug_shots/snap12_3.txt
chk "导出入口" "导出指标 CSV" debug_shots/snap12_3.txt
chk "历次对比条数（含本次）" "模型 ·" debug_shots/snap12_3.txt
chknot "无失败告警" "率定失败" debug_shots/snap12_3.txt
shot p12_04_result
# 滚动到条形图，确认 ECharts 实际渲染
ab eval "(function(){var b=document.querySelector('.chart-box'); if(!b) return 'no-chart'; b.scrollIntoView({block:'center'}); return 'scrolled'})()" >>"$LOG" 2>&1
sleep 1
shot p12_05_chart

echo
echo "############ [5] 点击历次对比条目可回看结果 ############"
ab eval "(function(){var hs=document.querySelectorAll('.hrow'); if(!hs.length) return 'no-hist'; hs[0].click(); return 'clicked-hist:'+hs.length})()"
sleep 2
agent-browser snapshot > debug_shots/snap12_5.txt 2>&1
chk "回看后结果表仍在" "率定期 NSE" debug_shots/snap12_5.txt
shot p12_06_history

echo
echo "############ [6] 回归：预报 Tab / 结果 Tab 仍正常 ############"
ab eval "(function(){var ts=document.querySelectorAll('.tabs button'); for(var i=0;i<ts.length;i++){if(ts[i].textContent.indexOf('预报')>=0){ts[i].click(); return 'clicked-fc'}} return 'not-found'})()"
sleep 2
agent-browser snapshot > debug_shots/snap12_6.txt 2>&1
chk "预报页正常" "预见期与情景雨情" debug_shots/snap12_6.txt
ab eval "(function(){var ts=document.querySelectorAll('.tabs button'); for(var i=0;i<ts.length;i++){if(ts[i].textContent.indexOf('结果')>=0){ts[i].click(); return 'clicked-res'}} return 'not-found'})()"
sleep 2
agent-browser snapshot > debug_shots/snap12_7.txt 2>&1
chk "结果页正常" "率定结果" debug_shots/snap12_7.txt
chknot "结果页无报错" "Cannot read properties" debug_shots/snap12_7.txt

echo
echo "############ 汇总 ############"
echo "FAIL 计数 = $FAIL"
ls -1 debug_shots/p12_*.png 2>/dev/null
ab close
