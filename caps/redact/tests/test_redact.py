"""caps/redact 单测。纯函数，不建库、不联网。"""

import re

import pytest

from caps.redact import (
    CYCLE,
    PLACEHOLDER,
    TOO_DEEP,
    RedactionPolicy,
    is_sensitive_key,
    is_url_key,
    normalize_key,
    redact_mapping,
    redact_text,
    redact_url,
    with_known_values,
)

# ── 键名 ───────────────────────────────────────────────────────────────────

class TestKeyNames:
    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            ("X-Api-Key", "xapikey"),
            ("api_key", "apikey"),
            ("API KEY", "apikey"),
            ("Authorization", "authorization"),
            ("set-cookie", "setcookie"),
        ],
    )
    def test_normalize(self, raw, normalized):
        assert normalize_key(raw) == normalized

    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "new_password",
            "api_key",
            "X-Api-Key",
            "Authorization",
            "proxy-authorization",
            "Cookie",
            "set-cookie",
            "bot_token",
            "access_token",
            "refresh_token",
            "session_id",
            "client_secret",
            "private_key",
            "credentials",
            "csrf_token",
            "fernet_key",
        ],
    )
    def test_sensitive(self, key):
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "author",
            "authors",
            "first_author",
            "author_list",
            "corresponding_author",
            "title",
            "abstract",
            "doi",
            "pmid",
            "year",
            "publication",
            "folder_id",
            "work_id",
            "digest",
            "sha256",
            "page_count",
        ],
    )
    def test_not_sensitive(self, key):
        assert is_sensitive_key(key) is False

    def test_author_fields_survive(self):
        """本项目满眼都是 author/authors。如果按子串匹配 "auth"，
        每一个作者名都会被抹掉——这是本模块最容易犯的错。"""
        data = {"author": "Smith J", "authors": ["Smith J", "Müller K"], "first_author": "Smith"}
        assert redact_mapping(data) == data


# ── 文本：模式兜底 ─────────────────────────────────────────────────────────

class TestTextPatterns:
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-proj-AbCdEf1234567890xyz",
            "sk-ant-api03-AbCdEf1234567890xyz",
            "ghp_AbCdEf1234567890xyzAbCdEf12",
            "github_pat_AbCdEf1234567890xyzAbCdEf12",
            "123456789:AAEhBOweik6ad9r_QXzBuUkA8CoGZ4AbCdE",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.AbCdEf1234567890xyz",
            "gAAAAABqx-yimM7iwD3HeGsPaKA_KFzrMg5IX2xw83Lz",
        ],
    )
    def test_known_shapes_redacted(self, secret):
        out = redact_text(f"调用失败了，用的是 {secret} 这个凭据")
        assert secret not in out
        assert PLACEHOLDER in out

    def test_bearer_header(self):
        out = redact_text("Authorization: Bearer abcdef1234567890xyz")
        assert "abcdef1234567890xyz" not in out

    def test_basic_header(self):
        out = redact_text("Authorization: Basic dXNlcjpwYXNzd29yZA==")
        assert "dXNlcjpwYXNzd29yZA" not in out

    def test_argon2_hash(self):
        out = redact_text("stored=$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA")
        assert "aGFzaA" not in out

    @pytest.mark.parametrize(
        "line",
        [
            "password=hunter2secret",
            "password: hunter2secret",
            'api_key="hunter2secret"',
            "token=hunter2secret",
            "access_token = hunter2secret",
            "secret=hunter2secret",
        ],
    )
    def test_key_value_pairs(self, line):
        out = redact_text(line)
        assert "hunter2secret" not in out

    def test_key_value_keeps_key_name(self):
        """保留键名比整段消失更好排查。"""
        out = redact_text("password=hunter2secret")
        assert out.startswith("password=")
        assert PLACEHOLDER in out

    def test_untouched_text_passes_through(self):
        text = "导入了 42 篇文献，其中 3 篇疑似重复"
        assert redact_text(text) == text

    def test_empty_and_non_string(self):
        assert redact_text("") == ""
        assert redact_text(None) is None


