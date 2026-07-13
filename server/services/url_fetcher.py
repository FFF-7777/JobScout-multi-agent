"""URL fetcher：抓取岗位详情页并提取纯文本。

安全约束（防 SSRF）：
- 仅允许 http/https。
- 解析主机名，禁止回环/私有/链路本地/保留/组播地址，禁止云元数据 169.254.169.254。
- 域名会解析为 IP 并逐一对内网网段做校验（含十进制/十六进制 IP 字面量）。
- 不自动跟随重定向：每次跳转都重新走一遍校验，防止「先放行外网、再 302 到内网」绕过。
- 以流式读取并限制最大体积，避免超大响应耗尽内存。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

_MAX_SIZE = 2 * 1024 * 1024  # 2 MiB，JD 正文远超此值即视为异常
_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = {"http", "https"}

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

# 禁止访问的网段：回环 / 私有 / 链路本地 / 唯一本地 / CGNAT / 云元数据
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # 含 169.254.169.254 云元数据
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]


class _HTMLTextExtractor(HTMLParser):
    """极简 HTML → 纯文本提取器（跳过 script/style/nav/header/footer/aside）。"""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip_tags = {"script", "style", "nav", "footer", "header", "aside"}
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._text.append(data)

    def get_text(self) -> str:
        raw = " ".join(self._text)
        return re.sub(r"\s+", " ", raw).strip()


def _check_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """单个 IP 是否落在禁止范围。"""
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        raise ValueError(f"不允许访问内网/保留地址：{addr}")
    for net in _BLOCKED_NETWORKS:
        if addr.version == net.version and addr in net:
            raise ValueError(f"不允许访问地址：{addr}")


def _validate_host(host: str) -> None:
    """校验主机名/IP，禁止本地与内网。"""
    host = (host or "").strip().lower()
    if not host:
        raise ValueError("缺少主机名")
    if host in ("localhost", "0.0.0.0", "::1", "ip6-localhost", "local"):
        raise ValueError("不支持本地地址")
    # 直接是 IP 字面量（含十进制/十六进制等 Python 能识别的形式）
    try:
        _check_ip(ipaddress.ip_address(host))
        return
    except ValueError:
        pass
    # 域名：解析后逐一校验每个 IP
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"无法解析主机名 {host}：{e}") from e
    if not infos:
        raise ValueError(f"无法解析主机名 {host}")
    for info in infos:
        ip = info[4][0]
        try:
            _check_ip(ipaddress.ip_address(ip))
        except ValueError as e:
            raise ValueError(f"主机 {host} 解析到禁止地址：{e}") from e


def _normalize_allowed_hosts(allowed_hosts: set[str] | None) -> set[str] | None:
    if not allowed_hosts:
        return None
    return {host.strip().lower() for host in allowed_hosts if host and host.strip()}


def _validate(url: str, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError("仅支持 HTTP/HTTPS 链接")
    host = (parsed.hostname or "").strip().lower()
    normalized_hosts = _normalize_allowed_hosts(allowed_hosts)
    if normalized_hosts is not None:
        if host not in normalized_hosts:
            raise ValueError("链接域名不在允许列表内")
        return url
    _validate_host(host)
    return url


def fetch(url: str, _depth: int = 0, allowed_hosts: set[str] | None = None) -> str:
    """抓取链接并返回正文文本。失败时抛出 ValueError。

    不启用 httpx 的 follow_redirects：遇到 3xx 时手动取出 Location，
    重新走 _validate 校验后再递归抓取，杜绝重定向绕过 SSRF 防护。
    """
    url = _validate(url, allowed_hosts=allowed_hosts)
    if _depth > _MAX_REDIRECTS:
        raise ValueError("重定向次数过多，已中止抓取")

    try:
        with httpx.Client(
            headers={"User-Agent": _MOBILE_UA},
            timeout=20,
        ) as client:
            with client.stream("GET", url) as r:
                if 300 <= r.status_code < 400:
                    loc = r.headers.get("location")
                    if not loc:
                        raise ValueError("收到重定向但缺少 Location 头")
                    return fetch(urljoin(url, loc), _depth + 1, allowed_hosts=allowed_hosts)
                r.raise_for_status()
                chunks: list[str] = []
                total = 0
                for chunk in r.iter_text():
                    total += len(chunk)
                    chunks.append(chunk)
                    if total > _MAX_SIZE:
                        raise ValueError("页面内容过大（超过 2 MiB），已中止读取")
                content = "".join(chunks)
    except httpx.TimeoutException as e:
        raise ValueError("请求超时，请检查链接可访问性") from e
    except httpx.HTTPStatusError as e:
        raise ValueError(f"页面返回 HTTP {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise ValueError(f"请求失败：{e}") from e

    try:
        extractor = _HTMLTextExtractor()
        extractor.feed(content)
        text = extractor.get_text()
    except Exception:
        # 如果 HTML 解析失败，兜底正则去标签
        text = re.sub(r"<[^>]+>", "", content)
        text = re.sub(r"\s+", " ", text).strip()

    if not text:
        raise ValueError("未能从页面中提取到有效文本，可能是页面需要 JavaScript 渲染或被反爬虫拦截")
    return text
