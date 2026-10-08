# caps/slug

通用字符串规范化与 slugify。**无表、无 IO、纯函数**，单测不建库。

## 对外承诺

```python
from caps.slug import normalize, slugify

normalize("  Ａ  B\tc ")                      # "A B c"      —— NFKC + 折叠空白
slugify("Hello, World!")                      # "hello_world"
slugify("深度学习与蛋白质折叠")                 # "深度学习与蛋白质折叠"
slugify("Müller, K. (2019)")                  # "müller_k_2019"
slugify("!!!")                                # "x9c1a0a0f3e"  —— 稳定 hash，不返回空串
slugify("a-b-c", separator="-")               # "a-b-c"
slugify("hello world", max_length=7)          # "hello"       —— 截断不留尾随分隔符
```

- **保留任何语言的字母数字**（按 Unicode 类别 L*/N* 判定，不是 ASCII 白名单），
  所以中文标题 slugify 后仍然可读。
- **永不返回空串**。纯符号/纯 emoji 落 `x<sha1 前 10 位>`，同输入恒定同输出。
  空串会在文件名、URL slug、唯一键这三处引发很难查的问题，所以在这里就堵住。
- `slugify` 幂等：`slugify(slugify(s)) == slugify(s)`。

## 依赖方向

只用标准库（`hashlib`、`unicodedata`）。**不依赖任何 infra / domain / adapters / features。**

## 刻意裁剪的范围

| 不做 | 为什么 |
|---|---|
| 音译（`Müller` → `mueller`、中文 → 拼音） | 需要语言相关的词典，是另一个能力；真要做时新开 cap，不往这里塞 |
| `title_year_key` 这类去重键 | 那是 works 的领域知识，归 `domain/works/fingerprint.py` |
| 文件名模板渲染（`[FIRSTAUTHOR:1]_[year]`） | 归 `caps/template` |
| 保证文件系统合法性（Windows 保留名 `CON`/`NUL` 等） | 调用方的约束，不是字符串规范化的职责 |
