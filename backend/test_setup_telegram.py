"""setup_telegram — the guided Telegram setup the owner runs.

Driven here with a fake Telegram and a scripted keyboard. The properties that matter:
the token is never printed, nothing is saved before a test message is delivered, the
rest of backend/.env (the MSSQL login) survives byte for byte, and flyctl gets the
token on stdin - never as an argument.
"""
import argparse

import pytest

import setup_telegram as st

TOKEN = "123456789:AAH" + "x" * 32


@pytest.fixture(autouse=True)
def _no_real_trail(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "TRAIL_FILE", tmp_path / "trail.log")      # never the real backend/logs file
GROUP = {"id": -1001234567890, "title": "Oshinei Alerts", "type": "supergroup"}
ME = {"id": 99, "first_name": "Wayla", "type": "private"}


class FakeUI:
    def __init__(self, answers=(), secrets=()):
        self.lines, self.answers, self.secrets, self.t = [], list(answers), list(secrets), 0.0

    def out(self, s=""):
        self.lines.append(str(s))

    def ask(self, prompt):
        self.lines.append(prompt)
        return self.answers.pop(0)

    def secret(self, prompt):
        return self.secrets.pop(0)

    def sleep(self, s):
        self.t += s

    def clock(self):
        self.t += 1
        return self.t

    @property
    def text(self):
        return "\n".join(self.lines)


class FakeTelegram:
    def __init__(self, updates=(), deliver=True, valid=(TOKEN,)):
        self.batches, self.deliver, self.valid, self.calls = [list(updates), []], deliver, valid, []

    def __call__(self, token, method, params=None, timeout=40):
        self.calls.append((method, params))
        if token not in self.valid:
            return {"ok": False, "error_code": 401, "description": "Unauthorized"}
        if method == "getMe":
            return {"ok": True, "result": {"username": "oshinei_alert_bot", "first_name": "Oshinei Alerts"}}
        if method == "getUpdates":
            return {"ok": True, "result": self.batches.pop(0) if self.batches else []}
        if method == "sendMessage":
            return {"ok": True} if self.deliver else {"ok": False, "description": "Forbidden: bot was kicked"}
        raise AssertionError(method)


def args(**kw):
    return argparse.Namespace(**{"test": False, "chat_only": False, "fly_only": False, **kw})


def added_to_group(update_id=1):
    return {"update_id": update_id, "my_chat_member": {"chat": GROUP}}


def test_full_walk_through_saves_only_after_delivery_and_never_prints_the_token(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"OSH_SVR=10.0.0.5\r\nOSH_PWD=s3cret=with=equals\r\n# a comment\r\n")
    ui, tg, pushed = FakeUI(answers=["n"], secrets=[TOKEN]), FakeTelegram([added_to_group()]), []
    rc = st.run(args(), ui, tg, env, push=lambda t, c: pushed.append((t, c)) or (True, ""), busy=lambda: "")
    assert rc == 0 and pushed == []                                  # answered "n" to the live app
    assert TOKEN not in ui.text and "1234...xxxx" in ui.text         # masked only
    raw = env.read_bytes()
    assert raw.startswith(b"OSH_SVR=10.0.0.5\r\nOSH_PWD=s3cret=with=equals\r\n# a comment\r\n")   # untouched, CRLF kept
    got = st.read_env(env)
    assert got["OSHINEI_TG_TOKEN"] == TOKEN and got["OSHINEI_TG_CHAT_ID"] == "-1001234567890"
    assert not (tmp_path / ".env.tmp").exists()


def test_nothing_is_saved_when_the_test_message_is_not_delivered(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OSH_SVR=x\n", encoding="utf-8")
    ui = FakeUI(secrets=[TOKEN])
    assert st.run(args(), ui, FakeTelegram([added_to_group()], deliver=False), env) == 1
    assert env.read_text(encoding="utf-8") == "OSH_SVR=x\n" and "nothing was saved" in ui.text


def test_a_mistyped_token_is_refused_before_any_network_call_and_a_wrong_one_by_telegram(tmp_path):
    ui, tg = FakeUI(answers=["n"], secrets=["hello", "987654321:" + "y" * 35, TOKEN]), FakeTelegram([added_to_group()])
    assert st.run(args(), ui, tg, tmp_path / ".env", busy=lambda: "") == 0
    assert "does not look like a bot token" in ui.text and "Telegram refused it: Unauthorized" in ui.text
    assert [m for m, _ in tg.calls].count("getMe") == 3             # wrong one, right one, then the username lookup


def test_with_two_chats_the_owner_picks(tmp_path):
    ups = [added_to_group(1), {"update_id": 2, "message": {"chat": ME, "text": "/start"}}]
    ui = FakeUI(answers=["2", "n"], secrets=[TOKEN])
    assert st.run(args(), ui, FakeTelegram(ups), tmp_path / ".env", busy=lambda: "") == 0
    assert st.read_env(tmp_path / ".env")["OSHINEI_TG_CHAT_ID"] == "99"
    assert "Oshinei Alerts  [supergroup]" in ui.text and "Wayla  [private]" in ui.text


def test_nobody_wrote_to_the_bot(tmp_path):
    ui = FakeUI(secrets=[TOKEN])
    assert st.run(args(), ui, FakeTelegram([]), tmp_path / ".env") == 1
    assert "nothing arrived" in ui.text and not (tmp_path / ".env").exists()


def test_a_saved_bot_is_offered_back_and_updating_replaces_the_lines_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"OSHINEI_TG_TOKEN={TOKEN}\nOSHINEI_TG_CHAT_ID=1\nOSH_SVR=x\n", encoding="utf-8")
    ui = FakeUI(answers=["", "n"])                                   # Enter = keep the bot; no to Fly
    assert st.run(args(), ui, FakeTelegram([added_to_group()]), env, busy=lambda: "") == 0
    assert env.read_text(encoding="utf-8") == f"OSHINEI_TG_TOKEN={TOKEN}\nOSHINEI_TG_CHAT_ID=-1001234567890\nOSH_SVR=x\n"


