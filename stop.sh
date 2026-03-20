#!/bin/bash
# GSD Orchestrator 중지
PID_FILE="/tmp/gsd-orchestrator.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "GSD Orchestrator 중지 (PID: $PID)"
        kill "$PID"
        sleep 2
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
        rm -f "$PID_FILE"
        echo "중지 완료"
    else
        echo "프로세스가 이미 종료되었습니다."
        rm -f "$PID_FILE"
    fi
else
    # PID 파일 없으면 프로세스 검색
    PIDS=$(pgrep -f "python.*gsd_orchestrator" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "GSD Orchestrator 프로세스 종료: $PIDS"
        pkill -f "python.*gsd_orchestrator" 2>/dev/null
        sleep 2
        pkill -9 -f "python.*gsd_orchestrator" 2>/dev/null || true
        echo "중지 완료"
    else
        echo "실행 중인 GSD Orchestrator가 없습니다."
    fi
fi