class TestLongHexIsNotRedacted:
    def test_sha256_digest_survives(self):
        """刻意不脱敏"长十六进制串"：blobstore 的 sha256 摘要正是 64 位十六进制，
        而它是排查附件问题时最关键的线索。抹掉它等于让日志失去用处。"""
        digest = "ba1ad02a26f0ad06f8fa9199c1f4527011101fb7e7db70f251bcf747250b0319"
        out = redact_text(f"blob 落位 sha256/ba/1a/{digest}")
        assert digest in out

    def test_uuid_survives(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        assert uid in redact_text(f"request_id={uid}")

    def test_request_id_survives(self):
        assert "a3f9c1d2e8b40517" in redact_text("request a3f9c1d2e8b40517 失败")


# ── 文本：已知值替换 ───────────────────────────────────────────────────────

class TestKnownValues:
    def test_exact_value_removed(self):
        policy = with_known_values(["my-weird-credential-format"])
        out = redact_text("登录用的是 my-weird-credential-format 这串", policy=policy)
        assert "my-weird-credential-format" not in out

    def test_catches_what_patterns_miss(self):
        """已知值是可靠的那一道——模式匹配抓不到的新格式，它能抓到。"""
        weird = "XyZzY-institutional-pass-2026"
        assert weird in redact_text(f"用了 {weird}")
        policy = with_known_values([weird])
        assert weird not in redact_text(f"用了 {weird}", policy=policy)

    def test_multiple_occurrences(self):
        policy = with_known_values(["sekrit-value-here"])
        out = redact_text("a sekrit-value-here b sekrit-value-here c", policy=policy)
        assert "sekrit-value-here" not in out

    def test_longer_value_replaced_first(self):
        """短值先替会把长值切断，所以必须按长度降序。"""
        policy = with_known_values(["abcdefgh", "abcdefghijklmnop"])
        out = redact_text("key=abcdefghijklmnop", policy=policy)
        assert "abcdefgh" not in out

    def test_too_short_value_ignored(self):
        """两三个字符的"密钥"会把正文打成筛子。"""
        policy = with_known_values(["ab"])
        assert redact_text("abstract 里有 ab 这两个字母", policy=policy).count("ab") > 0

    def test_known_values_accumulate(self):
        policy = with_known_values(["first-secret-value"])
        policy = with_known_values(["second-secret-value"], policy=policy)
        out = redact_text("first-secret-value 和 second-secret-value", policy=policy)
        assert "first-secret-value" not in out
        assert "second-secret-value" not in out


# ── 邮箱 ───────────────────────────────────────────────────────────────────

class TestEmail:
    def test_partially_masked_by_default(self):
        """邮箱是账号标识，整条抹掉会让登录类问题没法排查。"""
        out = redact_text("注册失败：crinsane@outlook.com")
        assert out.endswith("c***@outlook.com")
        assert "crinsane" not in out

    def test_domain_kept(self):
        assert "@vanderbilt.edu" in redact_text("someone@vanderbilt.edu")

    def test_can_be_turned_off(self):
        policy = RedactionPolicy(mask_emails=False)
        assert "crinsane@outlook.com" in redact_text("crinsane@outlook.com", policy=policy)


# ── URL ────────────────────────────────────────────────────────────────────

class TestRedactUrl:
    def test_query_dropped(self):
        out = redact_url("https://api.crossref.org/works?mailto=me@x.com&token=abc123456789")
        assert "token" not in out
        assert "abc123456789" not in out
        assert out.startswith("https://api.crossref.org/works")

    def test_query_presence_still_visible(self):
        """丢掉内容但留个痕迹，"有没有带参数"这个信息还在。"""
        assert PLACEHOLDER in redact_url("https://x.com/a?k=v")
        assert PLACEHOLDER not in redact_url("https://x.com/a")

    def test_fragment_dropped(self):
        assert "#secret" not in redact_url("https://x.com/a#secret")

    def test_userinfo_dropped(self):
        out = redact_url("https://user:pa55word@proxy.library.edu/login")
        assert "pa55word" not in out
        assert "user" not in out
        assert "proxy.library.edu" in out

    def test_port_kept(self):
        assert ":18002" in redact_url("http://38.102.124.229:18002/healthz")

    def test_keep_host_only(self):
        out = redact_url("https://www.nature.com/articles/s41586-019-1234-5", keep="host")
        assert out == "https://www.nature.com"

    def test_keep_host_drops_token_in_path(self):
        """路径本身也可能带令牌（/reset/<token>），所以扩展回报用 keep=host。"""
        out = redact_url("https://portal.example.com/reset/abc123tokenvalue", keep="host")
        assert "abc123tokenvalue" not in out

    def test_ezproxy_host_preserved(self):
        out = redact_url("https://www.nature.com.proxy.library.vanderbilt.edu/articles/x?a=1")
        assert "proxy.library.vanderbilt.edu" in out

    def test_non_url_falls_back_to_text(self):
        """脱敏在日志路径上，输入怪不能把调用方打断。"""
        assert redact_url("这不是一个 URL") == "这不是一个 URL"
        assert redact_url("password=hunter2secret").startswith("password=")

    def test_empty(self):
        assert redact_url("") == ""

    def test_bad_keep_rejected(self):
        with pytest.raises(ValueError):
            redact_url("https://x.com", keep="everything")


# ── 结构化数据 ─────────────────────────────────────────────────────────────

class TestRedactMapping:
    def test_sensitive_key_value_replaced_wholesale(self):
        out = redact_mapping({"api_key": "anything at all", "doi": "10.1038/x"})
        assert out == {"api_key": PLACEHOLDER, "doi": "10.1038/x"}

    def test_non_sensitive_string_still_scanned(self):
        out = redact_mapping({"message": "失败，Bearer abcdef1234567890"})
        assert "abcdef1234567890" not in out["message"]

    def test_nested_dict(self):
        out = redact_mapping({"conn": {"name": "openai", "api_key": "sk-proj-abc123456789"}})
        assert out["conn"]["api_key"] == PLACEHOLDER
        assert out["conn"]["name"] == "openai"

    def test_list_of_dicts(self):
        out = redact_mapping([{"password": "x"}, {"title": "A paper"}])
        assert out[0]["password"] == PLACEHOLDER
        assert out[1]["title"] == "A paper"

    def test_tuple_stays_tuple(self):
        out = redact_mapping(("a", {"token": "t"}))
        assert isinstance(out, tuple)
        assert out[1]["token"] == PLACEHOLDER

    def test_scalars_pass_through(self):
        assert redact_mapping({"n": 42, "ok": True, "nothing": None}) == {
            "n": 42,
            "ok": True,
            "nothing": None,
        }

    def test_bytes_not_treated_as_sequence(self):
        out = redact_mapping({"blob": b"\x00\x01"})
        assert out["blob"] == b"\x00\x01"

    def test_original_not_mutated(self):
        data = {"api_key": "sk-proj-abc123456789", "nested": {"password": "p"}}
        snapshot = {"api_key": "sk-proj-abc123456789", "nested": {"password": "p"}}
        redact_mapping(data)
        assert data == snapshot

    def test_depth_limit(self):
        deep: dict = {"level": None}
        node = deep
        for _ in range(20):
            node["level"] = {"level": None}
            node = node["level"]
        out = redact_mapping(deep)
        flat = repr(out)
        assert TOO_DEEP in flat

    def test_cycle_handled(self):
        data: dict = {"name": "x"}
        data["self"] = data
        out = redact_mapping(data)
        # 环在**第一次重复遇到**时就被截断，不会再往下展一层
        assert out["self"] == CYCLE
        assert out["name"] == "x"

    def test_cycle_via_list(self):
        items: list = ["a"]
        items.append(items)
        out = redact_mapping({"items": items})
        assert out["items"][1] == CYCLE

    def test_sibling_references_are_not_cycles(self):
        """同一个对象被两个兄弟键引用不是环，不该被误判。"""
        shared = {"title": "A paper"}
        out = redact_mapping({"a": shared, "b": shared})
        assert out["a"] == {"title": "A paper"}
        assert out["b"] == {"title": "A paper"}

    def test_url_key_gets_url_treatment(self):
        """query 里的会话 id 不匹配任何模式，走 redact_text 会原样留下。"""
        record = {"url": "https://www.nature.com/a?token=abc123456789&sid=zz"}
        out = redact_mapping(record)
        assert "sid=zz" not in out["url"]
        assert "abc123456789" not in out["url"]
        assert "sid=zz" not in out["url"]
        assert "www.nature.com/a" in out["url"]

    @pytest.mark.parametrize(
        "key", ["url", "URL", "pdf_url", "source_url", "referer", "redirect_uri", "proxy_url"]
    )
    def test_url_keys_recognized(self, key):
        assert is_url_key(key) is True

    @pytest.mark.parametrize("key", ["title", "doi", "author", "urlencoded_blob"])
    def test_non_url_keys(self, key):
        assert is_url_key(key) is False

    def test_url_key_with_non_string_value(self):
        assert redact_mapping({"url": None}) == {"url": None}
        assert redact_mapping({"url": 42}) == {"url": 42}

    def test_url_key_with_list_of_urls(self):
        """resolvers 产出的候选链接是一个**列表**，必须一样按 URL 处理。"""
        out = redact_mapping(
            {
                "candidates": [
                    "https://www.nature.com/a.pdf?sid=zz",
                    "https://x.proxy.library.edu/b.pdf?ticket=qq",
                ]
            }
        )
        # candidates 不是 URL 键名，所以 query 留着（这是预期的）
        assert "sid=zz" in out["candidates"][0]

        out = redact_mapping(
            {
                "pdf_url": [
                    "https://www.nature.com/a.pdf?sid=zz",
                    "https://x.proxy.library.edu/b.pdf?ticket=qq",
                ]
            }
        )
        assert all("sid=zz" not in u and "ticket=qq" not in u for u in out["pdf_url"])
        assert "www.nature.com/a.pdf" in out["pdf_url"][0]

    def test_url_mode_propagates_into_nested_dict(self):
        out = redact_mapping({"url": {"primary": "https://x.com/a?k=v"}})
        assert "k=v" not in out["url"]["primary"]

    def test_realistic_log_record(self):
        record = {
            "request_id": "a3f9c1d2e8b40517",
            "route": "/api/extension/fulltext/upload",
            "url": "https://www.nature.com/articles/x?token=abc123456789",
            "headers": {"Authorization": "Bearer abcdef1234567890", "User-Agent": "ld/0.1"},
            "work": {"doi": "10.1038/s41586-019-1234-5", "authors": ["Smith J", "Müller K"]},
            "digest": "ba1ad02a26f0ad06f8fa9199c1f4527011101fb7e7db70f251bcf747250b0319",
            "connection": {"kind": "source_credential", "api_key": "sk-proj-abc123456789"},
        }
        out = redact_mapping(record)
        # 该留的留着
        assert out["request_id"] == "a3f9c1d2e8b40517"
        assert out["digest"] == record["digest"]
        assert out["work"]["authors"] == ["Smith J", "Müller K"]
        assert out["work"]["doi"] == "10.1038/s41586-019-1234-5"
        assert out["route"] == "/api/extension/fulltext/upload"
        # 该抹的抹了
        assert out["headers"]["Authorization"] == PLACEHOLDER
        assert out["connection"]["api_key"] == PLACEHOLDER
        assert "abc123456789" not in out["url"]


class TestCustomPolicy:
    def test_extra_pattern(self):
        policy = RedactionPolicy(extra_patterns=(re.compile(r"VU-\d{6}"),))
        assert "VU-123456" not in redact_text("学号 VU-123456", policy=policy)

    def test_builtin_patterns_off(self):
        policy = RedactionPolicy(use_builtin_patterns=False)
        assert "sk-proj-abc123456789" in redact_text("sk-proj-abc123456789", policy=policy)

    def test_custom_placeholder(self):
        policy = RedactionPolicy(placeholder="[HIDDEN]")
        assert "[HIDDEN]" in redact_text("password=hunter2secret", policy=policy)

    def test_extra_sensitive_key(self):
        policy = RedactionPolicy(sensitive_key_names=frozenset({"institutionid"}))
        assert redact_mapping({"institution_id": "VU"}, policy=policy) == {
            "institution_id": PLACEHOLDER
        }
