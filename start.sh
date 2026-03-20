#!/bin/bash
# GSD Orchestrator 시작
# 사용법: ./start.sh
# 최초 실행 시: ./setup.sh 먼저 실행
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PID_FILE="/tmp/gsd-orchestrator.pid"

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

# 기존 프로세스 종료
EXISTING=$(pgrep -f "python.*gsd_orchestrator" 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
    echo "기존 프로세스 종료 중..."
    pkill -f "python.*gsd_orchestrator" 2>/dev/null || true
    sleep 2
    pkill -9 -f "python.*gsd_orchestrator" 2>/dev/null || true
    sleep 1
fi
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

# 상태 파일 정리
rm -f /tmp/gsd-orchestrator.cooldown /tmp/gsd-orchestrator.failcount /tmp/gsd-orchestrator.lock

# 시작
echo "GSD Orchestrator v0.5.0 시작 (PID: $$)"
echo $$ > "$PID_FILE"
trap "rm -f $PID_FILE" EXIT

exec python -m gsd_orchestrator
