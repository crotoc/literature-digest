"""caps/secrets 单测。不建库、不联网、不读 .env。"""

import time

import pytest

from caps.secrets import (
    MASK_WIDTH,
    DecryptFailed,
    InvalidKey,
    decrypt,
    derive_key,
    encrypt,
    generate_key,
    looks_encrypted,
    mask,
    rotate,
)

KEY = generate_key()
OTHER_KEY = generate_key()
PLAIN = "sk-proj-abcdef1234567890"


class TestKeys:
    def test_generate_key_shape(self):
        key = generate_key()
        assert len(key) == 44
        assert key.isascii()

    def test_generated_keys_are_unique(self):
        assert len({generate_key() for _ in range(50)}) == 50

    def test_derive_is_deterministic(self):
        a = derive_key("a-long-enough-passphrase", salt="deploy-salt", purpose="connections")
        b = derive_key("a-long-enough-passphrase", salt="deploy-salt", purpose="connections")
        assert a == b

    def test_derive_differs_by_purpose(self):
        """同一口令派生出的不同用途密钥必须互不相通。"""
        a = derive_key("a-long-enough-passphrase", salt="deploy-salt", purpose="connections")
        b = derive_key("a-long-enough-passphrase", salt="deploy-salt", purpose="sessions")
        assert a != b
        token = encrypt(PLAIN, key=a)
        with pytest.raises(DecryptFailed):
            decrypt(token, key=b)

    def test_derive_differs_by_salt(self):
        a = derive_key("a-long-enough-passphrase", salt="salt-one", purpose="x")
        b = derive_key("a-long-enough-passphrase", salt="salt-two", purpose="x")
        assert a != b

    def test_derived_key_usable(self):
        key = derive_key("a-long-enough-passphrase", salt="deploy-salt", purpose="connections")
        assert decrypt(encrypt(PLAIN, key=key), key=key) == PLAIN

    def test_short_passphrase_rejected(self):
        with pytest.raises(InvalidKey):
            derive_key("tooshort", salt="deploy-salt", purpose="x")

    def test_short_salt_rejected(self):
        with pytest.raises(InvalidKey):
            derive_key("a-long-enough-passphrase", salt="tiny", purpose="x")

    @pytest.mark.parametrize("bad", ["", "not-base64!!", "c2hvcnQ=", "a" * 44])
    def test_malformed_key_rejected(self, bad):
        with pytest.raises(InvalidKey):
            encrypt(PLAIN, key=bad)

    def test_empty_key_list_rejected(self):
        with pytest.raises(InvalidKey):
            encrypt(PLAIN, key=[])

    def test_key_list_with_blank_rejected(self):
        with pytest.raises(InvalidKey):
            encrypt(PLAIN, key=[KEY, ""])


class TestRoundTrip:
    def test_round_trip(self):
        assert decrypt(encrypt(PLAIN, key=KEY), key=KEY) == PLAIN

    def test_ciphertext_is_ascii(self):
        """密文要能直接进数据库文本列。"""
        token = encrypt("密钥里有中文和 emoji 🎉", key=KEY)
        assert token.isascii()

    def test_unicode_plaintext(self):
        plain = "密钥🎉Müller\n多行"
        assert decrypt(encrypt(plain, key=KEY), key=KEY) == plain

    def test_empty_plaintext(self):
        assert decrypt(encrypt("", key=KEY), key=KEY) == ""

    def test_long_plaintext(self):
        plain = "x" * 100_000
        assert decrypt(encrypt(plain, key=KEY), key=KEY) == plain

    def test_same_plaintext_gives_different_ciphertext(self):
        """每次加密有新 IV，所以相同明文的密文不同——不能靠比密文判断相等。"""
        assert encrypt(PLAIN, key=KEY) != encrypt(PLAIN, key=KEY)

    def test_plaintext_not_visible_in_ciphertext(self):
        assert PLAIN not in encrypt(PLAIN, key=KEY)


class TestDecryptFailures:
    def test_wrong_key(self):
        with pytest.raises(DecryptFailed):
            decrypt(encrypt(PLAIN, key=KEY), key=OTHER_KEY)

    def test_tampered_ciphertext(self):
        token = encrypt(PLAIN, key=KEY)
        flipped = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
        with pytest.raises(DecryptFailed):
            decrypt(flipped, key=KEY)

    @pytest.mark.parametrize("bad", ["", "nope", "gAAAAA", "a.b.c"])
    def test_garbage_ciphertext(self, bad):
        with pytest.raises(DecryptFailed):
            decrypt(bad, key=KEY)

    def test_failure_reason_not_distinguishable(self):
        """错密钥、被篡改、超期抛的是同一个异常类型——不给攻击者试探的信号。"""

        def kind_of(call) -> type:
            try:
                call()
            except Exception as error:  # noqa: BLE001 — 这里就是要看落到哪个类型
                return type(error)
            raise AssertionError("应当失败但没失败")

        token = encrypt(PLAIN, key=KEY)
        flipped = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
        kinds = {
            kind_of(lambda: decrypt(token, key=OTHER_KEY)),
            kind_of(lambda: decrypt(flipped, key=KEY)),
            kind_of(lambda: decrypt("gAAAAAtruncated", key=KEY)),
        }
        assert kinds == {DecryptFailed}

    def test_plaintext_never_in_exception_message(self):
        """异常消息会进日志，绝不能带明文或密钥。"""
        try:
            decrypt(encrypt(PLAIN, key=KEY), key=OTHER_KEY)
        except DecryptFailed as error:
            text = f"{error}{error.args}"
            assert PLAIN not in text
            assert KEY not in text
            assert OTHER_KEY not in text


