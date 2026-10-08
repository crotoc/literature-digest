"""caps/authn 单测。不建库、不联网。

密码用的 argon2 参数刻意调到最低（`FAST`），否则每次 hash 要 64 MiB + 3 轮，
几十条测试会把跑测时间拖到分钟级。生产参数由调用方传 DEFAULT_PASSWORD_PARAMS。
"""

import time

import pytest

from caps.authn import (
    DEFAULT_PASSWORD_PARAMS,
    LOOKUP_PREFIX_CHARS,
    HashingFailed,
    MalformedHash,
    PasswordParams,
    TokenExpired,
    TokenInvalid,
    check_password,
    hash_password,
    hash_token,
    lookup_prefix_of,
    new_token,
    sign,
    unsign,
    verify_token,
)

FAST = PasswordParams(time_cost=1, memory_cost=8, parallelism=1)
SECRET = "unit-test-secret-not-a-real-key"


# ── 密码 ───────────────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_is_argon2id(self):
        assert hash_password("correct horse", params=FAST).startswith("$argon2id$")

    def test_correct_password_accepted(self):
        stored = hash_password("correct horse", params=FAST)
        assert check_password(stored, "correct horse", params=FAST).ok is True

    def test_wrong_password_rejected_without_raising(self):
        stored = hash_password("correct horse", params=FAST)
        assert check_password(stored, "wrong horse", params=FAST).ok is False

    def test_result_is_truthy(self):
        stored = hash_password("pw", params=FAST)
        assert check_password(stored, "pw", params=FAST)
        assert not check_password(stored, "nope", params=FAST)

    def test_salt_makes_hashes_differ(self):
        a = hash_password("same", params=FAST)
        b = hash_password("same", params=FAST)
        assert a != b
        assert check_password(a, "same", params=FAST).ok
        assert check_password(b, "same", params=FAST).ok

    def test_hash_embeds_parameters(self):
        """参数写在哈希串里，不需要另存字段——换参数时旧哈希仍可校验。"""
        stored = hash_password("pw", params=FAST)
        assert "m=8,t=1,p=1" in stored

    def test_unicode_password(self):
        stored = hash_password("密码🎉Müller", params=FAST)
        assert check_password(stored, "密码🎉Müller", params=FAST).ok
        assert not check_password(stored, "密码🎉muller", params=FAST).ok

    def test_long_password(self):
        plain = "x" * 4096
        assert check_password(hash_password(plain, params=FAST), plain, params=FAST).ok

    def test_empty_password_is_not_rejected_here(self):
        """长度策略归 features/accounts_auth，这一层不管。"""
        stored = hash_password("", params=FAST)
        assert check_password(stored, "", params=FAST).ok
        assert not check_password(stored, "x", params=FAST).ok


class TestNeedsRehash:
    def test_same_params_no_rehash(self):
        stored = hash_password("pw", params=FAST)
        assert check_password(stored, "pw", params=FAST).needs_rehash is False

    def test_weaker_old_hash_wants_rehash(self):
        """用弱参数存的旧哈希，在目标参数变强后应当被标记为该升级。"""
        stored = hash_password("pw", params=FAST)
        stronger = PasswordParams(time_cost=2, memory_cost=16, parallelism=1)
        result = check_password(stored, "pw", params=stronger)
        assert result.ok is True
        assert result.needs_rehash is True

    def test_rehash_only_reported_on_success(self):
        stored = hash_password("pw", params=FAST)
        stronger = PasswordParams(time_cost=2, memory_cost=16, parallelism=1)
        assert check_password(stored, "wrong", params=stronger).needs_rehash is False

    def test_default_params_are_the_recommended_ones(self):
        assert DEFAULT_PASSWORD_PARAMS.memory_cost == 65536
        assert DEFAULT_PASSWORD_PARAMS.time_cost == 3


