#!/bin/bash
# GSD Orchestrator 중지
# 이 인스턴스의 프로세스 트리만 종료한다 (다른 인스턴스에 영향 없음).

cd "$(dirname "$0")"
INSTANCE_ID=$(echo -n "$(pwd)" | md5 -q | cut -c1-8)
PID_FILE="/tmp/gsd-orchestrator-${INSTANCE_ID}.pid"

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

# 종료 전 .processing 파일 복원 (재시작 시 실패 카운트 방지)
restore_processing_files() {
    local dir="$1"
    if [ -d "$dir" ]; then
        for f in "$dir"/*.json.processing; do
            [ -f "$f" ] || continue
            local original="${f%.processing}"
            mv "$f" "$original" 2>/dev/null && \
                echo "  복원: $(basename "$original")"
        done
    fi
}

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "GSD Orchestrator 프로세스 트리 종료 (PID: $PID, instance: $INSTANCE_ID)"
        restore_processing_files "messages/inbox"
        restore_processing_files "messages/workqueue"
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
    echo "실행 중인 GSD Orchestrator가 없습니다. (instance: $INSTANCE_ID)"
fi
