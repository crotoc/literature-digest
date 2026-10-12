"""带身份异步 PDF 下载器：通过浏览器扩展在用户机器执行。

dispatch() 返回 Dispatched，后续由 features/fulltext 的状态机推进。
命令原语和流程定义见本模块下方的 Command 类型。
"""

from dataclasses import dataclass
from typing import Union
import uuid


@dataclass
class Candidate:
    """resolver 推导的 PDF 候选。"""
    url: str
    kind: str
    source: str
    priority: int


@dataclass
class Command:
    """服务端下发给扩展的原语命令。"""
    op: str  # "open_hidden", "open_visible", "close_tab", "fetch", "prompt_sso", "done", "fail"
    req_id: str = ""  # 关联请求 ID
    url: str = ""
    hint: str = ""  # "gotcha" 等提示
    reason: str = ""


@dataclass
class Observation:
    """扩展上报的页面观察（脱敏）。"""
    url: str
    title: str = ""
    has_captcha: bool = False
    has_password_field: bool = False
    found_pdf_links: list[str] = None
    error: str = ""


@dataclass
class Done:
    bytes: bytes


@dataclass
class Failed:
    reason: str


@dataclass
class Dispatched:
    """异步操作已分发到扩展，等待外部推进。"""
    handle: str  # 关联 ID


FetchResult = Union[Done, Failed, Dispatched]


def dispatch(candidate: Candidate, ctx: dict = None) -> FetchResult:
    """
    启动异步两段式下载流程。
    
    流程（由 features/fulltext 的状态机推进）：
    1. 下发 open_hidden 命令，扩展打开隐形 tab → 访问 candidate.url
    2. 扩展 observe 页面：有无 captcha、有无密码框、有无 PDF 链接
    3a. 若无 captcha 且有 PDF → 下发 fetch 命令 → 返回 Done(bytes)
    3b. 若有 captcha → 下发 open_visible → 人点 → close_tab → 重开 → 回到 2
    3c. 若无 PDF → 下发 prompt_sso → 人登录 → 回到 2
    4. 超过重试次数 → 返回 Failed
    
    本函数仅负责：
    - 生成异步 handle
    - 返回 Dispatched，表示"任务已分发，等待状态机推进"
    
    实际的流程状态转移由 features/fulltext 的状态机管理。
    """
    if ctx is None:
        ctx = {}
    
    # 生成唯一 handle
    handle = str(uuid.uuid4())
    
    # 返回 Dispatched，信号"任务已开始，等待外部推进"
    # features/fulltext 会用这个 handle 关联后续的 observe/upload 回调
    return Dispatched(handle=handle)


def next_command(
    job_context: dict,
    last_observation: Observation = None,
) -> Command | None:
    """
    根据当前 job 上下文和最后观察，计算下一个命令。
    
    本函数是 features/fulltext 状态机与 extension 之间的接口——
    由状态机决定逻辑，这里只翻译成命令原语。
    
    job_context 应包含：
    - candidates: list[Candidate]
    - current_candidate_idx: int
    - observation_count: int
    - user_action_pending: bool
    
    返回 Command 或 None（流程完成或失败）。
    """
    # 这个函数的实现逻辑实际上在 features/fulltext 里——
    # adapter 这层只负责 dispatch() 和命令结构定义。
    # 由 fulltext 调用时传入已计算好的 command 对象。
    pass
