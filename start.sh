#!/bin/bash
# GSD Orchestrator 시작 (백그라운드 데몬)
# 사용법: ./start.sh
# 최초 실행 시: ./setup.sh 먼저 실행
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
INSTANCE_ID=$(echo -n "$(pwd)" | md5 -q | cut -c1-8)
PID_FILE="/tmp/gsd-orchestrator-${INSTANCE_ID}.pid"
LOG_DIR="${PROJECT_DIR}/logs"

# setup.sh 실행 여부 확인
if [ ! -d "$VENV_DIR" ]; then
    echo "오류: 가상환경이 없습니다. ./setup.sh 를 먼저 실행해주세요."
    exit 1
fi
source "${VENV_DIR}/bin/activate"

# .env 확인
if [ ! -f .env ]; then
    echo "오류: .env 파일이 없습니다. ./setup.sh 를 먼저 실행해주세요."
    exit 1
fi

set -a
source .env
set +a

# 이 인스턴스의 기존 프로세스 종료 (PID 파일 + 좀비 정리)
./stop.sh
sleep 1
rm -f "$PID_FILE"

# PYTHONPATH 설정
export PYTHONPATH="${PROJECT_DIR}/src:${PYTHONPATH:-}"

# 워킹 디렉토리 생성
python -c "
import yaml, os
c = yaml.safe_load(open('config.yaml'))
w = c.get('claude', {}).get('working_dir', 'workspace')
w = os.path.expanduser(w)
if not os.path.isabs(w):
    w = os.path.join('${PROJECT_DIR}', w)
os.makedirs(w, exist_ok=True)
print(f'워킹 디렉토리: {w}')
"

# 이 인스턴스의 상태 파일만 정리
rm -f "/tmp/gsd-orchestrator-${INSTANCE_ID}.cooldown" \
      "/tmp/gsd-orchestrator-${INSTANCE_ID}.failcount" \
      "/tmp/gsd-orchestrator-${INSTANCE_ID}.lock"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 백그라운드 데몬으로 시작 (터미널 종료에도 유지)
# 로그는 Python 내부 파일 핸들러가 처리
nohup python -m gsd_orchestrator > /dev/null 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# 절전 방지 — 프로세스 실행 중에는 절전 모드 진입을 막아 polling 중단 방지
if [[ "$(uname)" == "Darwin" ]]; then
    caffeinate -is -w "$PID" &
    echo "GSD Orchestrator v0.5.0 시작 (PID: $PID, instance: ${INSTANCE_ID}, 절전 방지: caffeinate)"
elif command -v systemd-inhibit &>/dev/null; then
    systemd-inhibit --what=idle --who="gsd-orchestrator" --why="메시지 polling 유지" \
        tail --pid="$PID" -f /dev/null &
    echo "GSD Orchestrator v0.5.0 시작 (PID: $PID, instance: ${INSTANCE_ID}, 절전 방지: systemd-inhibit)"
else
    echo "GSD Orchestrator v0.5.0 시작 (PID: $PID, instance: ${INSTANCE_ID})"
    echo "⚠ 절전 방지 도구를 찾을 수 없습니다. 절전 모드 시 메시지 수신이 지연될 수 있습니다."
fi
