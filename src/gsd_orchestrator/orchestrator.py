import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Callable, Awaitable

from .config import Config
from .channels.base import ChannelAdapter
from .channels.manager import ChannelManager
from .channels.telegram import TelegramAdapter
from .inbox_writer import InboxWriter, extract_keyword
from .outbox_sender import OutboxSender
from .inbox_processor import InboxProcessor
from .archiver import Archiver
from .api import ChannelSender

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# GSD 작업 계속 의도를 나타내는 경량 패턴 (Claude 호출 없이 판별)
_GSD_CONTINUE_PATTERNS = frozenset({
    "진행해주세요", "진행해", "진행", "네", "ㅇㅇ", "응", "확인",
    "승인", "계속", "시작", "고", "ㄱ", "ok", "yes", "go",
    "진행하겠습니다", "부탁합니다", "해주세요", "ㅇ", "넹", "넵",
    "해줘", "해주세요", "좋아", "그래", "시작해", "시작해주세요",
})


# on_result 콜백 타입: (source, request_text, response_text, status)
ResultCallback = Callable[[dict, str, str, str], Awaitable[None]]


class Orchestrator:
    """메인 오케스트레이터. 채널 어댑터와 백그라운드 태스크를 관리한다.

    단독 실행: run() 또는 await start() / await stop()
    연동 실행: on_result(callback) + channel_sender로 인터페이스 제공
    """

    def __init__(self, config: Config):
        self._config = config
        self._inbox_writer = InboxWriter(config.inbox_dir)
        self._result_callback: ResultCallback | None = None
        self._tasks: list[asyncio.Task] = []
        self._blocked_file = config.runtime_path("blocked")
        self._start_time = int(time.time())
        self._recent_message_ids: OrderedDict = OrderedDict()

        # 채널 어댑터 생성
        adapters: list[ChannelAdapter] = []

        runtime_paths = {
            name: config.runtime_path(name)
            for name in ("blocked", "token-usage", "reset", "cooldown",
                         "failcount", "cooldown-alerted", "gsd-active")
        }

        if config.telegram_enabled and config.telegram_bot_token:
            adapters.append(TelegramAdapter(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                runtime_paths=runtime_paths,
                attachments_config={
                    "allowed_extensions": config.attachments_allowed_extensions,
                    "max_file_size": config.attachments_max_file_size,
                    "temp_dir": config.attachments_temp_dir,
                    "reject_message": config.attachments_reject_message,
                },
                error_dir=config.error_dir,
                outbox_dir=config.outbox_dir,
            ))

        if config.slack_enabled and config.slack_bot_token:
            try:
                from .channels.slack import SlackAdapter
                adapters.append(SlackAdapter(
                    bot_token=config.slack_bot_token,
                    app_token=config.slack_app_token,
                    channel_id=config.slack_channel_id,
                    attachments_config={
                        "allowed_extensions": config.attachments_allowed_extensions,
                        "max_file_size": config.attachments_max_file_size,
                        "temp_dir": config.attachments_temp_dir,
                        "reject_message": config.attachments_reject_message,
                    },
                ))
            except ImportError as e:
                logger.error(f"Slack 어댑터 로드 실패: {e}")
                raise

        if not adapters:
            raise RuntimeError("최소 하나의 채널이 활성화되어야 합니다.")

        self._channel_manager = ChannelManager(adapters)

        self._inbox_processor = InboxProcessor(
            config, self._channel_manager, result_callback=self._fire_result)
        self._outbox_sender = OutboxSender(
            channel_manager=self._channel_manager,
            outbox_dir=config.outbox_dir,
            sent_dir=config.sent_dir,
            error_dir=config.error_dir,
            interval=config.outbox_interval,
            snippet_length=config.broadcast_snippet_length,
        )

        self._channel_sender = ChannelSender(self._channel_manager, config.outbox_dir)

    # ── 외부 연동 인터페이스 ──────────────────────────────

    @property
    def channel_sender(self) -> ChannelSender:
        """호스트 앱용 채널 발송 API."""
        return self._channel_sender

    def on_result(self, callback: ResultCallback) -> None:
        """처리 결과 콜백을 등록한다.

        callback(source, request_text, response_text, status)
        status: "success" | "error" | "blocked"
        """
        self._result_callback = callback
        # inbox_processor에도 반영
        self._inbox_processor.set_result_callback(self._fire_result)

    async def _fire_result(self, source: dict, request_text: str,
                           response_text: str, status: str) -> None:
        """등록된 콜백이 있으면 호출한다."""
        if self._result_callback:
            try:
                await self._result_callback(source, request_text, response_text, status)
            except Exception as e:
                logger.error(f"result_callback 에러: {e}")

    # ── 메시지 핸들링 ────────────────────────────────────

    async def _on_channel_message(self, source: dict, text: str,
                                  mode: str = "default",
                                  extracted_text: str = "") -> None:
        """모든 채널 어댑터의 공통 메시지 콜백."""
        logger.info(f"[수신] [{source.get('channel_type')}] {text}")

        # ── 중복 메시지 방지 (message_id 기반) ──
        if self._is_duplicate_message(source):
            logger.info(f"중복 메시지 skip: {source.get('channel_type')}_{source.get('message_id')}")
            return

        pending = self._inbox_writer.pending_count()

        keyword = extract_keyword(text)
        header = f"[{source.get('channel_type', '')}][{source.get('user_name', '')}][{keyword}]"

        # 메시지 수신 지연 감지 (60초 이상)
        delay_notice = ""
        message_ts = source.get("message_ts", 0)
        if message_ts > 0:
            delay = int(time.time()) - message_ts
            if delay >= 60:
                minutes = delay // 60
                seconds = delay % 60
                if message_ts < self._start_time:
                    delay_notice = f"\n(앱 정지 중 수신된 메시지입니다. {minutes}분 {seconds}초 전 전송)"
                else:
                    delay_notice = f"\n(네트워크 불안정으로 수신이 {minutes}분 {seconds}초 지연되었습니다)"
                logger.warning(f"메시지 수신 지연: {delay}초 ({source.get('channel_type')})")

        is_continuation = self._is_gsd_continuation(text)

        # GSD 블로킹 상태에서 사용자 응답 → gsd-resume
        if mode == "default" and self._blocked_file.exists():
            conv_id = self._resolve_conversation_id(source, is_continuation=True)
            self._inbox_writer.write(source, text, mode="gsd-resume",
                                     extracted_text=extracted_text,
                                     conversation_id=conv_id)
            try:
                self._blocked_file.unlink()
            except OSError:
                pass
            if pending > 0:
                msg = f"{header} GSD 블로킹 응답 접수. (앞선 작업 {pending}건 처리 중)"
            else:
                msg = f"{header} GSD 블로킹 응답 접수."
            await self._channel_manager.send_to(
                source.get("channel_type", ""), source.get("channel_id", ""),
                msg + delay_notice)
            return

        # 계속 패턴 + 계획서 존재 → GSD 세션 만료와 무관하게 gsd-resume (타임아웃 방어)
        if mode == "default" and is_continuation and self._inbox_processor.has_pending_plan():
            conv_id = self._resolve_conversation_id(source, is_continuation=True)
            self._inbox_writer.write(source, text, mode="gsd-resume",
                                     extracted_text=extracted_text,
                                     conversation_id=conv_id)
            if pending > 0:
                msg = f"{header} 계획서 기반 작업을 이어갑니다. (앞선 작업 {pending}건 처리 중)"
            else:
                msg = f"{header} 계획서 기반 작업을 이어갑니다."
            await self._channel_manager.send_to(
                source.get("channel_type", ""), source.get("channel_id", ""),
                msg + delay_notice)
            return

        # GSD 세션 활성 — 의도 기반 분류
        if mode == "default" and self._inbox_processor.is_gsd_active():
            if is_continuation:
                # GSD 계속 의도 → gsd-resume
                conv_id = self._resolve_conversation_id(source, is_continuation=True)
                self._inbox_writer.write(source, text, mode="gsd-resume",
                                         extracted_text=extracted_text,
                                         conversation_id=conv_id)
                if pending > 0:
                    msg = f"{header} GSD 작업 이어서 진행합니다. (앞선 작업 {pending}건 처리 중)"
                else:
                    msg = f"{header} GSD 작업 이어서 진행합니다."
                await self._channel_manager.send_to(
                    source.get("channel_type", ""), source.get("channel_id", ""),
                    msg + delay_notice)
                return
            else:
                # 새로운 요청 → GSD 세션 종료, default로 fall through
                logger.info("GSD 활성 중 새 요청 감지 — 세션 종료 후 default 처리")
                self._inbox_processor.clear_gsd_active()

        # inbox 저장 (delay_notice는 source에 포함하여 inbox_processor에서 활용)
        if delay_notice:
            source["delay_notice"] = delay_notice
        conv_id = self._resolve_conversation_id(source, is_continuation=False)
        self._inbox_writer.write(source, text, mode=mode,
                                 extracted_text=extracted_text,
                                 conversation_id=conv_id)

        # 수신 확인은 inbox_processor가 .processing 전환 시 발송

    # ── 의도 판별 헬퍼 ─────────────────────────────────

    @staticmethod
    def _is_gsd_continuation(text: str) -> bool:
        """사용자 메시지가 GSD 작업 계속 의도인지 경량 패턴 매칭으로 판별."""
        normalized = text.strip().lower().rstrip(".!?~")
        return normalized in _GSD_CONTINUE_PATTERNS

    def _is_duplicate_message(self, source: dict) -> bool:
        """message_id 기반 중복 메시지 감지."""
        msg_id = source.get("message_id")
        if not msg_id:
            return False
        ch = source.get("channel_type", "")
        key = f"{ch}_{msg_id}"
        if key in self._recent_message_ids:
            return True
        self._recent_message_ids[key] = True
        while len(self._recent_message_ids) > 100:
            self._recent_message_ids.popitem(last=False)
        return False

    def _resolve_conversation_id(self, source: dict, is_continuation: bool) -> str:
        """conversation_id를 결정한다. 계속이면 최근 대화 계승, 아니면 새로 생성."""
        if is_continuation:
            cid = self._find_recent_conversation_id(source)
            if cid:
                return cid
        return f"conv-{datetime.now(KST).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    def _find_recent_conversation_id(self, source: dict) -> str | None:
        """sent/ + 오늘 archive/에서 같은 사용자의 최근 conversation_id를 찾는다."""
        channel_id = source.get("channel_id", "")
        candidates: list = list(self._config.sent_dir.glob("*.json"))
        today = datetime.now(KST).strftime("%Y-%m-%d")
        today_archive = self._config.archive_dir / today
        if today_archive.exists():
            candidates.extend(today_archive.glob("*.json"))
        candidates = [f for f in candidates if "_system-alert" not in f.name]
        candidates.sort(key=lambda f: f.name, reverse=True)

        for f in candidates[:10]:
            try:
                data = json.loads(f.read_text())
                if data.get("source", {}).get("channel_id") == channel_id:
                    cid = data.get("conversation_id")
                    if cid:
                        return cid
            except (json.JSONDecodeError, OSError):
                continue
        return None

    # ── 백그라운드 태스크 ────────────────────────────────

    async def _inbox_stale_check(self):
        """inbox 파일 체류 시간 감시. 5분 이상 미처리 시 알림."""
        while True:
            try:
                for f in self._config.inbox_dir.glob("*.json"):
                    age = time.time() - f.stat().st_mtime
                    if age > 300:
                        await self._channel_manager.broadcast_all(
                            "[시스템] Inbox 메시지 미처리 경고: Claude Code 상태 확인 필요"
                        )
                        break
            except Exception as e:
                logger.error(f"inbox stale check 에러: {e}")
            await asyncio.sleep(60)

    async def _run_archiver(self):
        """매시간 archiver 실행."""
        archiver = Archiver(
            self._config.sent_dir,
            self._config.archive_dir,
            self._config.message_retention_days,
        )
        while True:
            try:
                archiver.run()
            except Exception as e:
                logger.error(f"archiver 에러: {e}")
            await asyncio.sleep(3600)

    # ── 진입점 ───────────────────────────────────────────

    def run(self) -> None:
        """블로킹 실행. asyncio.run() 내부 호출."""
        asyncio.run(self._run_blocking())

    async def _run_blocking(self):
        await self.start()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("종료 신호 수신")
        finally:
            await self.stop()

    async def start(self) -> None:
        """비동기 실행. 호스트 앱의 이벤트 루프에서 호출 가능."""
        logger.info("gsd-orchestrator 시작")

        # 백그라운드 태스크를 먼저 시작 (채널 어댑터의 long-polling이 루프를 점유하기 전)
        self._tasks = [
            asyncio.create_task(self._inbox_processor.run()),
            asyncio.create_task(self._outbox_sender.run()),
            asyncio.create_task(self._inbox_stale_check()),
            asyncio.create_task(self._run_archiver()),
        ]

        await self._channel_manager.start_all(self._on_channel_message)

    async def stop(self) -> None:
        """비동기 정지."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        await self._channel_manager.stop_all()
        logger.info("gsd-orchestrator 종료")
