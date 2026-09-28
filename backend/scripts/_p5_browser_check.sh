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
PID="${PID:-p_dee39fceeab6}"

ab() { agent-browser "$@" >>"$LOG" 2>&1; }
shot() {
  agent-browser screenshot --screenshot-dir debug_shots >>"$LOG" 2>&1
  f=$(ls -t debug_shots/*.png 2>/dev/null | head -1)
  [ -n "$f" ] && mv "$f" "debug_shots/$1.png" && echo "  [截图] $1.png"
}
chk() {
  if grep -q "$2" "$3" 2>/dev/null; then echo "  [PASS] $1"; else echo "  [FAIL] $1（未找到 '$2'）"; FAIL=$((FAIL+1)); fi
}

echo "############ 打开应用（P8 起默认落开始页） ############"
ab open "http://127.0.0.1:8013"
ab wait --load load
sleep 5
agent-browser snapshot > debug_shots/snap5_0.txt 2>&1
if grep -q "进入工作台" debug_shots/snap5_0.txt; then
  # 选「图层最多的项目」——避免误入测试残留的空白项目
  ab eval "(function(){var x=new XMLHttpRequest(); x.open('GET','/api/projects',false); x.send(); var ps=JSON.parse(x.responseText).projects; ps.sort(function(a,b){return b.layer_count-a.layer_count}); var t=ps[0]; var cards=[].slice.call(document.querySelectorAll('.card')).filter(function(c){return c.className.indexOf('new')<0}); var c=cards.filter(function(el){return el.textContent.indexOf(t.name)>=0 && el.textContent.indexOf(t.layer_count+' 个图层')>=0})[0]; if(!c) return 'no-card'; c.click(); return 'clicked:'+t.name+'/'+t.layer_count})()"
  sleep 6
  echo "  已从开始页进入项目"
fi
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
echo "############ [2] 切到联合模式（并读出维度提示） ############"
ab scrollintoview ".seg button:nth-child(2)"
ab click ".seg button:nth-child(2)"
sleep 1
agent-browser snapshot > debug_shots/snap5_2.txt 2>&1
chk "联合模式提示" "全站同时优化" debug_shots/snap5_2.txt
chk "维度提示" "建议最大评估 ≥" debug_shots/snap5_2.txt

echo
echo "############ [3] 勾选「共享」（留最后一列不共享，保持有区分度） ############"
# 共享列 = 参数表每行第 3 个 td 的 checkbox；共享 key 随模型而变（XAJ=K，HBV=FC…），
# 故不写死参数名，按行数动态勾选前 (行数-4) 行。
ab scrollintoview ".param-wrap"
ab eval "(function(){var rs=document.querySelectorAll('.param-wrap tbody tr'); var n=Math.max(1, rs.length-4); var k=0; for(var i=0;i<n;i++){var cb=rs[i].querySelector('td:nth-child(3) input'); if(cb){cb.click(); k++;}} return 'rows='+rs.length+' shared='+k})()"
sleep 1
echo "  勾选情况：$(grep -oE 'rows=[0-9]+ shared=[0-9]+' "$LOG" | tail -1)"
agent-browser snapshot > debug_shots/snap5_3.txt 2>&1
shot p5_02_config_joint

echo
echo "############ [4] 按维度自适应设置预算并启动联合率定 ############"
# SCE-UA 一代评估数 = s·m = max(2,p)·(2p+1)；预算低于它则 gens=0、收敛曲线为空。
# 从页面的「约 p 维」提示反推 p，设 max_evals = 一代点数 + 余量，既跑得动又能出收敛点。
ab eval "(function(){var m=document.body.innerText.match(/约\\s*(\\d+)\\s*维/); var p=m?parseInt(m[1]):0; var npts=Math.max(2,p)*(2*p+1); var b=npts+400; var el=document.querySelector('#cal-max-evals'); if(!el) return 'no-input'; el.value=b; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return 'p='+p+' npts='+npts+' budget='+el.value})()"
sleep 1
echo "  维度与预算：$(grep -oE 'p=[0-9]+ npts=[0-9]+ budget=[0-9]+' "$LOG" | tail -1)"
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
echo "############ [5] 等联合率定结束（预算=一代点数，约 1~3 分钟） ############"
for i in $(seq 1 40); do
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
chk "共享参数标注" "共享" debug_shots/snap5_6.txt
shot p5_05_result

echo
echo "############ [7] 导出参数 CSV 可下载 ############"
curl -s -o debug_shots/p5_params_export.csv "http://127.0.0.1:8013/api/projects/$PID/calibration/export?what=params"
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
