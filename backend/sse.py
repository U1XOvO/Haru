"""Bounded async SSE decoder for the Gemini audio transport."""
import json


async def events(response, *, max_bytes=32 * 1024 * 1024):
    buffer, lines, total, event_bytes = b'', [], 0, 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError('stream too large')
        buffer += chunk
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            line = line.rstrip(b'\r')
            event_bytes += len(line)
            if event_bytes > 2_000_000:
                raise ValueError('event too large')
            if not line:
                if lines:
                    data = b'\n'.join(lines)
                    if data != b'[DONE]':
                        obj = json.loads(data)
                        if not isinstance(obj, dict): raise ValueError('invalid event')
                        yield obj
                lines, event_bytes = [], 0
            elif line.startswith(b'data:'):
                lines.append(line[5:].lstrip(b' '))
        if len(buffer) + event_bytes > 2_000_000:
            raise ValueError('event too large')
    if buffer.strip() or lines:
        raise ValueError('incomplete event')
