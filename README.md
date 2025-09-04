# Batch Download 网页批量文件下载工具

异步批量目录爬虫，支持断点续传、深度限制、扩展名过滤等功能。
开源地址：https://github.com/PIKACHUIM/BatchDownload

## Install 安装方法
```shell
pip install batchdownload aiohttp aiofiles playwright tqdm
```

首次使用前需安装浏览器依赖：
```shell
playwright install chromium
```

## Usage 使用示例

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

## GUI App 图形化客户端下载
https://github.com/PIKACHUIM/BatchDownload/releases
