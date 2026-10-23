# Vid Kraken

[![Download success rate over the last 30 days](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fvidkraken.com%2Fapi%2Fstatus&query=%24.overall.reliability&suffix=%25&label=download%20success%2C%2030%20days&color=brightgreen&cacheSeconds=3600)](https://vidkraken.com/reliability)

**Reliability you can check:** Vid Kraken publishes its download success rate live. The badge above is the share of downloads that completed over the last 30 days ([daily breakdown](https://vidkraken.com/reliability)).

Official SDKs, CLI and agent skill for [Vid Kraken](https://vidkraken.com), the YouTube download API. Send a YouTube URL and get back a direct link to the finished MP4 or audio file (MP3, AAC/M4A, or the original stream). Bot detection, proxies, cookies and retries are handled server-side, so there's no yt-dlp to maintain.

| Package | Install | Docs |
|---|---|---|
| JavaScript / TypeScript SDK + CLI | `npm install vidkraken` | [js/](js/README.md) |
| Python SDK + CLI | `pip install vidkraken` | [python/](python/README.md) |
| Agent skills | `npx skills add vidkraken/vidkraken` | [vidkraken](skills/vidkraken/SKILL.md) (general), [vidkraken-audio](skills/vidkraken-audio/SKILL.md) (YouTube to MP3/AAC), [vidkraken-clips](skills/vidkraken-clips/SKILL.md) (cut clips by time) |
| MCP server | `https://vidkraken.com/api/mcp/mcp` | [vidkraken.com/docs/mcp](https://vidkraken.com/docs/mcp) |

## Quickstart

Get an API key at https://vidkraken.com/login (free to start, no credit card), then:

```sh
export VIDKRAKEN_API_KEY=your_api_key
npx vidkraken download "https://youtu.be/dQw4w9WgXcQ" -f 1080 -o ./
```

```ts
import { VidKraken } from "vidkraken";

const vk = new VidKraken();
const job = await vk.downloadAndWait({ url: "https://youtu.be/dQw4w9WgXcQ", format: "audio-mp3" });
await vk.saveFile(job, "./");
```

```python
from vidkraken import VidKraken

with VidKraken() as vk:
    job = vk.download_and_wait("https://youtu.be/dQw4w9WgXcQ", format="1080")
    vk.save_file(job, "./")
```

## Links

- API docs: https://vidkraken.com/docs (Markdown: https://vidkraken.com/docs.md)
- OpenAPI: https://vidkraken.com/openapi.json
- llms.txt: https://vidkraken.com/llms.txt
- Pricing: https://vidkraken.com/#pricing
- Support: hello@vidkraken.com

Download only content you have the right to download.

## License

MIT
