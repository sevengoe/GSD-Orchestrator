"""App Bridge 단위 테스트 — file 모드, api 모드, 라우팅, correlation, edge cases."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from gsd_orchestrator.app_bridge import (
    AppRouter, AppCommandWriter, AppResponseCorrelator,
)
from gsd_orchestrator.app_bridge.router import RouteResult
from gsd_orchestrator.api import AppBridge
from gsd_orchestrator.config import _normalize_app_bridge_apps

from tests.fixtures.echo_app import process_one


# ── 픽스처 ─────────────────────────────────────────────

@pytest.fixture
def app_bridge_dirs(tmp_path: Path):
    """App Bridge 통합 테스트용 디렉토리 세트."""
    dirs = {
        "outbox": tmp_path / "messages" / "outbox",
        "echo_inbox": tmp_path / "messages" / "external_inbox" / "echo",
        "sent": tmp_path / "messages" / "sent",
        "error": tmp_path / "messages" / "error",
    }
    for d in dirs.values():
        d.mkdir(parents=True)
    return dirs


@pytest.fixture
def echo_app_config(app_bridge_dirs):
    """echo 앱(file 모드) 정규화된 config."""
    base = app_bridge_dirs["outbox"].parent.parent  # tmp_path
    raw = [{
        "name": "echo",
        "mode": "file",
        "inbox_dir": "messages/external_inbox/echo",
        "command_prefix": ["/echo"],
        "whitelist_user_ids": [],
        "ack_message": "[echo] ack id={id}",
    }]
    return _normalize_app_bridge_apps(raw, base)


@pytest.fixture
def telegram_source():
    return {
        "channel_type": "telegram",
        "channel_id": "999",
        "user_id": "12345",
        "user_name": "tester",
        "message_id": 1,
    }


# ── Config 정규화 / 충돌 검증 ─────────────────────────

def test_config_normalize_basic(tmp_path):
    apps = _normalize_app_bridge_apps([{
        "name": "foo",
        "mode": "file",
        "command_prefix": "/foo",  # 단일 문자열도 list 로 정규화
    }], tmp_path)
    assert len(apps) == 1
    assert apps[0]["command_prefix"] == ["/foo"]
    assert apps[0]["mode"] == "file"
    assert apps[0]["inbox_dir"] == tmp_path / "messages/external_inbox/foo"


def test_config_normalize_default_inbox_dir(tmp_path):
    apps = _normalize_app_bridge_apps([{
        "name": "bar",
        "mode": "file",
        "command_prefix": ["/bar"],
        # inbox_dir 미지정 → 기본 messages/external_inbox/{name}
    }], tmp_path)
    assert apps[0]["inbox_dir"] == tmp_path / "messages/external_inbox/bar"


def test_config_normalize_api_mode_no_inbox_dir(tmp_path):
    apps = _normalize_app_bridge_apps([{
        "name": "api_app",
        "mode": "api",
        "command_prefix": ["/apicmd"],
    }], tmp_path)
    assert apps[0]["mode"] == "api"
    assert apps[0]["inbox_dir"] is None


def test_config_normalize_prefix_conflict(tmp_path):
    with pytest.raises(ValueError, match="중복 등록"):
        _normalize_app_bridge_apps([
            {"name": "a", "mode": "file", "command_prefix": ["/dup"]},
            {"name": "b", "mode": "file", "command_prefix": ["/dup"]},
        ], tmp_path)


def test_config_normalize_invalid_mode(tmp_path):
    with pytest.raises(ValueError, match="mode"):
        _normalize_app_bridge_apps([
            {"name": "x", "mode": "http", "command_prefix": ["/x"]},
        ], tmp_path)


def test_config_normalize_prefix_must_start_with_slash(tmp_path):
    with pytest.raises(ValueError, match="/"):
        _normalize_app_bridge_apps([
            {"name": "x", "mode": "file", "command_prefix": ["foo"]},
        ], tmp_path)


def test_config_normalize_missing_name(tmp_path):
    with pytest.raises(ValueError, match="name"):
        _normalize_app_bridge_apps([
            {"mode": "file", "command_prefix": ["/x"]},
        ], tmp_path)


def test_config_normalize_empty_prefix(tmp_path):
    with pytest.raises(ValueError, match="command_prefix"):
        _normalize_app_bridge_apps([
            {"name": "x", "mode": "file", "command_prefix": []},
        ], tmp_path)


# ── AppRouter — 매칭 / 거부 ──────────────────────────

def test_router_match_basic(echo_app_config, telegram_source):
    router = AppRouter(echo_app_config)
    r = router.route("/echo hello world", telegram_source)
    assert r.matched and not r.rejected
    assert r.app_name == "echo"
    assert r.app_mode == "file"
    assert r.prefix == "/echo"
    assert r.args == ["hello", "world"]
    assert r.command_id  # uuid 문자열
    assert "[echo] ack id=" in r.ack_message


def test_router_no_match_returns_unmatched(echo_app_config, telegram_source):
    router = AppRouter(echo_app_config)
    r = router.route("/notregistered", telegram_source)
    assert not r.matched


def test_router_plain_text_no_match(echo_app_config, telegram_source):
    router = AppRouter(echo_app_config)
    r = router.route("hello there", telegram_source)
    assert not r.matched


def test_router_whitelist_reject(tmp_path, telegram_source):
    apps = _normalize_app_bridge_apps([{
        "name": "secured",
        "mode": "file",
        "command_prefix": ["/sell"],
        "whitelist_user_ids": ["999"],
    }], tmp_path)
    router = AppRouter(apps)
    r = router.route("/sell d2 100", telegram_source)
    assert r.matched and r.rejected
    assert r.reason == "whitelist"
    assert "권한 없음" in r.reject_message


def test_router_whitelist_allow(tmp_path, telegram_source):
    apps = _normalize_app_bridge_apps([{
        "name": "secured",
        "mode": "file",
        "command_prefix": ["/sell"],
        "whitelist_user_ids": ["12345"],  # telegram_source.user_id 일치
    }], tmp_path)
    router = AppRouter(apps)
    r = router.route("/sell d2 100", telegram_source)
    assert r.matched and not r.rejected


def test_router_args_too_long(echo_app_config, telegram_source):
    router = AppRouter(echo_app_config, max_args_length=10)
    r = router.route("/echo " + ("x" * 50), telegram_source)
    assert r.matched and r.rejected
    assert r.reason == "args_too_long"


def test_router_dangerous_chars_blocked(echo_app_config, telegram_source):
    router = AppRouter(echo_app_config)
    for arg in ["foo;rm", "bar|cat", "a`whoami`", "x$(id)"]:
        r = router.route(f"/echo {arg}", telegram_source)
        assert r.matched and r.rejected, f"missed: {arg}"
        assert r.reason == "dangerous_chars"


def test_router_registered_prefixes(tmp_path):
    apps = _normalize_app_bridge_apps([
        {"name": "a", "mode": "file", "command_prefix": ["/a", "/aa"]},
        {"name": "b", "mode": "api", "command_prefix": ["/b"]},
    ], tmp_path)
    router = AppRouter(apps)
    assert router.registered_prefixes == ["/a", "/aa", "/b"]


# ── AppCommandWriter — 파일 작성 ────────────────────

def test_command_writer_writes_atomic(app_bridge_dirs, telegram_source):
    writer = AppCommandWriter(outbox_dir=app_bridge_dirs["outbox"])
    target = writer.write(
        app_inbox_dir=app_bridge_dirs["echo_inbox"],
        app_name="echo",
        command_id="cmd-uuid-123",
        prefix="/echo",
        raw_command="/echo hi there",
        args=["hi", "there"],
        source=telegram_source,
    )
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["command_id"] == "cmd-uuid-123"
    assert data["target_app"] == "echo"
    assert data["command"]["args"] == ["hi", "there"]
    assert data["source"]["channel_type"] == "telegram"
    assert data["reply_to_outbox"] == str(app_bridge_dirs["outbox"])
    # .tmp 잔존 없음
    assert not list(app_bridge_dirs["echo_inbox"].glob(".*.tmp"))


def test_command_writer_creates_dir_if_missing(app_bridge_dirs, telegram_source, tmp_path):
    new_dir = tmp_path / "missing_app"
    assert not new_dir.exists()
    writer = AppCommandWriter(outbox_dir=app_bridge_dirs["outbox"])
    writer.write(
        app_inbox_dir=new_dir, app_name="x", command_id="id1",
        prefix="/x", raw_command="/x", args=[], source=telegram_source,
    )
    assert new_dir.exists()


# ── AppResponseCorrelator — pending / resolve / timeout ──

def test_correlator_register_and_resolve(app_bridge_dirs, telegram_source):
    cor = AppResponseCorrelator(outbox_dir=app_bridge_dirs["outbox"])
    cor.register(command_id="c1", app_name="echo", source=telegram_source)
    assert cor.has_pending("c1")
    pc = cor.resolve("c1")
    assert pc is not None and pc.app_name == "echo"
    assert not cor.has_pending("c1")


def test_correlator_resolve_unknown_returns_none(app_bridge_dirs):
    cor = AppResponseCorrelator(outbox_dir=app_bridge_dirs["outbox"])
    assert cor.resolve("nope") is None
    assert cor.resolve("") is None


def test_correlator_expire_moves_to_expired(app_bridge_dirs, telegram_source):
    cor = AppResponseCorrelator(
        outbox_dir=app_bridge_dirs["outbox"],
        default_timeout_sec=1,
    )
    cor.register(command_id="c2", app_name="echo", source=telegram_source)
    # 인공적으로 sent_at 을 과거로
    cor._pending["c2"].sent_at = time.time() - 10
    cor._check_expired()
    assert not cor.has_pending("c2")
    assert cor.is_expired("c2")
    # 만료 알림 파일이 outbox 에 작성됐는지
    files = list(app_bridge_dirs["outbox"].glob("*_appbridge_timeout.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["command_id"] == "c2"
    assert "응답 없음" in data["response"]["text"]


def test_correlator_late_response_marked_expired(app_bridge_dirs, telegram_source):
    """timeout 후 도착한 응답도 best-effort 로 받아주되 is_expired=True."""
    cor = AppResponseCorrelator(
        outbox_dir=app_bridge_dirs["outbox"],
        default_timeout_sec=1,
    )
    cor.register(command_id="c3", app_name="echo", source=telegram_source)
    cor._pending["c3"].sent_at = time.time() - 10
    cor._check_expired()
    # 늦은 응답 도착 시
    assert cor.is_expired("c3")
    pc = cor.resolve("c3")
    assert pc is not None  # best-effort 회수 가능
    # resolve 후에도 is_expired 가 False (이미 expired dict 에서도 제거됨)
    assert not cor.is_expired("c3")


# ── echo_app fixture — file 모드 round-trip ──────────

def test_echo_app_round_trip(app_bridge_dirs, telegram_source):
    """file 모드 통합: writer → echo_app → outbox."""
    writer = AppCommandWriter(outbox_dir=app_bridge_dirs["outbox"])
    writer.write(
        app_inbox_dir=app_bridge_dirs["echo_inbox"],
        app_name="echo", command_id="cid-roundtrip",
        prefix="/echo", raw_command="/echo hello", args=["hello"],
        source=telegram_source,
    )
    # echo_app 1회 처리
    cmd = process_one(app_bridge_dirs["echo_inbox"], app_bridge_dirs["outbox"])
    assert cmd is not None
    # outbox 에 응답 도착
    replies = list(app_bridge_dirs["outbox"].glob("*_echoreply.json"))
    assert len(replies) == 1
    data = json.loads(replies[0].read_text())
    assert data["command_id"] == "cid-roundtrip"
    assert data["response"]["text"] == "hello"
    # inbox 비워졌는지
    assert not list(app_bridge_dirs["echo_inbox"].glob("*.json"))


def test_echo_app_concurrent_correlation(app_bridge_dirs, telegram_source):
    """여러 명령이 각각의 command_id 로 정확히 매칭되는지."""
    writer = AppCommandWriter(outbox_dir=app_bridge_dirs["outbox"])
    for i, word in enumerate(["alpha", "beta", "gamma"]):
        writer.write(
            app_inbox_dir=app_bridge_dirs["echo_inbox"],
            app_name="echo", command_id=f"cid-{i}",
            prefix="/echo", raw_command=f"/echo {word}", args=[word],
            source=telegram_source,
        )
    # 3건 처리
    for _ in range(3):
        process_one(app_bridge_dirs["echo_inbox"], app_bridge_dirs["outbox"])

    replies = sorted(app_bridge_dirs["outbox"].glob("*_echoreply.json"))
    assert len(replies) == 3
    seen = {}
    for r in replies:
        d = json.loads(r.read_text())
        seen[d["command_id"]] = d["response"]["text"]
    assert seen == {"cid-0": "alpha", "cid-1": "beta", "cid-2": "gamma"}


# ── AppBridge API 모드 ───────────────────────────────

class _FakeChannelManager:
    def get_all_channels(self):
        return [("telegram", "999")]


class _FakeChannelSender:
    pass


@pytest.mark.asyncio
async def test_appbridge_api_dispatch_sync_handler(app_bridge_dirs, telegram_source):
    bridge = AppBridge(
        channel_sender=_FakeChannelSender(),
        channel_manager=_FakeChannelManager(),
        outbox_dir=app_bridge_dirs["outbox"],
    )

    def handler(payload):
        return f"echoed: {' '.join(payload['command']['args'])}"

    bridge.register("api_echo", handler)
    assert bridge.has_handler("api_echo")

    await bridge.dispatch(
        app_name="api_echo", command_id="api-cid-1",
        prefix="/apie", raw_command="/apie hi", args=["hi"],
        source=telegram_source,
    )
    # dispatch 는 create_task 로 fire-and-forget — 잠시 대기
    await asyncio.sleep(0.1)

    files = list(app_bridge_dirs["outbox"].glob("*_appbridge.json"))
    assert len(files) == 1
    d = json.loads(files[0].read_text())
    assert d["command_id"] == "api-cid-1"
    assert d["response"]["text"] == "echoed: hi"


@pytest.mark.asyncio
async def test_appbridge_api_dispatch_async_handler(app_bridge_dirs, telegram_source):
    bridge = AppBridge(
        channel_sender=_FakeChannelSender(),
        channel_manager=_FakeChannelManager(),
        outbox_dir=app_bridge_dirs["outbox"],
    )

    async def handler(payload):
        await asyncio.sleep(0.01)
        return f"async: {payload['command']['prefix']}"

    bridge.register("api_async", handler)
    await bridge.dispatch(
        app_name="api_async", command_id="api-cid-2",
        prefix="/aa", raw_command="/aa", args=[],
        source=telegram_source,
    )
    await asyncio.sleep(0.1)

    files = list(app_bridge_dirs["outbox"].glob("*_appbridge.json"))
    assert len(files) == 1
    d = json.loads(files[0].read_text())
    assert d["response"]["text"] == "async: /aa"


@pytest.mark.asyncio
async def test_appbridge_api_handler_exception_writes_error(app_bridge_dirs, telegram_source):
    bridge = AppBridge(
        channel_sender=_FakeChannelSender(),
        channel_manager=_FakeChannelManager(),
        outbox_dir=app_bridge_dirs["outbox"],
    )

    def handler(payload):
        raise RuntimeError("boom")

    bridge.register("api_fail", handler)
    await bridge.dispatch(
        app_name="api_fail", command_id="api-cid-3",
        prefix="/af", raw_command="/af", args=[],
        source=telegram_source,
    )
    await asyncio.sleep(0.1)

    files = list(app_bridge_dirs["outbox"].glob("*_appbridge.json"))
    assert len(files) == 1
    d = json.loads(files[0].read_text())
    assert "RuntimeError" in d["response"]["text"]


@pytest.mark.asyncio
async def test_appbridge_dispatch_unknown_handler(app_bridge_dirs, telegram_source):
    bridge = AppBridge(
        channel_sender=_FakeChannelSender(),
        channel_manager=_FakeChannelManager(),
        outbox_dir=app_bridge_dirs["outbox"],
    )
    # 핸들러 등록 없이 dispatch
    await bridge.dispatch(
        app_name="missing", command_id="cid-x",
        prefix="/m", raw_command="/m", args=[],
        source=telegram_source,
    )
    await asyncio.sleep(0.05)
    files = list(app_bridge_dirs["outbox"].glob("*_appbridge.json"))
    assert len(files) == 1
    d = json.loads(files[0].read_text())
    assert "등록된 핸들러가 없습니다" in d["response"]["text"]


def test_appbridge_register_non_callable_rejected(app_bridge_dirs):
    bridge = AppBridge(
        channel_sender=_FakeChannelSender(),
        channel_manager=_FakeChannelManager(),
        outbox_dir=app_bridge_dirs["outbox"],
    )
    with pytest.raises(TypeError):
        bridge.register("x", "not callable")


def test_appbridge_unregister(app_bridge_dirs):
    bridge = AppBridge(
        channel_sender=_FakeChannelSender(),
        channel_manager=_FakeChannelManager(),
        outbox_dir=app_bridge_dirs["outbox"],
    )
    bridge.register("a", lambda p: "x")
    assert bridge.has_handler("a")
    bridge.unregister("a")
    assert not bridge.has_handler("a")


# ── OutboxSender 통합: command_id resolution + 지연 응답 ──

def test_outbox_sender_resolves_command_id(app_bridge_dirs, telegram_source):
    """command_id 가 있으면 OutboxSender 가 발송 직전 correlator.resolve 호출."""
    from gsd_orchestrator.outbox_sender import OutboxSender

    cor = AppResponseCorrelator(outbox_dir=app_bridge_dirs["outbox"])
    cor.register(command_id="cidR", app_name="echo", source=telegram_source)

    class _ChMgr:
        async def send_to(self, ct, ci, t, pm=None):
            return True

    sender = OutboxSender(
        channel_manager=_ChMgr(),
        outbox_dir=app_bridge_dirs["outbox"],
        sent_dir=app_bridge_dirs["sent"],
        error_dir=app_bridge_dirs["error"],
        correlator=cor,
    )
    # outbox 에 command_id 포함 응답 작성
    out_file = app_bridge_dirs["outbox"] / "20260504_120000_test_x.json"
    out_file.write_text(json.dumps({
        "id": "x", "command_id": "cidR",
        "source": telegram_source,
        "targets": [{"channel_type": "telegram", "channel_id": "999", "is_origin": True}],
        "retry_count": 0,
        "response": {"text": "ok", "parse_mode": None, "timestamp": ""},
    }))

    asyncio.run(sender._process_outbox())
    # correlator 에서 제거됐는지
    assert not cor.has_pending("cidR")
