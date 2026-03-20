import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typing import Callable, Awaitable

from .config import Config
from .channels.manager import ChannelManager

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# claude, node 등 CLI가 PATH에 있도록 보장
_EXTRA_PATHS = [
    str(Path.home() / ".local" / "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]
for p in _EXTRA_PATHS:
    if p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = p + ":" + os.environ.get("PATH", "")

BLOCKED_FILE = Path("/tmp/gsd-orchestrator.blocked")
TOKEN_TRACK_FILE = Path("/tmp/gsd-orchestrator.token-usage")
RESET_FILE = Path("/tmp/gsd-orchestrator.reset")
TURN_COUNT_FILE = Path("/tmp/gsd-orchestrator.turncount")
FAIL_COUNT_FILE = Path("/tmp/gsd-orchestrator.failcount")
COOLDOWN_FILE = Path("/tmp/gsd-orchestrator.cooldown")
ACTIVE_FILE = Path("/tmp/gsd-orchestrator.active")

MAX_FILE_FAILURES = 3
MAX_GLOBAL_FAILURES = 5
COOLDOWN_ALERT_SECONDS = 5 * 3600
COOLDOWN_ALERT_FILE = Path("/tmp/gsd-orchestrator.cooldown-alerted")


def _build_header(source: dict, keyword: str) -> str:
    """[채널][사용자][요청] 형식의 헤더를 생성한다."""
    ch = source.get("channel_type", "")
    user = source.get("user_name", "")
    return f"[{ch}][{user}][{keyword}]"


ResultCallback = Callable[[dict, str, str, str], Awaitable[None]]


class InboxProcessor:
    def __init__(self, config: Config, channel_manager: ChannelManager | None = None,
                 result_callback: ResultCallback | None = None):
        self._config = config
        self._channel_manager = channel_manager
        self._result_callback = result_callback
        self._working_dir = Path(config.claude_working_dir)
        self._working_dir.mkdir(parents=True, exist_ok=True)
        self._gsd_tools = self._find_gsd_tools()
        self._gsd_commands_dir = self._find_gsd_commands()

    def set_result_callback(self, callback: ResultCallback | None) -> None:
        self._result_callback = callback

    async def _notify_result(self, source: dict, request_text: str,
                             response_text: str, status: str) -> None:
        """등록된 result_callback을 호출한다."""
        if self._result_callback:
            try:
                await self._result_callback(source, request_text, response_text, status)
            except Exception as e:
                logger.error(f"result_callback 에러: {e}")

    def _find_gsd_tools(self) -> str | None:
        for candidate in [
            Path.home() / ".claude" / "get-shit-done" / "bin" / "gsd-tools.cjs",
            Path(self._config.claude_working_dir) / ".claude" / "get-shit-done" / "bin" / "gsd-tools.cjs",
        ]:
            if candidate.exists():
                return str(candidate)
        return None

    def _find_gsd_commands(self) -> str | None:
        for candidate in [
            Path.home() / ".claude" / "commands" / "gsd",
            Path(self._config.claude_working_dir) / ".claude" / "commands" / "gsd",
        ]:
            if candidate.is_dir():
                return str(candidate)
        return None

    def _recover_stale_processing(self) -> None:
        """잔류 .processing 파일을 복원하되, failcount가 초과되면 격리한다."""
        for processing_file in self._config.inbox_dir.glob("*.json.processing"):
            basename = processing_file.name.replace(".processing", "")
            original = self._config.inbox_dir / basename
            fc_path = Path(str(original) + ".failcount")
            fc = self._read_int_file(fc_path) + 1
            self._write_int_file(fc_path, fc)

            if fc >= MAX_FILE_FAILURES:
                # 실패 횟수 초과 → error/로 격리 + 채널 알림
                try:
                    shutil.move(str(processing_file),
                                str(self._config.error_dir / basename))
                    fc_path.unlink(missing_ok=True)
                    logger.warning(
                        f".processing 실패 횟수 초과 ({fc}회), error/로 격리: {basename}")
                    # 요청 내용을 읽어서 알림에 포함
                    try:
                        data = json.loads(
                            (self._config.error_dir / basename).read_text())
                        req = data.get("request", {}).get("text", "")[:30]
                    except Exception:
                        req = basename
                    asyncio.get_running_loop().create_task(
                        self._send_alert(
                            f"[시스템] 요청 처리 실패 ({fc}회 반복). "
                            f"'{req}' 요청을 건너뜁니다."
                        )
                    )
                except OSError as e:
                    logger.error(f".processing 격리 실패: {basename} — {e}")
            else:
                # 복원하여 재처리
                try:
                    processing_file.rename(original)
                    logger.info(
                        f"잔류 .processing 파일 복원 ({fc}/{MAX_FILE_FAILURES}): {basename}")
                except OSError as e:
                    logger.error(f".processing 복원 실패: {processing_file.name} — {e}")

    async def run(self):
        """inbox 디렉토리를 폴링하여 메시지를 처리한다."""
        self._recover_stale_processing()
        while True:
            try:
                await self._process_inbox()
            except Exception as e:
                logger.error(f"inbox_processor 에러: {e}")
            await asyncio.sleep(self._config.inbox_check_interval)

    async def _process_inbox(self):
        # 매 폴링마다 잔류 .processing 파일 체크
        self._recover_stale_processing()

        if self._is_cooldown():
            return

        files = sorted(self._config.inbox_dir.glob("*.json"))
        if not files:
            return

        file = files[0]
        basename = file.name

        if (self._config.outbox_dir / basename).exists():
            file.unlink(missing_ok=True)
            return
        if (self._config.sent_dir / basename).exists():
            file.unlink(missing_ok=True)
            return

        processing_file = file.with_suffix(".json.processing")
        try:
            file.rename(processing_file)
        except OSError:
            return

        # 작업 시작 알림 — 요청 채널에 "작업중입니다" 발송
        try:
            data = json.loads(processing_file.read_text())
            source = data.get("source", {})
            keyword = data.get("keyword", "")
            if source and self._channel_manager:
                header = _build_header(source, keyword)
                pending = len(list(self._config.inbox_dir.glob("*.json")))
                if pending > 0:
                    msg = f"{header} 작업중입니다. (대기 {pending}건)"
                else:
                    msg = f"{header} 작업중입니다."
                await self._channel_manager.broadcast_all(msg)
        except Exception as e:
            logger.warning(f"작업 시작 알림 실패: {e}")

        try:
            await self._process_file(processing_file, basename)
        except Exception as e:
            logger.error(f"처리 실패, inbox로 복원: {basename} — {e}")
            try:
                processing_file.rename(file)
            except OSError:
                pass

    async def _process_file(self, file: Path, basename: str):
        try:
            data = json.loads(file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"inbox 파일 읽기 실패: {basename} — {e}")
            self._handle_failure(file, basename)
            return

        mode = data.get("mode", "default")
        request_text = data.get("request", {}).get("text", "")
        source = data.get("source", {})

        # 하위 호환: source 없으면 chat_id로 생성
        if not source:
            source = {
                "channel_type": "telegram",
                "channel_id": data.get("chat_id", self._config.telegram_chat_id),
                "user_id": data.get("chat_id", ""),
                "user_name": data.get("chat_id", ""),
                "message_id": data.get("message_id", 0),
                "thread_ts": None,
            }

        if mode == "default" and self._config.gsd_auto_classify:
            if self._config.gsd_enabled and self._gsd_commands_dir:
                classified = await self._classify_request(request_text)
                if classified == "gsd":
                    mode = "gsd"

        resumed = not ACTIVE_FILE.exists()
        ACTIVE_FILE.touch()

        if mode in ("gsd", "gsd-resume"):
            await self._process_gsd(file, basename, data, mode, request_text, resumed, source)
        else:
            await self._process_simple(file, basename, data, request_text, resumed, source)

    # ===================================================================
    # Claude 기반 자동 분류
    # ===================================================================
    async def _classify_request(self, text: str) -> str:
        prompt = (
            "다음 사용자 요청을 분류해줘.\n"
            "- 코드 작성/수정/구현/리팩터링/마이그레이션 등 복잡한 개발 작업 → 'gsd'\n"
            "- 여러 파일을 수정해야 하거나, 설계+구현+테스트 등 단계가 많은 작업 → 'gsd'\n"
            "- 작업량이 많아 5분 이상 소요될 것으로 보이는 작업 → 'gsd'\n"
            "- 단순 질문/확인/설명/조회 요청 → 'simple'\n"
            "'gsd' 또는 'simple'로만 답해. 다른 말 하지 마.\n\n"
            f"요청: {text}"
        )
        result = await self._run_claude(prompt, model=self._config.gsd_classify_model, ephemeral=True)
        if result and "gsd" in result.get("result", "").lower():
            return "gsd"
        return "simple"

    # ===================================================================
    # Simple Track
    # ===================================================================
    async def _process_simple(self, file: Path, basename: str, data: dict,
                              request_text: str, resumed: bool, source: dict):
        continue_flag = True
        if RESET_FILE.exists():
            continue_flag = False
            RESET_FILE.unlink(missing_ok=True)
            TURN_COUNT_FILE.unlink(missing_ok=True)

        turn_count = self._read_int_file(TURN_COUNT_FILE)
        if turn_count >= self._config.claude_max_session_turns:
            continue_flag = False
            self._write_int_file(TURN_COUNT_FILE, 0)
            await self._send_alert(
                f"[시스템] 세션이 자동 리셋되었습니다. ({self._config.claude_max_session_turns}건 처리 완료)"
            )

        result = await self._run_claude(
            request_text,
            continue_session=continue_flag,
            progress_label=request_text[:30],
        )

        if result and result.get("subtype") == "success" and result.get("result"):
            self._assemble_outbox(file, basename, result["result"], source, data)
            self._track_tokens(result)
            await self._notify_result(source, request_text, result["result"], "success")

            turn_count = self._read_int_file(TURN_COUNT_FILE)
            self._write_int_file(TURN_COUNT_FILE, turn_count + 1)

            FAIL_COUNT_FILE.unlink(missing_ok=True)
            COOLDOWN_FILE.unlink(missing_ok=True)
            COOLDOWN_ALERT_FILE.unlink(missing_ok=True)

            if resumed:
                await self._send_alert("[시스템] 작업을 다시 시작했습니다.")
        else:
            self._track_tokens(result)
            error_msg = (result or {}).get("result", "처리 실패")
            await self._notify_result(source, request_text, error_msg, "error")
            self._handle_failure(file, basename)

    # ===================================================================
    # GSD Track
    # ===================================================================
    async def _process_gsd(self, file: Path, basename: str, data: dict,
                           mode: str, request_text: str, resumed: bool, source: dict):
        if not self._config.gsd_enabled:
            self._assemble_outbox(file, basename,
                "GSD가 비활성화 상태입니다. config.yaml에서 gsd.enabled를 true로 설정해주세요.",
                source, data)
            return

        if not self._gsd_commands_dir:
            self._assemble_outbox(file, basename,
                "GSD 슬래시 명령어가 설치되어 있지 않습니다.\n"
                "npx get-shit-done-cc --claude --global 으로 설치해주세요.",
                source, data)
            return

        BLOCKED_FILE.unlink(missing_ok=True)

        keyword = data.get("keyword", "")
        header = _build_header(source, keyword)

        if mode == "gsd-resume":
            await self._send_alert(f"{header} GSD 재개")
            prompt = f"{request_text}\n\n위 내용을 반영하여 /gsd:next 로 작업을 계속 진행해주세요. 완료 후 결과를 요약해서 텍스트로만 출력해주세요."
            result = await self._run_claude(
                prompt, continue_session=True,
                progress_label=f"GSD 재개: {request_text[:20]}",
            )
        else:
            await self._send_alert(f"{header} GSD 시작")
            gsd_prompt = (
                f"/gsd:do {request_text}\n\n"
                "## 실행 룰 (반드시 준수)\n"
                "1. 먼저 작업을 분석하여 분류 단위(모듈/기능/컴포넌트)로 분할한 계획을 출력하세요.\n"
                "   - 각 분류 단위별: 작업 범위, 변경 대상 파일, 예상 소요\n"
                "   - 전체 진행 순서와 의존 관계\n"
                "   - 이 계획을 사용자에게 보고하고 확인을 기다리세요.\n"
                "2. 확인을 받으면 분류 단위별로 다음 순서를 반복하세요:\n"
                "   분석 → 설계 → 구현 → 단위테스트\n"
                "3. 한 분류 단위가 완료되면 결과를 보고하고, 다음 단위로 진행하세요.\n"
                "4. 중간에 중단되더라도 해당 분류 단위부터 재시작할 수 있도록 "
                "각 단위의 완료 상태를 명확히 기록하세요.\n"
                "5. 전체 완료 후 최종 요약을 출력하세요."
            )
            result = await self._run_claude(
                gsd_prompt,
                progress_label=f"GSD: {request_text[:20]}",
            )

        self._track_tokens(result)

        blocker_text = self._check_gsd_blockers()
        if blocker_text:
            BLOCKED_FILE.write_text(basename)
            blocked_msg = (
                f"GSD 블로킹: {blocker_text}\n\n"
                "답변을 보내주세요. 다음 메시지가 GSD 재개 응답으로 전달됩니다."
            )
            self._assemble_outbox(file, basename, blocked_msg, source, data)
            await self._notify_result(source, request_text, blocker_text, "blocked")
        elif result and result.get("subtype") == "success" and result.get("result"):
            self._assemble_outbox(file, basename, result["result"], source, data)
            await self._notify_result(source, request_text, result["result"], "success")
            FAIL_COUNT_FILE.unlink(missing_ok=True)
            COOLDOWN_FILE.unlink(missing_ok=True)
            COOLDOWN_ALERT_FILE.unlink(missing_ok=True)
            if resumed:
                await self._send_alert("[시스템] 작업을 다시 시작했습니다.")
        else:
            error_msg = (result or {}).get("result", "GSD 처리 실패")
            await self._notify_result(source, request_text, error_msg, "error")
            self._handle_failure(file, basename)

    # ===================================================================
    # Claude 실행
    # ===================================================================
    async def _run_claude(self, prompt: str, continue_session: bool = False,
                          model: str | None = None,
                          ephemeral: bool = False,
                          progress_label: str | None = None) -> dict | None:
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--dangerously-skip-permissions"]
        if continue_session:
            cmd.append("--continue")
        if model:
            cmd.extend(["--model", model])
        if ephemeral:
            cmd.append("--no-session-persistence")

        progress_task = None
        if progress_label and self._config.progress_interval > 0:
            progress_task = asyncio.create_task(
                self._progress_reporter(progress_label)
            )

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._working_dir),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._config.claude_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("claude -p timeout — 프로세스 종료 중")
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
                return None

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if stderr:
                logger.warning(f"claude stderr: {stderr[:500]}")

            if stdout:
                return json.loads(stdout)
        except json.JSONDecodeError as e:
            logger.warning(f"claude 응답 JSON 파싱 실패: {e}")
        except Exception as e:
            logger.error(f"claude 실행 에러: {e}")
        finally:
            if progress_task:
                progress_task.cancel()
                self._cleanup_progress_alerts()
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
        return None

    async def _progress_reporter(self, label: str):
        interval = self._config.progress_interval
        elapsed = 0
        try:
            while True:
                await asyncio.sleep(interval)
                elapsed += interval
                minutes = elapsed // 60
                seconds = elapsed % 60
                if minutes > 0:
                    time_str = f"{minutes}분 {seconds}초"
                else:
                    time_str = f"{seconds}초"
                await self._send_alert(f"[시스템] {label} 처리 중... ({time_str} 경과)")
        except asyncio.CancelledError:
            pass

    # ===================================================================
    # outbox 조립 (응답 헤더 + broadcast targets)
    # ===================================================================
    def _assemble_outbox(self, processing_file: Path, basename: str,
                         response_text: str, source: dict, data: dict):
        try:
            file_data = json.loads(processing_file.read_text())

            # source 보장
            file_data["source"] = source

            # [채널][사용자][요청] 처리결과 헤더 적용
            keyword = file_data.get("keyword", data.get("keyword", ""))
            header = _build_header(source, keyword)
            headed_text = f"{header} 처리결과\n\n{response_text}"

            # broadcast targets 생성
            if self._channel_manager:
                file_data["targets"] = self._channel_manager.build_broadcast_targets(source)
            else:
                # fallback: 단일 타겟
                file_data["targets"] = [{
                    "channel_type": source.get("channel_type", "telegram"),
                    "channel_id": source.get("channel_id", file_data.get("chat_id", "")),
                    "is_origin": True,
                }]

            file_data["retry_count"] = 0
            file_data["response"] = {
                "text": headed_text,
                "parse_mode": "HTML",
                "timestamp": datetime.now(KST).isoformat(),
            }

            tmp = self._config.outbox_dir / f".{basename}.tmp"
            tmp.write_text(json.dumps(file_data, ensure_ascii=False, indent=2))
            final = self._config.outbox_dir / basename
            tmp.rename(final)
            processing_file.unlink(missing_ok=True)

            original = processing_file.parent / basename
            fc = Path(str(original) + ".failcount")
            fc.unlink(missing_ok=True)

            logger.info(f"outbox 조립 완료: {basename}")
        except Exception as e:
            logger.error(f"outbox 조립 실패: {basename} — {e}")

    def _cleanup_progress_alerts(self) -> None:
        """outbox에 남아있는 미발송 progress 알림 파일을 삭제한다."""
        for f in self._config.outbox_dir.glob("*_system-alert.json"):
            try:
                data = json.loads(f.read_text())
                resp_text = data.get("response", {}).get("text", "")
                if "처리 중..." in resp_text and "경과)" in resp_text:
                    f.unlink()
                    logger.info(f"progress 알림 정리: {f.name}")
            except Exception:
                pass
        # .sending 상태인 것도 정리
        for f in self._config.outbox_dir.glob("*_system-alert.json.sending"):
            try:
                data = json.loads(f.read_text())
                resp_text = data.get("response", {}).get("text", "")
                if "처리 중..." in resp_text and "경과)" in resp_text:
                    f.unlink()
                    logger.info(f"progress 알림 정리: {f.name}")
            except Exception:
                pass

    # ===================================================================
    # 시스템 알림 (전채널 브로드캐스트)
    # ===================================================================
    async def _send_alert(self, text: str):
        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_system-alert.json"

        if self._channel_manager:
            targets = [
                {"channel_type": ct, "channel_id": ci, "is_origin": True}
                for ct, ci in self._channel_manager.get_all_channels()
            ]
        else:
            targets = [{
                "channel_type": "telegram",
                "channel_id": self._config.telegram_chat_id,
                "is_origin": True,
            }]

        data = {
            "id": str(uuid.uuid4()),
            "source": {},
            "targets": targets,
            "retry_count": 0,
            "keyword": "system-alert",
            "request": None,
            "response": {
                "text": text,
                "parse_mode": "HTML",
                "timestamp": now.isoformat(),
            },
        }
        tmp = self._config.outbox_dir / f".{filename}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.rename(self._config.outbox_dir / filename)

    # ===================================================================
    # 실패 처리
    # ===================================================================
    def _handle_failure(self, processing_file: Path, basename: str):
        original = processing_file.parent / basename
        try:
            processing_file.rename(original)
        except OSError:
            original = processing_file

        fc_path = Path(str(original) + ".failcount")
        fc = self._read_int_file(fc_path) + 1

        if fc >= MAX_FILE_FAILURES:
            try:
                shutil.move(str(original), str(self._config.error_dir / basename))
            except OSError:
                pass
            fc_path.unlink(missing_ok=True)
            asyncio.get_running_loop().create_task(
                self._send_alert(
                    f"[시스템] 메시지 처리 실패 ({MAX_FILE_FAILURES}회 연속). "
                    f"error/로 격리: {basename}"
                )
            )
        else:
            self._write_int_file(fc_path, fc)

        gfc = self._read_int_file(FAIL_COUNT_FILE) + 1
        self._write_int_file(FAIL_COUNT_FILE, gfc)

        if gfc >= MAX_GLOBAL_FAILURES:
            retry_min = self._config.claude_cooldown_retry_minutes
            resume_at = int(time.time()) + retry_min * 60
            COOLDOWN_FILE.write_text(str(resume_at))
            ACTIVE_FILE.unlink(missing_ok=True)
            FAIL_COUNT_FILE.unlink(missing_ok=True)
            asyncio.get_running_loop().create_task(
                self._send_alert(
                    f"[시스템] 연속 실패 감지. {retry_min}분 후 자동 재시도. "
                    "즉시 재개: /resume"
                )
            )

    # ===================================================================
    # 토큰 추적
    # ===================================================================
    def _track_tokens(self, result: dict | None):
        if not result:
            return
        try:
            usage = result.get("usage", {})
            model_usage = result.get("modelUsage", {})

            existing = {}
            if TOKEN_TRACK_FILE.exists():
                try:
                    existing = json.loads(TOKEN_TRACK_FILE.read_text())
                except (json.JSONDecodeError, OSError):
                    existing = {}

            existing["input_tokens"] = existing.get("input_tokens", 0) + usage.get("input_tokens", 0)
            existing["output_tokens"] = existing.get("output_tokens", 0) + usage.get("output_tokens", 0)
            existing["cache_read"] = existing.get("cache_read", 0) + usage.get("cache_read_input_tokens", 0)
            existing["cache_creation"] = existing.get("cache_creation", 0) + usage.get("cache_creation_input_tokens", 0)
            existing["total_cost_usd"] = round(
                existing.get("total_cost_usd", 0) + result.get("total_cost_usd", 0), 6
            )
            existing["call_count"] = existing.get("call_count", 0) + 1
            existing["last_updated"] = datetime.now(KST).isoformat()
            existing["last_call"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cost_usd": round(result.get("total_cost_usd", 0), 6),
                "duration_ms": result.get("duration_ms", 0),
                "model": list(model_usage.keys())[0] if model_usage else "",
                "stop_reason": result.get("stop_reason", ""),
            }

            TOKEN_TRACK_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"토큰 추적 실패: {e}")

    # ===================================================================
    # 쿨다운
    # ===================================================================
    def _is_cooldown(self) -> bool:
        if not COOLDOWN_FILE.exists():
            COOLDOWN_ALERT_FILE.unlink(missing_ok=True)
            return False
        try:
            resume_at = int(COOLDOWN_FILE.read_text().strip())
            now = int(time.time())
            if now < resume_at:
                cooldown_start = resume_at - self._config.claude_cooldown_retry_minutes * 60
                elapsed = now - cooldown_start
                if elapsed >= COOLDOWN_ALERT_SECONDS and not COOLDOWN_ALERT_FILE.exists():
                    COOLDOWN_ALERT_FILE.touch()
                    asyncio.get_running_loop().create_task(
                        self._send_alert(
                            "[시스템] 쿨다운 5시간 이상 지속. 토큰 상태 확인 필요. /resume 으로 재시도 가능."
                        )
                    )
                return True
            COOLDOWN_FILE.unlink(missing_ok=True)
            COOLDOWN_ALERT_FILE.unlink(missing_ok=True)
            return False
        except (ValueError, OSError):
            COOLDOWN_FILE.unlink(missing_ok=True)
            return False

    # ===================================================================
    # GSD 블로커 감지
    # ===================================================================
    def _check_gsd_blockers(self) -> str:
        if not self._gsd_tools:
            return ""
        try:
            proc = subprocess.run(
                ["node", self._gsd_tools, "state", "load", "--raw",
                 "--cwd", str(self._working_dir)],
                capture_output=True, text=True, timeout=10,
            )
            if proc.stdout:
                state = json.loads(proc.stdout)
                blockers = state.get("blockers", []) or state.get("open_questions", []) or []
                if blockers:
                    b = blockers[0]
                    if isinstance(b, str):
                        return b
                    if isinstance(b, dict):
                        return b.get("description", "") or b.get("question", "") or str(b)
                    return str(b)
        except Exception:
            pass
        return ""

    # ===================================================================
    # 유틸리티
    # ===================================================================
    @staticmethod
    def _read_int_file(path: Path) -> int:
        try:
            return int(path.read_text().strip())
        except (ValueError, OSError):
            return 0

    @staticmethod
    def _write_int_file(path: Path, value: int):
        path.write_text(str(value))
