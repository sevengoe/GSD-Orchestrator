import asyncio
import json
import logging
import os
import re
import shutil
import signal
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

MAX_FILE_FAILURES = 3
MAX_GLOBAL_FAILURES = 5
COOLDOWN_ALERT_SECONDS = 5 * 3600


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

        # 인스턴스별 런타임 파일 경로
        self._blocked_file = config.runtime_path("blocked")
        self._token_track_file = config.runtime_path("token-usage")
        self._reset_file = config.runtime_path("reset")
        self._turn_count_file = config.runtime_path("turncount")
        self._fail_count_file = config.runtime_path("failcount")
        self._cooldown_file = config.runtime_path("cooldown")
        self._active_file = config.runtime_path("active")
        self._cooldown_alert_file = config.runtime_path("cooldown-alerted")
        self._gsd_active_file = config.runtime_path("gsd-active")

        # 보안 룰 (프롬프트 주입용)
        self._security_rules = self._load_security_rules()

        # 작업 큐
        self._workqueue_dir = config.workqueue_dir
        self._workqueue_dir.mkdir(parents=True, exist_ok=True)
        self._plan_dir = config.plan_dir
        self._plan_dir.mkdir(parents=True, exist_ok=True)
        self._plan_file = self._plan_dir / ".gsd-plan.md"

    def set_result_callback(self, callback: ResultCallback | None) -> None:
        self._result_callback = callback

    def _load_security_rules(self) -> str:
        """working_dir 또는 프로젝트 루트의 SECURITY_RULES.md를 로드한다."""
        for candidate in [
            self._working_dir / "SECURITY_RULES.md",
            Path(self._config.claude_working_dir).parent / "SECURITY_RULES.md",
        ]:
            if candidate.exists():
                try:
                    return candidate.read_text().strip()
                except OSError:
                    pass
        return ""

    def _apply_security_rules(self, prompt: str) -> str:
        """보안 룰이 있으면 프롬프트 최상단에 주입한다."""
        if self._security_rules:
            return f"{self._security_rules}\n\n---\n\n{prompt}"
        return prompt

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
            # inbox가 비어있으면 작업 큐 처리
            await self._process_workqueue()
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
                delay_notice = source.get("delay_notice", "")
                pending = len(list(self._config.inbox_dir.glob("*.json")))
                if pending > 0:
                    msg = f"{header} 작업중입니다. (대기 {pending}건)"
                else:
                    msg = f"{header} 작업중입니다."
                if delay_notice:
                    msg += delay_notice
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
        extracted_text = data.get("request", {}).get("extracted_text", "")
        source = data.get("source", {})

        # 첨부파일 텍스트가 있으면 프롬프트에 삽입
        if extracted_text:
            attachments = data.get("request", {}).get("attachments", [])
            file_label = attachments[0].get("filename", "첨부파일") if attachments else "첨부파일"
            request_text = f"{request_text}\n\n[첨부파일: {file_label}]\n{extracted_text}"

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

        # 자기 자신의 재시작/중지 명령 감지 → Claude 우회, 직접 처리
        self_cmd = self._detect_self_command(request_text)
        if self_cmd:
            await self._handle_self_command(file, basename, self_cmd, source, data)
            return

        if mode == "default" and self._config.gsd_auto_classify:
            if self._config.gsd_enabled and self._gsd_commands_dir:
                classified = await self._classify_request(request_text)
                if classified == "gsd":
                    mode = "gsd"

        self._active_file.touch()

        if mode in ("gsd", "gsd-resume"):
            await self._process_gsd(file, basename, data, mode, request_text, source)
        else:
            await self._process_simple(file, basename, data, request_text, source)

    # ===================================================================
    # 자기 프로세스 관리 명령 (재시작/중지) — Claude 우회
    # ===================================================================
    _SELF_CMD_PATTERNS = [
        (re.compile(r"(gsd.?orchestrator|오케스트레이터|봇).{0,10}(재시작|restart|리스타트)", re.I), "restart"),
        (re.compile(r"(재시작|restart|리스타트).{0,10}(gsd.?orchestrator|오케스트레이터|봇)", re.I), "restart"),
        (re.compile(r"(gsd.?orchestrator|오케스트레이터|봇).{0,10}(중지|stop|종료|꺼)", re.I), "stop"),
        (re.compile(r"(중지|stop|종료|꺼).{0,10}(gsd.?orchestrator|오케스트레이터|봇)", re.I), "stop"),
    ]

    def _detect_self_command(self, text: str) -> str | None:
        """재시작/중지 등 자기 프로세스 관리 요청이면 명령어를 반환한다."""
        for pattern, cmd in self._SELF_CMD_PATTERNS:
            if pattern.search(text):
                return cmd
        return None

    async def _handle_self_command(self, file: Path, basename: str,
                                   cmd: str, source: dict, data: dict):
        """자기 관리 명령을 안전하게 처리: 응답 저장 → 프로세스 종료/재시작."""
        project_dir = Path(self._config.claude_working_dir).parent

        if cmd == "restart":
            script = project_dir / "restart.sh"
            response = "GSD-Orchestrator를 재시작합니다."
        else:  # stop
            script = project_dir / "stop.sh"
            response = "GSD-Orchestrator를 중지합니다."

        if not script.exists():
            self._assemble_outbox(file, basename,
                                  f"{script.name}을 찾을 수 없습니다.", source, data)
            return

        # 1) 응답을 outbox에 저장 (처리 완료 상태로 전환)
        self._assemble_outbox(file, basename, response, source, data)
        logger.info(f"자기 관리 명령 '{cmd}' — outbox 저장 완료, 실행 대기")

        # 2) outbox_sender가 발송할 시간을 확보한 뒤 실행
        await asyncio.sleep(2)

        # 3) 스크립트 실행 (재시작 시 자신이 죽으므로 subprocess.Popen으로 비동기 실행)
        logger.info(f"자기 관리 명령 실행: {script}")
        subprocess.Popen(
            ["bash", str(script)],
            cwd=str(project_dir),
            start_new_session=True,
        )

        # stop/restart 스크립트가 이 프로세스를 종료하므로 여기서 대기
        if cmd == "restart":
            await asyncio.sleep(30)
        else:
            # stop의 경우 스크립트가 종료시켜줌, 안전장치로 직접 종료
            await asyncio.sleep(5)
            os.kill(os.getpid(), signal.SIGTERM)

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
        result = await self._run_claude(prompt, model=self._config.gsd_classify_model, ephemeral=True, bare=True)
        if result and "gsd" in result.get("result", "").lower():
            return "gsd"
        return "simple"

    # ===================================================================
    # Simple Track
    # ===================================================================
    async def _process_simple(self, file: Path, basename: str, data: dict,
                              request_text: str, source: dict):
        continue_flag = True
        if self._reset_file.exists():
            continue_flag = False
            self._reset_file.unlink(missing_ok=True)
            self._turn_count_file.unlink(missing_ok=True)

        turn_count = self._read_int_file(self._turn_count_file)
        if turn_count >= self._config.claude_max_session_turns:
            continue_flag = False
            self._write_int_file(self._turn_count_file, 0)
            await self._send_alert(
                f"[시스템] 세션이 자동 리셋되었습니다. ({self._config.claude_max_session_turns}건 처리 완료)"
            )

        # 세션 리셋 또는 첫 메시지 시 sent/ 이력으로 맥락 복원
        prompt = request_text
        needs_context = not continue_flag or turn_count == 0
        if needs_context:
            channel_id = source.get("channel_id", "")
            conversation_id = data.get("conversation_id", "")
            context = self._build_context_from_history(channel_id, conversation_id)
            if context:
                label = "세션 리셋" if not continue_flag else "첫 메시지"
                logger.info(f"Simple Track {label} — sent/ 이력으로 맥락 복원")
                prompt = (
                    f"이전 대화 맥락을 참고하여 답변하세요.\n\n"
                    f"{context}\n\n"
                    f"사용자의 새 메시지: {request_text}"
                )

        result = await self._run_claude(
            prompt,
            continue_session=continue_flag,
            progress_label=request_text[:30],
        )

        if result and result.get("subtype") == "success" and result.get("result"):
            self._assemble_outbox(file, basename, result["result"], source, data)
            self._track_tokens(result)
            await self._notify_result(source, request_text, result["result"], "success")

            turn_count = self._read_int_file(self._turn_count_file)
            self._write_int_file(self._turn_count_file, turn_count + 1)

            self._fail_count_file.unlink(missing_ok=True)
            self._cooldown_file.unlink(missing_ok=True)
            self._cooldown_alert_file.unlink(missing_ok=True)

        else:
            self._track_tokens(result)
            error_msg = (result or {}).get("result", "처리 실패")
            await self._notify_result(source, request_text, error_msg, "error")
            self._handle_failure(file, basename)

    # ===================================================================
    # GSD Track
    # ===================================================================
    async def _process_gsd(self, file: Path, basename: str, data: dict,
                           mode: str, request_text: str, source: dict):
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

        self._blocked_file.unlink(missing_ok=True)

        keyword = data.get("keyword", "")
        header = _build_header(source, keyword)

        # 새 GSD 작업이면 이전 세션 + 작업 큐 종료
        if mode == "gsd":
            self._gsd_active_file.unlink(missing_ok=True)
            self._clear_workqueue()
            self._plan_file.unlink(missing_ok=True)

        if mode == "gsd-resume":
            # 계획서가 존재하면 작업 큐를 생성하고 순차 실행
            if self._plan_file.exists() and not self._has_workqueue():
                units = self._parse_plan()
                if units:
                    self._enqueue_plan_units(units, source, data)
                    self._assemble_outbox(
                        file, basename,
                        f"작업 큐 생성 완료 ({len(units)}개 단위). 순차 실행을 시작합니다.",
                        source, data)
                    await self._notify_result(
                        source, request_text,
                        f"작업 큐 {len(units)}개 단위 생성", "success")
                    self._fail_count_file.unlink(missing_ok=True)
                    self._cooldown_file.unlink(missing_ok=True)
                    self._cooldown_alert_file.unlink(missing_ok=True)
                    if self._config.gsd_session_timeout_minutes > 0:
                        self._gsd_active_file.write_text(str(os.getpid()))
                    return

            await self._send_alert(f"{header} GSD 재개")

            # 세션 유실 여부 판단: .gsd-active에 기록된 PID와 현재 PID 비교
            session_alive = self._is_gsd_session_alive()
            channel_id = source.get("channel_id", "")
            conversation_id = data.get("conversation_id", "")

            if session_alive:
                # 세션 유지: Claude가 이전 맥락을 보유, 유연한 프롬프트로 전달
                prompt = (
                    f"{request_text}\n\n"
                    "이전 작업 맥락을 유지하여 사용자의 요청을 이어서 처리해주세요. "
                    "완료 후 결과를 요약해서 텍스트로만 출력해주세요."
                )
                result = await self._run_claude(
                    prompt, continue_session=True,
                    progress_label=f"GSD 재개: {request_text[:20]}",
                )
            else:
                # 세션 유실 → sent/ 이력으로 맥락 복원
                logger.info("GSD 세션 유실 감지 — sent/ 이력으로 맥락 복원")
                context = self._build_context_from_history(
                    channel_id, conversation_id)
                if context:
                    prompt = (
                        f"이전 대화 맥락을 참고하여 작업을 이어서 진행하세요.\n\n"
                        f"{context}\n\n"
                        f"사용자의 새 메시지: {request_text}\n\n"
                        "위 맥락을 반영하여 사용자의 요청을 처리해주세요. "
                        "완료 후 결과를 요약해서 텍스트로만 출력해주세요."
                    )
                else:
                    prompt = (
                        f"{request_text}\n\n"
                        "사용자의 요청을 처리해주세요. "
                        "완료 후 결과를 요약해서 텍스트로만 출력해주세요."
                    )
                result = await self._run_claude(
                    prompt,
                    progress_label=f"GSD 재개(복원): {request_text[:20]}",
                )
        else:
            await self._send_alert(f"{header} GSD 시작")
            gsd_prompt = (
                f"{request_text}\n\n"
                "## 실행 룰 (반드시 준수)\n"
                "작업 규모를 판단하세요:\n"
                "- 10분 이내 완료 가능한 단순 작업 → 즉시 실행하고 결과를 텍스트로 출력\n"
                "- 10분 초과 예상되는 복잡한 작업 → 아래 규칙에 따라 계획서를 작성\n\n"
                "### 계획서 작성 규칙 (복잡한 작업일 때만)\n"
                f"1. `{self._plan_file}` 파일을 생성하세요\n"
                "2. 아래 형식을 정확히 따르세요:\n"
                "```markdown\n"
                "# GSD 작업 계획\n"
                "## 원본 요청\n"
                "{사용자 요청 원문}\n"
                "## 단위 작업\n"
                "- [ ] Unit 1: {작업 제목}\n"
                "  - 범위: {구체적 작업 내용}\n"
                "  - 대상: {변경 파일 목록}\n"
                "- [ ] Unit 2: {작업 제목}\n"
                "  - 범위: {구체적 작업 내용}\n"
                "  - 대상: {변경 파일 목록}\n"
                "```\n"
                "3. 계획서 내용을 사용자에게 보고하세요 (텍스트로 출력)\n"
                "4. 계획서 작성만 하고, 직접 코드를 수정하지 마세요\n"
            )
            result = await self._run_claude(
                gsd_prompt,
                progress_label=f"GSD: {request_text[:20]}",
            )

        self._track_tokens(result)

        blocker_text = self._check_gsd_blockers()
        if blocker_text:
            self._blocked_file.write_text(basename)
            blocked_msg = (
                f"GSD 블로킹: {blocker_text}\n\n"
                "답변을 보내주세요. 다음 메시지가 GSD 재개 응답으로 전달됩니다."
            )
            self._assemble_outbox(file, basename, blocked_msg, source, data)
            await self._notify_result(source, request_text, blocker_text, "blocked")
        elif result and result.get("subtype") == "success" and result.get("result"):
            # 계획서가 생성되었으면 알림 메시지에 안내 추가
            response_text = result["result"]
            if self._plan_file.exists() and mode == "gsd":
                response_text += "\n\n진행하시려면 '진행해주세요'라고 답변해주세요."
            self._assemble_outbox(file, basename, response_text, source, data)
            await self._notify_result(source, request_text, response_text, "success")
            self._fail_count_file.unlink(missing_ok=True)
            self._cooldown_file.unlink(missing_ok=True)
            self._cooldown_alert_file.unlink(missing_ok=True)
            # GSD 세션 활성 표시 (PID 기록 → 세션 유실 판단용)
            if self._config.gsd_session_timeout_minutes > 0:
                self._gsd_active_file.write_text(str(os.getpid()))
        else:
            error_msg = (result or {}).get("result", "GSD 처리 실패")
            await self._notify_result(source, request_text, error_msg, "error")
            self._gsd_active_file.unlink(missing_ok=True)
            self._handle_failure(file, basename)

    # ===================================================================
    # Claude 실행
    # ===================================================================
    async def _run_claude(self, prompt: str, continue_session: bool = False,
                          model: str | None = None,
                          ephemeral: bool = False,
                          bare: bool = False,
                          agent: str | None = None,
                          progress_label: str | None = None) -> dict | None:
        # 보안 룰 주입 (분류/bare 호출 제외)
        if not ephemeral and not bare:
            prompt = self._apply_security_rules(prompt)

        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--dangerously-skip-permissions"]
        if continue_session:
            cmd.append("--continue")
        if model:
            cmd.extend(["--model", model])
        if ephemeral:
            cmd.append("--no-session-persistence")
        if bare:
            cmd.append("--bare")
        if agent:
            cmd.extend(["--agent", agent])
        # --add-dir: 추가 디렉토리 접근 권한
        for d in self._config.claude_additional_dirs:
            cmd.extend(["--add-dir", d])

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
    def _is_quiet_hour(self) -> bool:
        """현재 시각이 알림 무음 시간대인지 확인한다."""
        start = self._config.alert_quiet_start
        end = self._config.alert_quiet_end
        if start < 0 or end < 0:
            return False
        hour = datetime.now(KST).hour
        if start <= end:
            # 예: 23~6이 아닌 09~18 같은 일반 범위
            return start <= hour < end
        else:
            # 자정을 넘는 범위: 예: 23~06
            return hour >= start or hour < end

    async def _send_alert(self, text: str):
        if self._is_quiet_hour():
            logger.info(f"알림 무음 시간대 — 알림 생략: {text[:50]}")
            return

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

        gfc = self._read_int_file(self._fail_count_file) + 1
        self._write_int_file(self._fail_count_file, gfc)

        if gfc >= MAX_GLOBAL_FAILURES:
            retry_min = self._config.claude_cooldown_retry_minutes
            resume_at = int(time.time()) + retry_min * 60
            self._cooldown_file.write_text(str(resume_at))
            self._active_file.unlink(missing_ok=True)
            self._fail_count_file.unlink(missing_ok=True)
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
            if self._token_track_file.exists():
                try:
                    existing = json.loads(self._token_track_file.read_text())
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

            self._token_track_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"토큰 추적 실패: {e}")

    # ===================================================================
    # 쿨다운
    # ===================================================================
    def _is_cooldown(self) -> bool:
        if not self._cooldown_file.exists():
            self._cooldown_alert_file.unlink(missing_ok=True)
            return False
        try:
            resume_at = int(self._cooldown_file.read_text().strip())
            now = int(time.time())
            if now < resume_at:
                cooldown_start = resume_at - self._config.claude_cooldown_retry_minutes * 60
                elapsed = now - cooldown_start
                if elapsed >= COOLDOWN_ALERT_SECONDS and not self._cooldown_alert_file.exists():
                    self._cooldown_alert_file.touch()
                    asyncio.get_running_loop().create_task(
                        self._send_alert(
                            "[시스템] 쿨다운 5시간 이상 지속. 토큰 상태 확인 필요. /resume 으로 재시도 가능."
                        )
                    )
                return True
            self._cooldown_file.unlink(missing_ok=True)
            self._cooldown_alert_file.unlink(missing_ok=True)
            return False
        except (ValueError, OSError):
            self._cooldown_file.unlink(missing_ok=True)
            return False

    # ===================================================================
    # 작업 큐 (Workqueue)
    # ===================================================================
    def _has_workqueue(self) -> bool:
        """작업 큐에 대기 중인 항목이 있는지 확인한다."""
        return bool(list(self._workqueue_dir.glob("*.json")))

    def _parse_plan(self) -> list[dict]:
        """`.gsd-plan.md`를 파싱하여 미완료 Unit 목록을 반환한다."""
        if not self._plan_file.exists():
            return []
        try:
            content = self._plan_file.read_text()
        except OSError:
            return []

        import re
        units = []
        # - [ ] Unit N: 제목 형식 파싱
        pattern = re.compile(
            r"^- \[ \] Unit (\d+):\s*(.+?)$\n"
            r"(?:  - 범위:\s*(.+?)$\n)?"
            r"(?:  - 대상:\s*(.+?)$)?",
            re.MULTILINE,
        )
        for match in pattern.finditer(content):
            units.append({
                "unit_number": int(match.group(1)),
                "title": match.group(2).strip(),
                "description": (match.group(3) or "").strip(),
                "target_files": (match.group(4) or "").strip(),
            })
        # 패턴 매칭이 안 되면 간단한 형식 시도
        if not units:
            for match in re.finditer(r"^- \[ \] Unit (\d+):\s*(.+)$", content, re.MULTILINE):
                units.append({
                    "unit_number": int(match.group(1)),
                    "title": match.group(2).strip(),
                    "description": "",
                    "target_files": "",
                })
        return units

    def _enqueue_plan_units(self, units: list[dict], source: dict, data: dict):
        """파싱된 Unit 목록을 workqueue/ 디렉토리에 파일로 생성한다."""
        total = len(units)
        for unit in units:
            n = unit["unit_number"]
            filename = f"{n:03d}_unit{n}.json"
            item = {
                "unit_number": n,
                "total_units": total,
                "title": unit["title"],
                "description": unit["description"],
                "target_files": unit["target_files"],
                "plan_file": str(self._plan_file),
                "source": source,
                "keyword": data.get("keyword", ""),
                "status": "pending",
            }
            tmp = self._workqueue_dir / f".{filename}.tmp"
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2))
            tmp.rename(self._workqueue_dir / filename)
        logger.info(f"작업 큐 생성: {total}개 단위")

    def _clear_workqueue(self):
        """작업 큐를 비운다."""
        for f in self._workqueue_dir.glob("*.json"):
            f.unlink(missing_ok=True)
        for f in self._workqueue_dir.glob("*.json.processing"):
            f.unlink(missing_ok=True)

    async def _process_workqueue(self):
        """작업 큐에서 다음 항목을 꺼내 실행한다."""
        files = sorted(self._workqueue_dir.glob("*.json"))
        if not files:
            return

        file = files[0]
        processing_file = file.with_suffix(".json.processing")
        try:
            file.rename(processing_file)
        except OSError:
            return

        try:
            item = json.loads(processing_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"작업 큐 파일 읽기 실패: {file.name} — {e}")
            processing_file.unlink(missing_ok=True)
            return

        n = item["unit_number"]
        total = item["total_units"]
        title = item["title"]
        description = item["description"]
        target_files = item["target_files"]
        source = item.get("source", {})
        keyword = item.get("keyword", "")
        header = _build_header(source, keyword)

        await self._send_alert(
            f"{header} Unit {n}/{total} 시작: {title}")

        prompt = (
            f"`{self._plan_file}`를 읽고 Unit {n}: '{title}'을 실행하세요.\n\n"
            f"작업 내용: {description}\n"
            f"대상 파일: {target_files}\n\n"
            "## 실행 규칙\n"
            "1. 이 단위 작업만 수행하세요 (다른 단위는 건드리지 마세요)\n"
            f"2. 완료 후 `{self._plan_file}`에서 해당 항목을 `- [x]`로 체크하세요\n"
            "3. 결과를 요약해서 텍스트로 출력하세요\n"
        )

        self._active_file.touch()
        result = await self._run_claude(
            prompt,
            progress_label=f"Unit {n}/{total}: {title[:15]}",
        )
        self._track_tokens(result)

        if result and result.get("subtype") == "success" and result.get("result"):
            # 성공: 결과를 outbox로 발송
            response_text = f"Unit {n}/{total} 완료: {title}\n\n{result['result']}"

            # 남은 큐 확인
            remaining = len(list(self._workqueue_dir.glob("*.json")))
            if remaining == 0:
                response_text += f"\n\n전체 완료 ({total}/{total})"
                self._plan_file.unlink(missing_ok=True)
                self._gsd_active_file.unlink(missing_ok=True)

            self._send_workqueue_result(
                response_text, source, keyword)
            processing_file.unlink(missing_ok=True)

            self._fail_count_file.unlink(missing_ok=True)
            self._cooldown_file.unlink(missing_ok=True)
            self._cooldown_alert_file.unlink(missing_ok=True)

            if self._config.gsd_session_timeout_minutes > 0:
                self._gsd_active_file.write_text(str(os.getpid()))

            logger.info(f"작업 큐 Unit {n}/{total} 완료: {title}")
        else:
            # 실패: 복원하여 재시도
            try:
                processing_file.rename(file)
            except OSError:
                pass

            fc_path = Path(str(file) + ".failcount")
            fc = self._read_int_file(fc_path) + 1

            if fc >= MAX_FILE_FAILURES:
                # 이 Unit은 skip하고 다음으로
                fc_path.unlink(missing_ok=True)
                file.unlink(missing_ok=True)
                asyncio.get_running_loop().create_task(
                    self._send_alert(
                        f"{header} Unit {n}/{total} 실패 ({fc}회). "
                        f"건너뛰고 다음 단위를 진행합니다."
                    )
                )
                logger.warning(f"작업 큐 Unit {n} 실패 초과, skip")
            else:
                self._write_int_file(fc_path, fc)
                logger.info(f"작업 큐 Unit {n} 실패 ({fc}/{MAX_FILE_FAILURES})")

            # 전역 실패 카운터
            gfc = self._read_int_file(self._fail_count_file) + 1
            self._write_int_file(self._fail_count_file, gfc)
            if gfc >= MAX_GLOBAL_FAILURES:
                retry_min = self._config.claude_cooldown_retry_minutes
                resume_at = int(time.time()) + retry_min * 60
                self._cooldown_file.write_text(str(resume_at))
                self._active_file.unlink(missing_ok=True)
                self._fail_count_file.unlink(missing_ok=True)
                asyncio.get_running_loop().create_task(
                    self._send_alert(
                        f"[시스템] 연속 실패 감지. {retry_min}분 후 자동 재시도. "
                        "즉시 재개: /resume"
                    )
                )

    def _send_workqueue_result(self, text: str, source: dict, keyword: str):
        """작업 큐 실행 결과를 outbox에 발송한다."""
        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_wq-result.json"

        header = _build_header(source, keyword)
        headed_text = f"{header} 처리결과\n\n{text}"

        if self._channel_manager:
            targets = self._channel_manager.build_broadcast_targets(source)
        else:
            targets = [{
                "channel_type": source.get("channel_type", "telegram"),
                "channel_id": source.get("channel_id", ""),
                "is_origin": True,
            }]

        data = {
            "id": str(uuid.uuid4()),
            "source": source,
            "targets": targets,
            "retry_count": 0,
            "keyword": keyword,
            "request": None,
            "response": {
                "text": headed_text,
                "parse_mode": "HTML",
                "timestamp": now.isoformat(),
            },
        }
        tmp = self._config.outbox_dir / f".{filename}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.rename(self._config.outbox_dir / filename)

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
    # GSD 세션 관리
    # ===================================================================
    def _is_gsd_session_alive(self) -> bool:
        """GSD 세션이 현재 프로세스에서 유지되고 있는지 확인한다."""
        if not self._gsd_active_file.exists():
            return False
        try:
            stored_pid = int(self._gsd_active_file.read_text().strip())
            return stored_pid == os.getpid()
        except (ValueError, OSError):
            return False

    def is_gsd_active(self) -> bool:
        """GSD 세션이 활성 상태이고 타임아웃되지 않았는지 확인한다.
        Orchestrator에서 호출용."""
        if not self._gsd_active_file.exists():
            return False
        timeout = self._config.gsd_session_timeout_minutes
        if timeout <= 0:
            return False
        try:
            age = time.time() - self._gsd_active_file.stat().st_mtime
            if age > timeout * 60:
                self._gsd_active_file.unlink(missing_ok=True)
                return False
            return True
        except OSError:
            return False

    def clear_gsd_active(self) -> None:
        """GSD 세션 활성 상태를 해제한다."""
        self._gsd_active_file.unlink(missing_ok=True)
        self._clear_workqueue()
        self._plan_file.unlink(missing_ok=True)

    def has_pending_plan(self) -> bool:
        """승인 대기 중인 GSD 계획서가 있는지 확인한다."""
        return self._plan_file.exists()

    def _build_context_from_history(self, user_channel_id: str,
                                     conversation_id: str = "") -> str:
        """sent/ 디렉토리에서 최근 대화 이력을 읽어 맥락 문자열을 생성한다.

        conversation_id가 지정되면 같은 대화의 메시지를 우선 수집한다.
        """
        max_messages = self._config.gsd_history_max_messages
        if max_messages <= 0:
            return ""

        # sent/ + 오늘 archive/ 에서 파일 수집
        candidates: list[Path] = []
        candidates.extend(self._config.sent_dir.glob("*.json"))

        today = datetime.now(KST).strftime("%Y-%m-%d")
        today_archive = self._config.archive_dir / today
        if today_archive.exists():
            candidates.extend(today_archive.glob("*.json"))

        # system-alert 제외, 시간순 정렬 (최신 먼저)
        candidates = [f for f in candidates if "_system-alert" not in f.name]
        candidates.sort(key=lambda f: f.name, reverse=True)

        # conversation_id가 있으면 같은 대화 메시지 우선 수집
        messages = []
        if conversation_id:
            for f in candidates:
                if len(messages) >= max_messages:
                    break
                try:
                    data = json.loads(f.read_text())
                    if data.get("conversation_id") != conversation_id:
                        continue
                    req = data.get("request", {})
                    resp = data.get("response", {})
                    if not req or not resp or not resp.get("text"):
                        continue
                    messages.append({
                        "request": req.get("text", ""),
                        "response": resp.get("text", ""),
                    })
                except (json.JSONDecodeError, OSError):
                    continue

        # conversation_id로 못 찾으면 channel_id 기반 fallback
        if not messages:
            for f in candidates:
                if len(messages) >= max_messages:
                    break
                try:
                    data = json.loads(f.read_text())
                    source = data.get("source", {})
                    if source.get("channel_id") != user_channel_id:
                        continue
                    req = data.get("request", {})
                    resp = data.get("response", {})
                    if not req or not resp or not resp.get("text"):
                        continue
                    messages.append({
                        "request": req.get("text", ""),
                        "response": resp.get("text", ""),
                    })
                except (json.JSONDecodeError, OSError):
                    continue

        if not messages:
            return ""

        # 시간순으로 되돌림 (오래된 것 → 최신)
        messages.reverse()

        lines = ["--- 이전 대화 ---"]
        for msg in messages:
            lines.append(f"[요청] {msg['request']}")
            # 응답에서 헤더 제거 (처리결과 이후 본문만)
            resp_text = msg["response"]
            if "] 처리결과\n\n" in resp_text:
                resp_text = resp_text.split("] 처리결과\n\n", 1)[1]
            # 길이 제한 (맥락이 너무 길면 토큰 낭비)
            if len(resp_text) > 2000:
                resp_text = resp_text[:2000] + "\n... (이하 생략)"
            lines.append(f"[응답] {resp_text}")
        lines.append("--- 이전 대화 끝 ---")

        return "\n".join(lines)

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
