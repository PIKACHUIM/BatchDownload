# BatchDownload

异步批量目录爬虫，支持断点续传、深度限制、扩展名过滤等功能。

```python
import asyncio
from batchdownload import BatchDownload

async def main():
    crawler = BatchDownload(
        url="https://example.com/files",
        depth=2,
        store_dir="downloads",
        ext={".zip", ".pdf"}
    )
    links = await crawler.fetch()
    await crawler.download(max_workers=5)

if __name__ == "__main__":
    asyncio.run(main())
```

首次使用前需安装浏览器依赖：

```shell
playwright install chromium
```