class TestMaxAge:
    def test_fresh_token_passes(self):
        token = encrypt(PLAIN, key=KEY)
        assert decrypt(token, key=KEY, max_age_seconds=3600) == PLAIN

    def test_expired_token_rejected(self):
        # Fernet 时间戳是秒粒度，睡过两个整秒边界才稳定过期
        token = encrypt(PLAIN, key=KEY)
        time.sleep(2.1)
        with pytest.raises(DecryptFailed):
            decrypt(token, key=KEY, max_age_seconds=1)

    def test_no_max_age_means_no_expiry(self):
        """久期凭据不该因为存得久而失效。"""
        token = encrypt(PLAIN, key=KEY)
        assert decrypt(token, key=KEY) == PLAIN

    def test_nonpositive_max_age_rejected(self):
        with pytest.raises(ValueError):
            decrypt(encrypt(PLAIN, key=KEY), key=KEY, max_age_seconds=0)


class TestKeyRotation:
    def test_last_key_encrypts(self):
        """本项目约定：列表里最后一个是当前密钥（和 authn 一致，
        与 cryptography 原生 MultiFernet 的顺序相反，本模块内部反转）。"""
        token = encrypt(PLAIN, key=[OTHER_KEY, KEY])
        assert decrypt(token, key=KEY) == PLAIN

    def test_old_key_still_decrypts(self):
        old_token = encrypt(PLAIN, key=OTHER_KEY)
        assert decrypt(old_token, key=[OTHER_KEY, KEY]) == PLAIN

    def test_dropped_key_no_longer_decrypts(self):
        old_token = encrypt(PLAIN, key=OTHER_KEY)
        with pytest.raises(DecryptFailed):
            decrypt(old_token, key=[KEY])

    def test_single_string_equals_single_item_list(self):
        assert decrypt(encrypt(PLAIN, key=[KEY]), key=KEY) == PLAIN

    def test_rotate_reencrypts_to_current_key(self):
        old_token = encrypt(PLAIN, key=OTHER_KEY)
        fresh = rotate(old_token, key=[OTHER_KEY, KEY])
        assert fresh != old_token
        assert decrypt(fresh, key=KEY) == PLAIN

    def test_rotated_token_survives_dropping_old_key(self):
        """刷完一遍就能把老密钥从列表里删掉——这是 rotate 存在的理由。"""
        old_token = encrypt(PLAIN, key=OTHER_KEY)
        fresh = rotate(old_token, key=[OTHER_KEY, KEY])
        assert decrypt(fresh, key=[KEY]) == PLAIN

    def test_rotate_garbage_fails(self):
        with pytest.raises(DecryptFailed):
            rotate("nope", key=[OTHER_KEY, KEY])


class TestLooksEncrypted:
    def test_ciphertext_recognized(self):
        assert looks_encrypted(encrypt(PLAIN, key=KEY)) is True

    def test_plaintext_not_recognized(self):
        for plain in ["sk-proj-abc", "", "hello", "123456"]:
            assert looks_encrypted(plain) is False

    def test_non_string_is_false_not_error(self):
        assert looks_encrypted(None) is False
        assert looks_encrypted(123) is False


class TestMask:
    def test_shape(self):
        result = mask("sk-proj-abcdef1234")
        assert result.display == "•" * MASK_WIDTH + "1234"
        assert result.last == "1234"

    def test_str_is_display(self):
        assert str(mask("sk-proj-abcdef1234")) == "••••••1234"

    def test_short_value_fully_masked(self):
        """长度 <= keep 时一个字符都不能露。"""
        for short in ["a", "ab", "abc", "abcd"]:
            result = mask(short, keep=4)
            assert result.last == ""
            assert short not in result.display

    def test_bullet_count_fixed_regardless_of_length(self):
        """圆点数量不反映真实长度——长度本身也是信息。"""
        short = mask("abcde12345")
        long = mask("x" * 500 + "12345"[:4] + "1234")
        assert short.display.count("•") == long.display.count("•") == MASK_WIDTH

    def test_empty(self):
        result = mask("")
        assert result.display == ""
        assert result.last == ""

    def test_keep_zero_masks_everything(self):
        result = mask("sk-proj-abcdef", keep=0)
        assert result.last == ""
        assert result.display == "•" * MASK_WIDTH

    def test_custom_keep(self):
        assert mask("abcdefghij", keep=2).last == "ij"

    def test_negative_keep_rejected(self):
        with pytest.raises(ValueError):
            mask("abcdef", keep=-1)

    def test_non_string_rejected(self):
        """悄悄把 None 显示成空串会掩盖掉"调用方取错字段"这种 bug。"""
        with pytest.raises(TypeError):
            mask(None)

    def test_reveals_at_most_keep_characters(self):
        value = "supersecretkey9876"
        result = mask(value, keep=4)
        assert len(result.last) <= 4
        assert value[:-4] not in result.display
