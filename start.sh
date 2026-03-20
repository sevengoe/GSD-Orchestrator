#!/bin/bash
# GSD Orchestrator 시작
# 사용법: ./start.sh
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PID_FILE="/tmp/gsd-orchestrator.pid"

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

# .env 확인
if [ ! -f .env ]; then
    echo "오류: .env 파일이 없습니다."
    echo "  cp .env.example .env"
    echo "  .env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 설정해주세요."
    exit 1
fi

set -a
source .env
set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    echo "오류: .env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 설정해주세요."
    exit 1
fi

# venv 자동 생성
if [ ! -d "$VENV_DIR" ]; then
    echo "Python 가상환경 생성 중..."
    python3 -m venv "$VENV_DIR"
fi
source "${VENV_DIR}/bin/activate"

# 패키지 설치
if ! python -c "import gsd_orchestrator" 2>/dev/null; then
    echo "패키지 설치 중..."
    pip install -e . 2>&1 | tail -3
fi

# 디렉토리 자동 생성
mkdir -p messages/{inbox,outbox,sent,error,archive} logs

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
echo "GSD Orchestrator v1.0.0 시작 (PID: $$)"
echo $$ > "$PID_FILE"
trap "rm -f $PID_FILE" EXIT

exec python -m gsd_orchestrator
