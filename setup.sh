#!/bin/bash
# GSD Orchestrator 초기 설정
# 사용법: ./setup.sh [--slack]
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

echo "=== GSD Orchestrator 초기 설정 ==="

# Python 버전 확인
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    echo "오류: Python 3.11 이상이 필요합니다. (현재: $(python3 --version))"
    exit 1
fi
echo "✓ Python $(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"

# Claude Code 확인
if command -v claude &>/dev/null; then
    echo "✓ Claude Code CLI"
else
    echo "⚠ Claude Code CLI가 설치되어 있지 않습니다."
    echo "  brew install claude-code 또는 npm install -g @anthropic-ai/claude-code"
fi

# venv 생성
if [ ! -d "$VENV_DIR" ]; then
    echo "Python 가상환경 생성 중..."
    python3 -m venv "$VENV_DIR"
fi
source "${VENV_DIR}/bin/activate"
echo "✓ 가상환경: ${VENV_DIR}"

# 기본 의존성 설치
echo "의존성 설치 중..."
pip install -e . -q 2>&1 | tail -3

# Slack 옵션
if [ "${1:-}" = "--slack" ]; then
    echo "Slack 의존성 설치 중..."
    pip install slack-bolt aiohttp -q 2>&1 | tail -3
    echo "✓ Slack (slack-bolt, aiohttp)"
fi

# .env 생성
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✓ .env 생성됨 — 토큰을 설정해주세요"
else
    echo "✓ .env 존재"
fi

# 디렉토리 생성
mkdir -p messages/{inbox,outbox,sent,error,archive} logs
echo "✓ 디렉토리 생성"

echo ""
echo "=== 설정 완료 ==="
echo "1. .env 파일에 봇 토큰을 설정하세요"
echo "2. ./start.sh 로 실행하세요"