def test_live_app_gets_the_token_on_stdin_never_as_an_argument():
    seen = {}

    def fake_run(cmd, input=None, **kw):
        seen.update(cmd=cmd, stdin=input)
        return argparse.Namespace(returncode=0, stdout=b"Secrets are staged\n", stderr=b"")

    ok, last = st.push_to_fly(TOKEN, "-100", run=fake_run)
    assert ok and last == "Secrets are staged"
    assert TOKEN not in " ".join(seen["cmd"]) and seen["cmd"][:3] == ["flyctl", "secrets", "import"]
    assert seen["stdin"] == f"OSHINEI_TG_TOKEN={TOKEN}\nOSHINEI_TG_CHAT_ID=-100\n".encode()


def test_live_app_is_not_restarted_while_a_sync_is_uploading(tmp_path):
    ui, pushed = FakeUI(answers=["y"], secrets=[TOKEN]), []
    st.run(args(), ui, FakeTelegram([added_to_group()]), tmp_path / ".env",
           push=lambda t, c: pushed.append(1) or (True, ""), busy=lambda: "Oshinei Sync Live (Fly)")
    assert pushed == [] and "'Oshinei Sync Live (Fly)' is running" in ui.text


def test_test_and_fly_only_need_a_finished_setup(tmp_path):
    ui = FakeUI()
    assert st.run(args(test=True), ui, FakeTelegram(), tmp_path / ".env") == 1 and "not set up yet" in ui.text
    env = tmp_path / ".env"
    st.write_env({"OSHINEI_TG_TOKEN": TOKEN, "OSHINEI_TG_CHAT_ID": "5"}, env)
    assert st.run(args(test=True), FakeUI(), FakeTelegram(), env) == 0


def test_the_trail_says_where_it_stopped_and_never_holds_the_token(tmp_path):
    # 2026-09-21: a first run saved nothing and left nothing to read
    trail = []
    ui = FakeUI(secrets=["987654321:" + "y" * 35] * 5)               # a wrong token, five times
    assert st.run(args(), ui, FakeTelegram([]), tmp_path / ".env", trail=trail.append) == 1
    assert trail[0] == "--- started (setup)" and trail[-1] == "--- ended, exit 1 - NOT complete"
    assert sum("Telegram refused it: Unauthorized" in ln for ln in trail) == 5

    trail = []
    ui = FakeUI(answers=["n"], secrets=[TOKEN])
    assert st.run(args(), ui, FakeTelegram([added_to_group()]), tmp_path / ".env", trail=trail.append, busy=lambda: "") == 0
    joined = "\n".join(st.scrub(ln) for ln in trail)
    assert "found: Oshinei Alerts" in joined and "ok - delivered" in joined and trail[-1] == "--- ended, exit 0"
    assert TOKEN not in joined and TOKEN[:4] + "..." not in joined     # not even the masked form
    assert st.scrub(f"bot {st.mask(TOKEN)} / {TOKEN}") == "bot <bot> / <token>"


def test_the_trail_file_is_written_scrubbed(tmp_path):
    f = tmp_path / "logs" / "setup_telegram.log"
    st.file_trail(f"x Telegram refused {TOKEN}", f)
    assert f.read_text(encoding="utf-8").strip().endswith("x Telegram refused <token>")


def test_the_bat_is_ascii_and_writes_no_log():
    from pathlib import Path
    bat = (Path(__file__).resolve().parents[1] / "Setup Telegram.bat").read_bytes()
    assert all(b < 128 for b in bat) and b"_tee.ps1" not in bat.replace(b"REM", b"")     # a secret passes through this window
