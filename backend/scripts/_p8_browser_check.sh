#!/usr/bin/env bash
# P8 开始页（项目卡片）浏览器验收
set -u
cd "$(dirname "$0")/../.."
mkdir -p debug_shots
export PATH="C:/Users/giser/.workbuddy/binaries/node/versions/22.22.2-2:$PATH"

PASS=0; FAIL=0
chk() { # chk 描述 文本 文件
  if grep -q "$2" "$3" 2>/dev/null; then
    echo "PASS  $1"; PASS=$((PASS+1))
  else
    echo "FAIL  $1"; FAIL=$((FAIL+1))
  fi
}
ab() { agent-browser "$@" > debug_shots/ab.log 2>&1; }
shot() { agent-browser screenshot --screenshot-dir debug_shots > /dev/null 2>&1; f=$(ls -t debug_shots/screenshot-*.png 2>/dev/null | head -1); [ -n "$f" ] && mv "$f" "debug_shots/$1.png"; }

echo "############ [1] 开始页加载 ############"
agent-browser close > /dev/null 2>&1
agent-browser open "http://127.0.0.1:8013" > debug_shots/ab.log 2>&1
sleep 6
agent-browser snapshot > debug_shots/snap8_1.txt 2>&1
chk "品牌标题（新名称）" "流域水文建模平台" debug_shots/snap8_1.txt
chk "新建项目按钮" 'button "＋ 新建项目"' debug_shots/snap8_1.txt
if grep -q "导入示例流域" debug_shots/snap8_1.txt; then
  echo "FAIL  已移除「导入示例流域」按钮"; FAIL=$((FAIL+1))
else
  echo "PASS  已移除「导入示例流域」按钮"; PASS=$((PASS+1))
fi
chk "项目卡片（堵河）" "堵河" debug_shots/snap8_1.txt
chk "拓扑标记" "拓扑" debug_shots/snap8_1.txt
chk "图层计数" "个图层" debug_shots/snap8_1.txt
chk "工作流徽标（时序）" "时序" debug_shots/snap8_1.txt
chk "工作流徽标（率定）" "率定" debug_shots/snap8_1.txt
chk "重命名按钮（✎）" 'button "✎"' debug_shots/snap8_1.txt
chk "搜索框" "按名称 / 说明筛选项目" debug_shots/snap8_1.txt
chk "导入项目按钮" 'button "导入项目 zip"' debug_shots/snap8_1.txt
chk "卡片导出链接（⤓）" 'link "⤓"' debug_shots/snap8_1.txt
chk "新建卡片" "导入 SHP / DEM 数据" debug_shots/snap8_1.txt
shot p8_01_home

echo
echo "############ [2] 点击卡片进入工作台 ############"
ab eval "(function(){var c=[...document.querySelectorAll('.card')].find(function(x){return x.textContent.indexOf('堵河')>=0 && !x.className.includes('new')}); if(!c) return 'no-card'; c.click(); return 'clicked'})()"
sleep 6
agent-browser snapshot > debug_shots/snap8_2.txt 2>&1
chk "进入地图视图" "地图视图" debug_shots/snap8_2.txt
chk "顶栏工具（DEM）" "DEM 与河网自动生成" debug_shots/snap8_2.txt
chk "侧栏图层面板" "图层" debug_shots/snap8_2.txt

echo
echo "############ [3] 品牌点击返回开始页 ############"
ab eval "(function(){var b=document.querySelector('.brand'); if(!b) return 'no-brand'; b.click(); return 'clicked'})()"
sleep 2
agent-browser snapshot > debug_shots/snap8_3.txt 2>&1
chk "回到开始页（新建按钮）" "进入工作台" debug_shots/snap8_3.txt
shot p8_03_back_home

echo
echo "############ [4] 再次进入（状态保留，秒开） ############"
ab eval "(function(){var c=[...document.querySelectorAll('.card')].find(function(x){return x.textContent.indexOf('堵河')>=0 && !x.className.includes('new')}); if(!c) return 'no-card'; c.click(); return 'clicked'})()"
sleep 4
agent-browser snapshot > debug_shots/snap8_4.txt 2>&1
chk "再次进入工作台" "模型与率定" debug_shots/snap8_4.txt

