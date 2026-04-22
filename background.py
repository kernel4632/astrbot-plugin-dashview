from __future__ import annotations

"""
这个文件专门负责“拿到一张背景图”。

你可以把它理解成一个统一的背景图入口：
别的文件不用关心背景图到底来自网络、本地文件夹，还是默认图片，
只需要调用 Background.resolve(...)，就能拿到可直接使用的图片字节和图片类型。

这个文件按“事件 → 指令 → 数据 → 反馈”的顺序组织：

事件：
    其他文件调用 Background.resolve(...)，这就是一次“我要背景图”的事件。

指令：
    这个文件内部会按 provider_chain 依次尝试不同来源，比如 loli、lolicon、local、none。

数据：
    BackgroundRequest 表示一次获取请求要带什么参数。
    BgBytesData 表示最终拿到的图片内容和 mime 类型。
    BgPreloader 负责预加载缓存，避免每次都重新请求。

反馈：
    最终返回 BgBytesData，调用者拿到 data 和 mime 后就可以继续生成图片、发消息、写文件。

最常见的调用方式：

    bg = await Background.resolve()

    bg = await Background.resolve(
        request=BackgroundRequest(
            provider_chain=("lolicon", "local", "none"),
            timeout=10,
            preload_count=2,
        )
    )

    bg = await Background.resolve(
        request=BackgroundRequest(
            provider_chain=("local", "none"),
            local_path=Path("./my_backgrounds"),
        )
    )

    bg = await Background.resolve(
        prefer_bytes=image_bytes,
    )

如果你以后要改：
1. 想加新的背景来源，就在 _providers 里新增一个名字和对应函数。
2. 想改默认图位置，就改 DEFAULT_BG_PATH。
3. 想改 provider 别名规则，就改 _normalizeProvider。
4. 想改预加载策略，就看 BgPreloader。
"""

import asyncio
import mimetypes
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

try:
    from astrbot.api import logger  # type: ignore
except Exception:  # pragma: no cover - 本地测试环境可能没有 astrbot
    import logging

    logger = logging.getLogger("background")


# 资源目录。默认背景图会从这里读取。
ASSETS_PATH = Path(__file__).parent / "res" / "assets"

# 当所有 provider 都失败时，最终会回退到这张默认背景图。
DEFAULT_BG_PATH = ASSETS_PATH / "default_bg.webp"

# 网络请求默认超时时间，单位是秒。
DEFAULT_TIMEOUT = 10


@dataclass
class BgBytesData:
    """
    这个数据就是“最终反馈结果”。

    data 是图片原始字节，调用方可以直接写文件、传给图片库、或发给别的接口。
    mime 是图片类型，比如 image/png、image/jpeg。

    结果示例：
        BgBytesData(
            data=b"...",
            mime="image/webp",
        )
    """

    data: bytes
    mime: str


@dataclass(frozen=True)
class BackgroundRequest:
    """
    这个数据表示“一次获取背景图请求”要带哪些参数。

    provider_chain 决定尝试顺序。
    比如 ("lolicon", "local", "none") 的意思是：
    先试 lolicon，失败了再读本地，本地也失败就用默认图。

    local_path 可以是单个文件，也可以是文件夹：
    - 是文件：直接读这个文件
    - 是文件夹：随机挑一张图片
    - 是空：使用默认图路径或 provider 自己的逻辑

    timeout 和 proxy 只影响网络 provider。
    preload_count 表示预加载队列里尽量保留几张图。
    lolicon_r18_type:
        0 = 普通
        1 = R18
        2 = 混合

    真实调用示例：
        BackgroundRequest(provider_chain=("loli",))
        BackgroundRequest(provider_chain=("lolicon", "local", "none"), timeout=15)
        BackgroundRequest(provider_chain=("local",), local_path=Path("./bg"))
        BackgroundRequest(provider_chain=("loli&lolicon", "none"), preload_count=3)
    """

    provider_chain: tuple[str, ...]
    local_path: Path | None = None
    timeout: int = DEFAULT_TIMEOUT
    proxy: str | None = None
    preload_count: int = 1
    lolicon_r18_type: int = 0


# provider 函数的统一签名。
# 输入是一次请求，输出是图片结果或 None。
BgProvider = Callable[[BackgroundRequest], Awaitable[BgBytesData | None]]


