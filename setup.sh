#!/bin/bash
# GSD Orchestrator 초기 설정
# 사용법: ./setup.sh [--slack]
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

echo "=== GSD Orchestrator 초기 설정 ==="

# Python 탐색 (3.13 → 3.12 → 3.11 → python3 순으로 fallback)
PYTHON_CMD=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "오류: Python 3.11 이상이 필요합니다."
    echo "  macOS: brew install python@3.13"
    echo "  Ubuntu: sudo apt install python3.13"
    exit 1
fi
echo "✓ Python $($PYTHON_CMD --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"

# Claude Code 확인
if command -v claude &>/dev/null; then
    echo "✓ Claude Code CLI"
else
    echo "⚠ Claude Code CLI가 설치되어 있지 않습니다."
    echo "  brew install claude-code 또는 npm install -g @anthropic-ai/claude-code"
fi

# venv 생성 (ensurepip 실패 시 --without-pip fallback)
if [ ! -d "$VENV_DIR" ]; then
    echo "Python 가상환경 생성 중..."
    if ! $PYTHON_CMD -m venv "$VENV_DIR" 2>/dev/null; then
        echo "  ensurepip 실패, --without-pip 모드로 재시도..."
        $PYTHON_CMD -m venv "$VENV_DIR" --without-pip
        curl -sS https://bootstrap.pypa.io/get-pip.py | "${VENV_DIR}/bin/python3"
    fi
fi
source "${VENV_DIR}/bin/activate"
echo "✓ 가상환경: ${VENV_DIR}"

# 기본 의존성 설치 (setuptools 선행 설치로 빌드 실패 방지)
echo "의존성 설치 중..."
pip install setuptools -q 2>&1 | tail -1
pip install -e . -q 2>&1 | tail -3

# Slack 의존성 설치 (config.yaml에서 slack.enabled 확인 또는 --slack 옵션)
SLACK_ENABLED=$(python3 -c "
import yaml, sys
try:
    cfg = yaml.safe_load(open('config.yaml'))
    print('true' if cfg.get('channels',{}).get('slack',{}).get('enabled') else 'false')
except: print('false')
" 2>/dev/null || echo "false")

if [ "${1:-}" = "--slack" ] || [ "$SLACK_ENABLED" = "true" ]; then
    echo "Slack 의존성 설치 중..."
    pip install slack-bolt aiohttp -q 2>&1 | tail -3
    echo "✓ Slack (slack-bolt, aiohttp)"
fi

# SSL 프록시 환경 감지 및 자동 조치
if ! python3 -c "import urllib.request; urllib.request.urlopen('https://pypi.org')" 2>/dev/null; then
    echo "⚠ SSL 인증서 검증 실패 감지 (프록시/VPN 환경)"
    echo "  pip-system-certs 설치 중..."
    pip install pip-system-certs -q 2>&1 | tail -1
    echo "✓ pip-system-certs (시스템 CA 인증서 사용)"
fi

# .env 생성
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✓ .env 생성됨 — 토큰을 설정해주세요"
else
    echo "✓ .env 존재"
fi

# 디렉토리 생성
mkdir -p messages/{inbox,outbox,sent,error,archive,workqueue,plan} logs
echo "✓ 디렉토리 생성"

echo ""
echo "=== 설정 완료 ==="
echo "1. .env 파일에 봇 토큰을 설정하세요"
echo "2. ./start.sh 로 실행하세요"