echo
echo "############ [5] 开始页 → 新建项目弹窗（复用同一弹窗） ############"
# 回开始页
ab eval "(function(){var b=document.querySelector('.brand'); if(b) b.click(); return 'home'})()"
sleep 2
ab eval "(function(){var b=[...document.querySelectorAll('button')].find(function(x){return x.textContent.indexOf('＋ 新建项目')>=0}); if(!b) return 'no-btn'; b.click(); return 'clicked'})()"
sleep 2
agent-browser snapshot > debug_shots/snap8_5.txt 2>&1
chk "弹窗已打开" "新建项目" debug_shots/snap8_5.txt
chk "空白项目选项" "空白项目" debug_shots/snap8_5.txt
chk "示例项目选项" "示例项目" debug_shots/snap8_5.txt
chk "创建按钮" 'button "创建"' debug_shots/snap8_5.txt
shot p8_05_new_modal

# 切到示例项目模式
ab eval "(function(){var m=[...document.querySelectorAll('.mode')].find(function(x){return x.textContent.indexOf('示例项目')>=0}); if(!m) return 'no-mode'; m.click(); return 'switched'})()"
sleep 2
agent-browser snapshot > debug_shots/snap8_5b.txt 2>&1
chk "示例模式：导入说明" "导入后自动完成拓扑构建与概化图生成" debug_shots/snap8_5b.txt
chk "示例模式：按钮文案切换" "创建并导入示例数据" debug_shots/snap8_5b.txt

echo
echo "############ [6] 弹窗内创建空白项目 → 进工作台 → 清理 ############"
ab eval "(function(){var m=[...document.querySelectorAll('.mode')].find(function(x){return x.textContent.indexOf('空白项目')>=0}); m.click(); var i=document.querySelector('.modal input[type=text]'); i.value='_p8_自动测试项目'; i.dispatchEvent(new Event('input',{bubbles:true})); return 'filled'})()"
sleep 1
ab eval "(function(){var b=[...document.querySelectorAll('.modal .foot button')].find(function(x){return x.textContent.trim()==='创建'}); if(!b) return 'no-btn'; b.click(); return 'created'})()"
sleep 8
agent-browser snapshot > debug_shots/snap8_6.txt 2>&1
chk "创建后进入工作台" "地图视图" debug_shots/snap8_6.txt
chk "新项目已选中" "_p8_自动测试项目" debug_shots/snap8_6.txt

echo
echo "############ [6b] 卡片重命名（PATCH 接口 + 列表刷新）→ 顺带清理测试项目 ############"
# 回开始页（品牌点击），对刚创建的测试项目卡片执行改名（不碰真实项目）
ab eval "(function(){var b=document.querySelector('.brand'); if(b) b.click(); return 'home'})()"
sleep 2
ab eval "(function(){var c=[...document.querySelectorAll('.card')].find(function(x){return x.textContent.indexOf('_p8_自动测试项目')>=0 && x.className.indexOf('new')<0}); if(!c) return 'no-card'; var btn=[].slice.call(c.querySelectorAll('.card-del')).find(function(b){return b.title==='重命名'}); if(!btn) return 'no-rename-btn'; btn.click(); return 'edit-mode'})()"
sleep 1
ab eval "(function(){var card=[...document.querySelectorAll('.card')].find(function(x){return x.querySelector('.edit-form')}); if(!card) return 'no-edit'; var inp=card.querySelector('input[type=text]'); inp.value='_p8_自动测试项目_改名'; inp.dispatchEvent(new Event('input',{bubbles:true})); return 'filled'})()"
sleep 1
ab eval "(function(){var card=[...document.querySelectorAll('.card')].find(function(x){return x.querySelector('.edit-form')}); var b=[...card.querySelectorAll('button')].find(function(x){return x.textContent.trim()==='保存'}); if(!b) return 'no-save'; b.click(); return 'saved'})()"
sleep 3
agent-browser snapshot > debug_shots/snap8_5c.txt 2>&1
chk "改名后列表刷新（_改名）" "_改名" debug_shots/snap8_5c.txt

