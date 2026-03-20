#!/bin/bash
# GSD Orchestrator 중지
# gsd_orchestrator 프로세스 + 자식 프로세스(claude 등)를 모두 종료한다.
# 여러 인스턴스가 실행 중일 수 있으므로 PID 파일 기반으로 해당 프로세스 트리만 종료.

PID_FILE="/tmp/gsd-orchestrator.pid"

kill_tree() {
    local pid=$1
    # 자식 프로세스 먼저 종료 (재귀)
    local children
    children=$(pgrep -P "$pid" 2>/dev/null || true)
    for child in $children; do
        kill_tree "$child"
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
    fi
}

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "GSD Orchestrator 프로세스 트리 종료 (PID: $PID)"
        kill_tree "$PID"
        sleep 2
        # 아직 살아있으면 강제 종료
        if kill -0 "$PID" 2>/dev/null; then
            echo "강제 종료 중..."
            kill -9 "$PID" 2>/dev/null
            pkill -9 -P "$PID" 2>/dev/null || true
        fi
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
        for pid in $PIDS; do
            kill_tree "$pid"
        done
        sleep 2
        pkill -9 -f "python.*gsd_orchestrator" 2>/dev/null || true
        echo "중지 완료"
    else
        echo "실행 중인 GSD Orchestrator가 없습니다."
    fi
fi
