#!/bin/bash
# GSD Orchestrator 로그 실시간 확인
cd "$(dirname "$0")"
tail -f logs/gsd-orchestrator.log
