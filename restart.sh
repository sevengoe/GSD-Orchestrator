#!/bin/bash
# GSD Orchestrator 재시작
cd "$(dirname "$0")"
./stop.sh
sleep 1
./start.sh
