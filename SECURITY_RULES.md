# 보안 룰 (필수 준수)

이 룰은 모든 작업에서 반드시 지켜야 합니다. 예외 없음.

## 금지 행위
1. `rm -rf /`, `rm -rf ~`, `rm -rf *` 등 루트/홈/전체 삭제 명령 실행 금지
2. 시스템 설정 파일 수정 금지: `/etc/`, `/System/`, `~/.ssh/`, `~/.gnupg/`
3. 데이터베이스 스키마 변경 쿼리 금지: `DROP`, `TRUNCATE`, `ALTER`, `DELETE` (WHERE 없는)
4. 시스템 서비스 조작 금지: `systemctl`, `launchctl`, `service` 명령
5. 사용자 계정/권한 변경 금지: `useradd`, `usermod`, `chmod 777`, `chown root`
6. `.env`, 인증서, 키 파일 내용 출력 또는 외부 전송 금지

## 파일 삭제 규칙
- 파일 삭제가 필요할 때는 `rm` 대신 `.trash/` 디렉토리로 `mv` 이동
- 단, `__pycache__/`, `.pyc`, `.tmp` 등 임시 파일은 직접 삭제 허용

## 작업 범위
- 작업 디렉토리(workspace/) 내에서만 파일 생성/수정
- 외부 파일은 읽기만 허용

## 응답 포맷
- 에러 로그나 스택트레이스가 포함된 요청을 받으면, 전체 traceback을 그대로 반복하지 마세요
- 핵심 에러 타입과 메시지(1~2줄)만 인용하고, 원인 분석과 해결 방안을 간결하게 요약하세요
- 예시: `FileNotFoundError: config.yaml` → 원인: 설정 파일 누락 → 해결: `cp config.yaml.example config.yaml`
- 파일 생성/수정/삭제 등 다수의 작업을 수행한 경우, 응답 최상단에 **실행 요약(Execute Summary)**을 작성하세요
  - 형식: 변경한 파일 목록, 주요 작업 내용, 결과를 간결한 목록으로 정리
  - 예시:
    ```
    ## 실행 요약
    - 수정: src/config.py (MCP 설정 추가)
    - 생성: src/mcp_server/server.py (MCP 서버 구현)
    - 삭제: src/old_handler.py (미사용 파일 제거)
    - 테스트: 12개 통과, 0개 실패
    ```
