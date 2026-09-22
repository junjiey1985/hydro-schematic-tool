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
chk "品牌标题" "水系概化图工具" debug_shots/snap8_1.txt
chk "新建项目按钮" 'button "＋ 新建项目"' debug_shots/snap8_1.txt
chk "示例流域按钮" "导入示例流域" debug_shots/snap8_1.txt
chk "项目卡片（堵河）" "堵河" debug_shots/snap8_1.txt
chk "拓扑标记" "拓扑 ✓" debug_shots/snap8_1.txt
chk "图层计数" "个图层" debug_shots/snap8_1.txt
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
chk "回到开始页（新建按钮）" "导入示例流域" debug_shots/snap8_3.txt
shot p8_03_back_home

echo
echo "############ [4] 再次进入（状态保留，秒开） ############"
ab eval "(function(){var c=[...document.querySelectorAll('.card')].find(function(x){return x.textContent.indexOf('堵河')>=0 && !x.className.includes('new')}); if(!c) return 'no-card'; c.click(); return 'clicked'})()"
sleep 4
agent-browser snapshot > debug_shots/snap8_4.txt 2>&1
chk "再次进入工作台" "模型与率定" debug_shots/snap8_4.txt

echo
echo "########################################"
echo "PASS=$PASS FAIL=$FAIL"
agent-browser close > /dev/null 2>&1
