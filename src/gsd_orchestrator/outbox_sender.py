import asyncio
import json
import logging
from pathlib import Path

from .channels.manager import ChannelManager

logger = logging.getLogger(__name__)

MAX_OUTBOX_RETRIES = 3


class OutboxSender:
    def __init__(self, channel_manager: ChannelManager, outbox_dir: Path,
                 sent_dir: Path, error_dir: Path, interval: int = 3,
                 snippet_length: int = 500):
        self._channel_manager = channel_manager
        self._dir = outbox_dir
        self._sent_dir = sent_dir
        self._error_dir = error_dir
        self._snippet_length = snippet_length
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sent_dir.mkdir(parents=True, exist_ok=True)
        self._error_dir.mkdir(parents=True, exist_ok=True)
        self._interval = interval

    def _recover_stale_sending(self) -> None:
        """잔류 .sending 파일을 원래 이름으로 복원한다."""
        for sending_file in self._dir.glob("*.json.sending"):
            original = self._dir / sending_file.name.replace(".sending", "")
            try:
                sending_file.rename(original)
                logger.info(f"잔류 .sending 파일 복원: {original.name}")
            except OSError as e:
                logger.error(f".sending 복원 실패: {sending_file.name} — {e}")

    async def run(self):
        """outbox 디렉토리를 폴링하여 채널로 발송한다."""
        self._recover_stale_sending()
        while True:
            try:
                await self._process_outbox()
            except Exception as e:
                logger.error(f"outbox_sender 에러: {e}")
            await asyncio.sleep(self._interval)

    async def _process_outbox(self):
        files = sorted(self._dir.glob("*.json"))
        for filepath in files:
            sending_path = filepath.with_suffix(".json.sending")
            try:
                filepath.rename(sending_path)
            except OSError:
                continue

            try:
                data = json.loads(sending_path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.error(f"JSON 파싱 실패, error/로 격리: {filepath.name} — {e}")
                sending_path.rename(self._error_dir / filepath.name)
                continue

            response = data.get("response")
            if not response or not response.get("text"):
                logger.warning(f"응답 없음, error/로 격리: {filepath.name}")
                sending_path.rename(self._error_dir / filepath.name)
                continue

            try:
                await self._send_to_targets(data, filepath, sending_path)
            except Exception as e:
                logger.error(f"발송 실패, error/로 격리: {filepath.name} — {e}")
                sending_path.rename(self._error_dir / filepath.name)

    async def _send_to_targets(self, data: dict, filepath: Path, sending_path: Path):
        targets = data.get("targets", [])
        source = data.get("source", {})
        response = data["response"]
        response_text = response["text"]
        parse_mode = response.get("parse_mode", "HTML")
        retry_count = data.get("retry_count", 0)

        # 하위 호환: targets 없으면 source 또는 chat_id로 단일 타겟
        if not targets:
            channel_type = source.get("channel_type", "telegram") if source else "telegram"
            channel_id = source.get("channel_id", "") if source else data.get("chat_id", "")
            targets = [{"channel_type": channel_type, "channel_id": channel_id, "is_origin": True}]

        failed_targets = []
        for target in targets:
            text = self._build_text(target, source, response_text)
            ok = await self._channel_manager.send_to(
                target["channel_type"], target["channel_id"], text, parse_mode)
            if not ok:
                # parse_mode 문제일 수 있으므로 plain text 재시도
                ok = await self._channel_manager.send_to(
                    target["channel_type"], target["channel_id"], text, None)
            if not ok:
                failed_targets.append(target)

        if not failed_targets:
            sending_path.rename(self._sent_dir / filepath.name)
            resp_preview = response_text[:100].replace("\n", " ")
            logger.info(f"[발송] {resp_preview}")
        elif retry_count >= MAX_OUTBOX_RETRIES:
            logger.error(f"최대 재시도 초과({MAX_OUTBOX_RETRIES}), error/로 이관: {filepath.name}")
            sending_path.rename(self._error_dir / filepath.name)
        else:
            data["targets"] = failed_targets
            data["retry_count"] = retry_count + 1
            sending_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            sending_path.rename(filepath)  # outbox로 복원
            logger.warning(
                f"부분 발송 실패, {len(failed_targets)}건 재시도 예정 "
                f"(retry {retry_count + 1}/{MAX_OUTBOX_RETRIES}): {filepath.name}"
            )

    def _build_text(self, target: dict, source: dict, response_text: str) -> str:
        """origin은 전체 텍스트, 상대 채널은 snippet_length 요약본."""
        if target.get("is_origin", True):
            return response_text
        if self._snippet_length <= 0 or len(response_text) <= self._snippet_length:
            return response_text
        snippet = response_text[:self._snippet_length]
        origin = source.get("channel_type", "원본 채널")
        return f"{snippet}\n... [전체 내용은 {origin}에서 확인하세요]"