def _detectImageMime(data: bytes) -> str:
    """根据文件头识别常见图片类型，识别不到就返回二进制通用类型。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _guessMimeFromPath(path: Path) -> str:
    """
    先根据文件名后缀猜 mime。

    这是一个“便宜”的判断方式，速度快，不用读文件内容。
    如果猜不到，后面还会再用文件头做一次更稳妥的识别。
    """
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        return mime

    suffix = path.suffix.lower()
    if suffix == ".webp":
        return "image/webp"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".gif":
        return "image/gif"
    return "application/octet-stream"


def _isImageFile(path: Path) -> bool:
    """
    判断一个路径是不是图片文件。

    这里只做轻量判断：
    1. 必须是文件
    2. mime 猜测结果要以 image/ 开头

    这样写的好处是简单、快，适合在文件夹里快速筛图片。
    """
    if not path.is_file():
        return False

    mime, _ = mimetypes.guess_type(path.name)
    return bool(mime and mime.startswith("image/"))


def _normalizeProvider(name: str) -> str:
    """
    把 provider 名字统一成标准写法，避免调用方写出各种别名时匹配不上。

    比如 builtin、built-in、internal，本质上都表示“用内置默认图”。
    """
    normalizedName = (name or "").strip().lower()
    if normalizedName in {"builtin", "built-in", "internal"}:
        return "none"
    return normalizedName


def _buildClient(
    *,
    timeout: int,
    proxy: str | None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """
    创建统一的异步 http 客户端。

    这里兼容了 httpx 的 proxy / proxies 两种参数写法，
    是因为不同版本 httpx 的参数名可能不同。
    这种兼容写法能减少环境差异带来的报错。
    """
    clientOptions = {
        "follow_redirects": True,
        "timeout": timeout,
        "headers": headers,
    }

    if not proxy:
        return httpx.AsyncClient(**clientOptions)

    try:
        return httpx.AsyncClient(proxy=proxy, **clientOptions)
    except TypeError:
        return httpx.AsyncClient(proxies=proxy, **clientOptions)  # type: ignore[arg-type]


async def _fetchFromLoli(request: BackgroundRequest) -> BgBytesData | None:
    """
    从 loliapi 拉取一张图。

    这是最简单的网络 provider：直接请求图片接口，成功就返回图片字节。
    """
    print("Background 正在尝试从 loli 获取背景图")
    url = "https://www.loliapi.com/acg/pe/"

    try:
        async with _buildClient(timeout=request.timeout, proxy=request.proxy) as client:
            response = await client.get(url)
            response.raise_for_status()

            imageBytes = response.content
            imageMime = response.headers.get("Content-Type") or _detectImageMime(imageBytes)

            print(f"Background 从 loli 获取成功，mime={imageMime}")
            return BgBytesData(data=imageBytes, mime=imageMime)

    except Exception as error:
        logger.warning(f"fetchFromLoli failed: {error.__class__.__name__}: {error}")
        print("Background 从 loli 获取失败")
        return None


async def _fetchFromLolicon(request: BackgroundRequest) -> BgBytesData | None:
    """
    从 lolicon 接口先拿图片地址，再下载原图。

    这个 provider 分两步：
    1. 请求 lolicon 接口，拿到图片原始地址
    2. 带 Referer 去下载图片

    为什么要带 Referer：
    一些图片源会检查请求来源，不带可能被拒绝。
    """
    print("Background 正在尝试从 lolicon 获取背景图")

    try:
        r18Type = max(0, min(2, int(request.lolicon_r18_type)))
    except Exception:
        r18Type = 0

    try:
        async with _buildClient(
            timeout=request.timeout,
            proxy=request.proxy,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/119.0.0.0 Safari/537.36"
                )
            },
        ) as client:
            response = await client.get(
                "https://api.lolicon.app/setu/v2",
                params={"num": 1, "r18": r18Type},
            )
            response.raise_for_status()

            responseData = response.json()
            imageList = responseData.get("data") or []
            if not imageList:
                print("Background 从 lolicon 获取失败，接口没有返回图片数据")
                return None

            imageUrl = (imageList[0].get("urls") or {}).get("original")
            if not imageUrl:
                print("Background 从 lolicon 获取失败，接口没有返回原图地址")
                return None

            imageResponse = await client.get(
                imageUrl,
                headers={"Referer": "https://www.pixiv.net/"},
            )
            imageResponse.raise_for_status()

            imageBytes = imageResponse.content
            imageMime = imageResponse.headers.get("Content-Type") or _detectImageMime(imageBytes)

            print(f"Background 从 lolicon 获取成功，mime={imageMime}")
            return BgBytesData(data=imageBytes, mime=imageMime)

    except Exception as error:
        logger.warning(f"fetchFromLolicon failed: {error.__class__.__name__}: {error}")
        print("Background 从 lolicon 获取失败")
        return None


def _readLocalImage(localPath: Path | None = None) -> BgBytesData | None:
    """
    从本地读取背景图。

    规则是：
    1. 没传路径就读默认图
    2. 传的是文件就直接读
    3. 传的是文件夹就随机选一张图片
    4. 文件夹里没有图片时回退到默认图

    这里故意把“文件”和“文件夹”都支持掉，
    这样调用方更自由，不需要先在外面写很多判断。
    """
    print("Background 正在尝试从本地读取背景图")
    targetPath = localPath or DEFAULT_BG_PATH

    try:
        if targetPath.is_dir():
            imageFiles = [file for file in targetPath.glob("*") if _isImageFile(file)]
            targetPath = random.choice(imageFiles) if imageFiles else DEFAULT_BG_PATH

        imageBytes = targetPath.read_bytes()
        imageMime = _guessMimeFromPath(targetPath)

        if imageMime == "application/octet-stream":
            imageMime = _detectImageMime(imageBytes)

        print(f"Background 从本地读取成功，文件={targetPath}, mime={imageMime}")
        return BgBytesData(data=imageBytes, mime=imageMime)

    except Exception as error:
        logger.warning(f"readLocalImage failed: {error.__class__.__name__}: {error}")
        print("Background 从本地读取失败")
        return None


async def _fetchFromLocal(request: BackgroundRequest) -> BgBytesData | None:
    """按请求里的 local_path 读取本地背景图。"""
    return _readLocalImage(request.local_path)


async def _fetchFromNone(request: BackgroundRequest) -> BgBytesData | None:
    """使用内置默认图。这里的 request 参数保留是为了统一 provider 签名。"""
    return _readLocalImage(DEFAULT_BG_PATH)


async def _fetchFromLoliAndLolicon(request: BackgroundRequest) -> BgBytesData | None:
    """
    在 loli 和 lolicon 之间随机选顺序尝试。

    这样做有两个好处：
    1. 能分散流量，不总压一个源
    2. 其中一个源偶尔挂了，还有另一个兜底
    """
    print("Background 正在尝试从 loli 和 lolicon 混合获取背景图")

    providerList = [_fetchFromLoli, _fetchFromLolicon]
    random.shuffle(providerList)

    for provider in providerList:
        bg = await provider(request)
        if bg:
            print("Background 混合来源获取成功")
            return bg

    print("Background 混合来源获取失败")
    return None


# 所有 provider 都集中放在这里，方便一眼看全，也方便以后新增来源。
_providers: dict[str, BgProvider] = {
    "loli": _fetchFromLoli,
    "lolicon": _fetchFromLolicon,
    "local": _fetchFromLocal,
    "none": _fetchFromNone,
    "default": _fetchFromNone,
    "loli&lolicon": _fetchFromLoliAndLolicon,
}


class BgPreloader:
    """
    这个类负责“预加载背景图”。

    你可以把它理解成一个小仓库：
    - 仓库里提前放几张图
    - 调用 get() 时优先直接拿现成的
    - 仓库不够了再后台补货

    这样做的好处是：
    网络 provider 较慢时，下一次请求可能会明显更快。
    """

    def __init__(self, request: BackgroundRequest):
        self.request = request
        self.queue: asyncio.Queue[BgBytesData] = asyncio.Queue()
        self.preloadTask: asyncio.Task | None = None

    async def fetchOnce(self) -> BgBytesData:
        """
        按 provider_chain 顺序尝试获取一张图。

        这是整个文件最核心的“指令调度”逻辑：
        事件进来后，会按这里定义的顺序一步步尝试，直到拿到结果。
        """
        print(f"Background 开始按 provider_chain 获取背景图: {self.request.provider_chain}")

        for rawName in self.request.provider_chain:
            providerName = _normalizeProvider(rawName)
            if not providerName:
                continue

            provider = _providers.get(providerName)
            if not provider:
                logger.warning(f"Unknown bg provider: {providerName}")
                print(f"Background 跳过未知 provider: {providerName}")
                continue

            try:
                bg = await provider(self.request)
            except Exception:
                logger.exception(f"bg provider {providerName} failed")
                bg = None

            if bg:
                print(f"Background provider 成功: {providerName}")
                return bg

        print("Background 所有 provider 都失败，回退到默认背景图")
        fallbackBg = _readLocalImage(DEFAULT_BG_PATH)
        assert fallbackBg, "Default background missing"

        return fallbackBg

    async def fillQueue(self) -> None:
        """
        把预加载队列补满到 preload_count。

        这里用了 while self.queue.qsize() < preloadCount，
        好处是即使一次只补一张，也能稳定补到目标数量，
        逻辑比提前算差值更直观，适合阅读和维护。
        """
        print("Background 开始预加载背景图队列")

        try:
            preloadCount = max(1, int(self.request.preload_count))
        except Exception:
            preloadCount = 1

        try:
            while self.queue.qsize() < preloadCount:
                bg = await self.fetchOnce()
                await self.queue.put(bg)
                print(f"Background 预加载完成一张，当前队列数量={self.queue.qsize()}")

        except Exception:
            logger.exception("BgPreloader fillQueue failed")
            print("Background 预加载队列失败")

        finally:
            self.preloadTask = None

    def ensurePreload(self) -> None:
        """
        确保后台预加载任务正在运行。

        如果已经有任务在跑，就不要重复创建，
        这样可以避免同一批请求同时触发很多重复预加载任务。
        """
        if self.preloadTask and not self.preloadTask.done():
            return

        self.preloadTask = asyncio.create_task(self.fillQueue())

    async def get(self) -> BgBytesData:
        """
        取一张背景图。

        优先从队列立即拿，这样最快。
        如果队列暂时是空的，就直接现场抓一张，保证调用方不会一直等队列。
        """
        print("Background 正在获取背景图结果")
        self.ensurePreload()

        try:
            bg = self.queue.get_nowait()
            print("Background 直接从预加载队列取到背景图")
            return bg

        except asyncio.QueueEmpty:
            print("Background 预加载队列为空，直接现场获取")
            return await self.fetchOnce()


class _Background:
    """
    这是对外暴露的统一对象。

    调用方只需要认识它，不需要知道内部有多少 provider、缓存、预加载细节。
    这就是 HOP 里“事件入口要简单”的做法：
    把复杂度留在内部，把入口做得稳定、直观、好记。
    """

    def __init__(self):
        self.cachedPreloader: BgPreloader | None = None
        self.cachedKey: tuple | None = None

    def _buildPreloaderKey(self, request: BackgroundRequest) -> tuple:
        """
        把请求参数整理成一个可比较的 key。

        只要 key 一样，就说明这批请求的核心配置一样，
        可以复用同一个预加载器，避免重复建队列。
        """
        return (
            tuple(_normalizeProvider(name) for name in request.provider_chain),
            str(request.local_path) if request.local_path else "",
            int(request.timeout),
            request.proxy or "",
            int(request.preload_count),
            int(request.lolicon_r18_type),
        )

    def _getPreloader(self, request: BackgroundRequest) -> BgPreloader:
        """
        根据请求配置获取对应的预加载器。

        配置变了就重建，配置没变就复用。
        这样做兼顾了性能和正确性。
        """
        requestKey = self._buildPreloaderKey(request)

        if self.cachedPreloader is None or self.cachedKey != requestKey:
            print("Background 创建新的预加载器")
            self.cachedPreloader = BgPreloader(request=request)
            self.cachedKey = requestKey

        return self.cachedPreloader

    async def resolve(
        self,
        prefer_bytes: bytes | None = None,
        request: BackgroundRequest | None = None,
    ) -> BgBytesData:
        """
        这是外部最应该调用的入口函数。

        调用规则非常简单：
        1. 如果 prefer_bytes 有值，直接把它包装成结果返回
        2. 否则按 request 里的 provider_chain 获取背景图
        3. 如果 request 没传，就使用默认请求

        真实调用示例：
            bg = await Background.resolve()

            bg = await Background.resolve(
                request=BackgroundRequest(
                    provider_chain=("lolicon", "local", "none"),
                    timeout=10,
                    preload_count=2,
                )
            )

            bg = await Background.resolve(
                request=BackgroundRequest(
                    provider_chain=("local",),
                    local_path=Path("./bg"),
                )
            )

            bg = await Background.resolve(prefer_bytes=raw_image_bytes)
        """
        print("Background 收到一次背景图解析请求")

        # 调用方已经明确给了图片字节时，直接返回，不再走 provider 链。
        # 这样优先级最清楚，也避免不必要的网络和文件读取。
        if prefer_bytes:
            imageMime = _detectImageMime(prefer_bytes)
            print(f"Background 使用调用方直接传入的字节数据，mime={imageMime}")
            return BgBytesData(data=prefer_bytes, mime=imageMime)

        currentRequest = request or BackgroundRequest(
            provider_chain=("loli",),
            timeout=DEFAULT_TIMEOUT,
        )

        preloader = self._getPreloader(currentRequest)
        result = await preloader.get()

        print(f"Background 返回背景图成功，mime={result.mime}")
        return result


# 对外统一暴露这个对象。
# 其他文件导入后直接用 Background.resolve(...) 即可。
Background = _Background()