class TestMalformedHash:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "plaintext-password",
            "$2b$12$abcdefghijklmnopqrstuv",
            "$argon2id$truncated",
            "not even close",
            # 非 ASCII 的坏数据：argon2-cffi 在编码阶段就炸，必须在本模块
            # 收成 MalformedHash，否则一行坏数据会变成一个 500
            "库里存的是明文",
            "$argon2id$v=19$m=8,t=1,p=1$盐$哈希",
        ],
    )
    def test_raises_not_returns_false(self, bad):
        """坏数据不能伪装成'密码不对'——那会让一次数据事故再也没人发现。"""
        with pytest.raises(MalformedHash):
            check_password(bad, "pw", params=FAST)

    def test_malformed_is_distinct_from_wrong_password(self):
        stored = hash_password("pw", params=FAST)
        assert check_password(stored, "wrong", params=FAST).ok is False
        with pytest.raises(MalformedHash):
            check_password("garbage", "wrong", params=FAST)

    @pytest.mark.parametrize("bad", [None, 123, b"", object()])
    def test_non_string_hash_raises_malformed(self, bad):
        with pytest.raises(MalformedHash):
            check_password(bad, "pw", params=FAST)

    def test_impossible_params_raise_hashing_failed(self):
        with pytest.raises((HashingFailed, ValueError)):
            hash_password("pw", params=PasswordParams(time_cost=0, memory_cost=0, parallelism=0))


# ── 签名 ───────────────────────────────────────────────────────────────────

class TestSigning:
    def test_round_trip(self):
        token = sign("session-abc", secret=SECRET)
        assert unsign(token, secret=SECRET, max_age_seconds=60) == "session-abc"

    def test_payload_is_not_secret(self):
        """签名只保证没被改过，**不保证保密**——不拿密钥也能把 payload 解回来。"""
        import base64

        token = sign("session-abc", secret=SECRET)
        body = token.split(".")[0]
        decoded = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        assert b"session-abc" in decoded

    def test_token_is_ascii_and_header_safe(self):
        """token 要能塞进 Cookie / HTTP 头，所以哪怕 payload 是中文也必须是 ASCII。"""
        token = sign("会话-深度学习", secret=SECRET)
        assert token.isascii()
        assert not any(c in token for c in " ;,\n\r")

    def test_tampered_payload_rejected(self):
        # payload 现在是 base64，不能靠替换原文来改它（那会变成空操作、
        # 让这条测试什么都测不到）。翻 payload 段的第一个字节，签名段原样留着。
        token = sign("session-abc", secret=SECRET)
        head, rest = token.split(".", 1)
        flipped = ("A" if head[0] != "A" else "B") + head[1:]
        assert flipped != head
        with pytest.raises(TokenInvalid):
            unsign(f"{flipped}.{rest}", secret=SECRET, max_age_seconds=60)

    def test_tampered_signature_rejected(self):
        token = sign("session-abc", secret=SECRET)
        body, _, sig = token.rpartition(".")
        forged = ("A" if sig[0] != "A" else "B") + sig[1:]
        with pytest.raises(TokenInvalid):
            unsign(f"{body}.{forged}", secret=SECRET, max_age_seconds=60)

    def test_wrong_secret_rejected(self):
        token = sign("x", secret=SECRET)
        with pytest.raises(TokenInvalid):
            unsign(token, secret="another-secret", max_age_seconds=60)

    def test_garbage_rejected(self):
        for bad in ["", "nope", "a.b.c", "....."]:
            with pytest.raises(TokenInvalid):
                unsign(bad, secret=SECRET, max_age_seconds=60)

    def test_unicode_payload(self):
        token = sign("会话-深度学习", secret=SECRET)
        assert unsign(token, secret=SECRET, max_age_seconds=60) == "会话-深度学习"

    def test_nonpositive_max_age_rejected(self):
        token = sign("x", secret=SECRET)
        with pytest.raises(ValueError):
            unsign(token, secret=SECRET, max_age_seconds=0)

    def test_empty_secret_rejected(self):
        with pytest.raises(ValueError):
            sign("x", secret="")
        with pytest.raises(ValueError):
            sign("x", secret=[])
        with pytest.raises(ValueError):
            sign("x", secret=["ok", ""])


class TestExpiry:
    def test_expired_raises_token_expired(self):
        # itsdangerous 的时间戳是秒粒度、判定是 age > max_age，所以必须睡过
        # 两个整秒边界才稳定过期；睡 1.1 秒算出来的 age 可能还是 1，会 flaky。
        token = sign("x", secret=SECRET)
        time.sleep(2.1)
        with pytest.raises(TokenExpired):
            unsign(token, secret=SECRET, max_age_seconds=1)

    def test_token_expired_is_a_token_invalid(self):
        """调用方只想拒绝时可以只捕 TokenInvalid，不必分别处理两种。"""
        assert issubclass(TokenExpired, TokenInvalid)

    def test_not_yet_expired_passes(self):
        token = sign("x", secret=SECRET)
        assert unsign(token, secret=SECRET, max_age_seconds=3600) == "x"


