"""Tests for tg_proxy.models."""

from tg_proxy.models import (
    BotCreateItem,
    BotCreatePayload,
    BotDeletePayload,
    BotInfoPayload,
    BotListPayload,
    BotSendFilePayload,
    BotSendPayload,
    BotTokenPayload,
    ChatDownloadPayload,
    ChatListPayload,
    ChatReadPayload,
    ChatSendFilePayload,
    ChatSendPayload,
    Output,
    OutputMeta,
    UpdatesPayload,
    WebhookDelPayload,
    WebhookGetPayload,
    WebhookSetPayload,
)


class TestBotListPayload:
    def test_default(self):
        p = BotListPayload()
        assert p.filter == "all"

    def test_custom(self):
        p = BotListPayload(filter="mine")
        assert p.filter == "mine"


class TestBotInfoPayload:
    def test_empty(self):
        p = BotInfoPayload()
        assert p.bots == []

    def test_with_bots(self):
        p = BotInfoPayload(bots=["@bot1", "@bot2"])
        assert len(p.bots) == 2
        assert p.bots[0] == "@bot1"


class TestBotTokenPayload:
    def test_single(self):
        p = BotTokenPayload(bots=["@s25"])
        assert p.bots == ["@s25"]

    def test_multiple(self):
        p = BotTokenPayload(bots=["@a", "@b", "@c"])
        assert len(p.bots) == 3


class TestBotCreatePayload:
    def test_single(self):
        p = BotCreatePayload(bots=[BotCreateItem(name="Test", username="test_bot")])
        assert len(p.bots) == 1
        assert p.bots[0].name == "Test"
        assert p.bots[0].username == "test_bot"

    def test_multiple(self):
        items = [
            BotCreateItem(name="A", username="a_bot"),
            BotCreateItem(name="B", username="b_bot"),
        ]
        p = BotCreatePayload(bots=items)
        assert len(p.bots) == 2


class TestBotDeletePayload:
    def test_multiple(self):
        p = BotDeletePayload(bots=["@dead1", "@dead2"])
        assert len(p.bots) == 2
        assert p.bots[1] == "@dead2"


class TestBotSendPayload:
    def test_minimal(self):
        p = BotSendPayload(bot="@mybot", message="Hello")
        assert p.bot == "@mybot"
        assert p.message == "Hello"
        assert p.parse_mode is None

    def test_html(self):
        p = BotSendPayload(bot="@mybot", message="<b>Hi</b>", parse_mode="HTML")
        assert p.parse_mode == "HTML"


class TestBotSendFilePayload:
    def test_full(self):
        p = BotSendFilePayload(
            bot="@mybot", message="Docs", files=["/a.pdf", "/b.pdf"]
        )
        assert len(p.files) == 2
        assert p.files[0] == "/a.pdf"


class TestChatListPayload:
    def test_default(self):
        p = ChatListPayload()
        assert p.limit == 30
        assert p.type is None

    def test_filtered(self):
        p = ChatListPayload(type="user", limit=10)
        assert p.type == "user"
        assert p.limit == 10


class TestChatReadPayload:
    def test_valid(self):
        p = ChatReadPayload(chat=93372553, limit=5, search="token")
        assert p.chat == 93372553
        assert p.search == "token"


class TestChatSendPayload:
    def test_valid(self):
        p = ChatSendPayload(to="@KpihX", message="Hello")
        assert p.to == "@KpihX"
        assert p.message == "Hello"


class TestChatSendFilePayload:
    def test_valid(self):
        p = ChatSendFilePayload(to="@KpihX", message="Files", files=["/a.pdf"])
        assert len(p.files) == 1


class TestChatDownloadPayload:
    def test_valid(self):
        p = ChatDownloadPayload(chat="@chat", message_ids=[42, 43], out="/tmp/")
        assert p.message_ids == [42, 43]


class TestUpdatesPayload:
    def test_valid(self):
        p = UpdatesPayload(bot="@mybot", limit=5)
        assert p.bot == "@mybot"
        assert p.limit == 5


class TestWebhookGetPayload:
    def test_valid(self):
        p = WebhookGetPayload(bot="@bot")
        assert p.bot == "@bot"


class TestWebhookSetPayload:
    def test_valid(self):
        p = WebhookSetPayload(bot="@bot", url="https://example.com/w")
        assert p.url == "https://example.com/w"


class TestWebhookDelPayload:
    def test_default(self):
        p = WebhookDelPayload(bot="@bot")
        assert p.drop_pending is False

    def test_drop(self):
        p = WebhookDelPayload(bot="@bot", drop_pending=True)
        assert p.drop_pending is True


class TestOutput:
    def test_meta_default(self):
        o = Output(data={"ok": True})
        assert o.meta.status == "ok"
        assert o.meta.edited is False

    def test_hitl_meta(self):
        meta = OutputMeta(
            status="approved", comment="LGTM", edited=True,
            original="Hello", edited_to="Hello!",
        )
        o = Output(meta=meta, data={"message_id": 42})
        assert o.meta.comment == "LGTM"
        assert o.meta.edited_to == "Hello!"
        assert o.data["message_id"] == 42