# 清理：按名称查 id 后 API 删除
agent-browser eval "(function(){var x=new XMLHttpRequest(); x.open('GET','/api/projects',false); x.send(); var t=JSON.parse(x.responseText).projects.find(function(p){return p.name.indexOf('_p8_自动测试项目')>=0}); return t ? t.id : 'no-target'})()" > debug_shots/eval8.txt 2>&1
PID=$(tr -d '"\r\n' < debug_shots/eval8.txt | tail -1)
echo "  测试项目 id = $PID"
if [ -n "$PID" ] && [ "${PID:0:2}" = "p_" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "http://127.0.0.1:8013/api/projects/$PID")
  echo "  清理删除 HTTP $code"
  if [ "$code" = "200" ]; then echo "PASS  测试项目已删除（API）"; PASS=$((PASS+1)); else echo "FAIL  测试项目删除（HTTP $code）"; FAIL=$((FAIL+1)); fi
else
  echo "FAIL  未取到新项目 id（$PID）"; FAIL=$((FAIL+1))
fi
ab open "http://127.0.0.1:8013"
ab wait --load load
sleep 5
agent-browser snapshot > debug_shots/snap8_7.txt 2>&1
if grep -q "_p8_自动测试项目" debug_shots/snap8_7.txt; then
  echo "FAIL  测试项目已从列表移除"; FAIL=$((FAIL+1))
else
  echo "PASS  测试项目已从列表移除"; PASS=$((PASS+1))
fi

echo
echo "############ [7] 开始页 → 弹窗选示例项目创建（真实导入）→ 清理 ############"
ab eval "(function(){var b=[...document.querySelectorAll('button')].find(function(x){return x.textContent.indexOf('＋ 新建项目')>=0}); if(!b) return 'no-btn'; b.click(); return 'clicked'})()"
sleep 2
ab eval "(function(){var m=[...document.querySelectorAll('.mode')].find(function(x){return x.textContent.indexOf('示例项目')>=0}); if(!m) return 'no-mode'; m.click(); return 'switched'})()"
sleep 2
agent-browser snapshot > debug_shots/snap8_8.txt 2>&1
chk "弹窗内切到示例项目" "创建并导入示例数据" debug_shots/snap8_8.txt
ab eval "(function(){var b=[...document.querySelectorAll('.modal .foot button')].find(function(x){return x.textContent.indexOf('创建并导入')>=0}); if(!b) return 'no-btn'; b.click(); return 'go'})()"
echo "  已触发示例导入，等待完成…"
sleep 75
agent-browser snapshot > debug_shots/snap8_9.txt 2>&1
chk "示例导入后进入工作台" "地图视图" debug_shots/snap8_9.txt
chk "示例导入后拓扑已构建" "拓扑：" debug_shots/snap8_9.txt
agent-browser eval "(function(){return document.querySelector('.proj-sel') ? document.querySelector('.proj-sel').value : 'no-sel'})()" > debug_shots/eval8b.txt 2>&1
SPID=$(tr -d '"\r\n' < debug_shots/eval8b.txt | tail -1)
echo "  示例项目 id = $SPID"
if [ -n "$SPID" ] && [ "${SPID:0:2}" = "p_" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "http://127.0.0.1:8013/api/projects/$SPID")
  if [ "$code" = "200" ]; then echo "PASS  示例项目已删除（API）"; PASS=$((PASS+1)); else echo "FAIL  示例项目删除（HTTP $code）"; FAIL=$((FAIL+1)); fi
else
  echo "FAIL  未取到示例项目 id（$SPID）"; FAIL=$((FAIL+1))
fi

echo
echo "########################################"
echo "PASS=$PASS FAIL=$FAIL"
agent-browser close > /dev/null 2>&1