class TestPurposeNamespace:
    def test_cross_purpose_token_rejected(self):
        """会话 cookie 拿去当密码重置令牌必须被拒。"""
        token = sign("abc", secret=SECRET, purpose="session")
        with pytest.raises(TokenInvalid):
            unsign(token, secret=SECRET, max_age_seconds=60, purpose="password_reset")

    def test_same_purpose_passes(self):
        token = sign("abc", secret=SECRET, purpose="password_reset")
        assert unsign(token, secret=SECRET, max_age_seconds=60, purpose="password_reset") == "abc"


class TestKeyRotation:
    def test_last_key_signs(self):
        token = sign("x", secret=["old", "new"])
        assert unsign(token, secret="new", max_age_seconds=60) == "x"

    def test_all_keys_verify(self):
        old_token = sign("x", secret="old")
        assert unsign(old_token, secret=["old", "new"], max_age_seconds=60) == "x"

    def test_dropped_key_no_longer_verifies(self):
        old_token = sign("x", secret="old")
        with pytest.raises(TokenInvalid):
            unsign(old_token, secret=["new"], max_age_seconds=60)

    def test_single_string_equals_single_item_list(self):
        assert unsign(sign("x", secret=[SECRET]), secret=SECRET, max_age_seconds=60) == "x"


# ── 随机令牌 ───────────────────────────────────────────────────────────────

class TestNewToken:
    def test_fields_consistent(self):
        token = new_token(prefix="ld")
        assert token.plaintext.startswith("ld_")
        assert token.lookup_prefix == token.plaintext[:LOOKUP_PREFIX_CHARS]
        assert token.hashed == hash_token(token.plaintext)

    def test_verify_round_trip(self):
        token = new_token(prefix="ld")
        assert verify_token(token.plaintext, token.hashed) is True

    def test_wrong_token_rejected(self):
        a, b = new_token(prefix="ld"), new_token(prefix="ld")
        assert verify_token(a.plaintext, b.hashed) is False

    def test_tokens_are_unique(self):
        assert len({new_token().plaintext for _ in range(200)}) == 200

    def test_no_prefix(self):
        token = new_token()
        assert "_" not in token.plaintext[:1]
        assert len(token.lookup_prefix) == LOOKUP_PREFIX_CHARS

    def test_lookup_prefix_recoverable_from_presented_token(self):
        """扩展出示令牌时，服务端靠这个前缀 O(1) 定位，而不是扫全表比哈希。"""
        token = new_token(prefix="ld")
        assert lookup_prefix_of(token.plaintext) == token.lookup_prefix

    def test_entropy_floor_enforced(self):
        with pytest.raises(ValueError):
            new_token(nbytes=8)

    def test_bad_prefix_rejected(self):
        for bad in ["ld_", "l d", "ld-", "ld/x"]:
            with pytest.raises(ValueError):
                new_token(prefix=bad)

    def test_repr_hides_plaintext(self):
        """明文绝不该进日志或 traceback。"""
        token = new_token(prefix="ld")
        text = repr(token)
        assert token.plaintext not in text
        assert "已隐藏" in text
        assert token.lookup_prefix in text


class TestTokenHashing:
    def test_sha256_hex(self):
        hashed = hash_token("ld_abc")
        assert len(hashed) == 64
        assert all(c in "0123456789abcdef" for c in hashed)

    def test_deterministic(self):
        assert hash_token("ld_abc") == hash_token("ld_abc")

    def test_differs_by_input(self):
        assert hash_token("ld_abc") != hash_token("ld_abd")

    def test_not_argon2(self):
        """令牌刻意**不用**慢哈希：256 位熵没有爆破面，argon2 只会让扩展
        每次调 API 都付 64 MiB + 3 轮。"""
        assert not hash_token("ld_abc").startswith("$argon2")

    def test_verify_against_garbage_is_false_not_error(self):
        assert verify_token("ld_abc", "") is False
        assert verify_token("ld_abc", "nope") is False

    def test_unicode_token_hashable(self):
        assert len(hash_token("令牌")) == 64
