"""One long-lived stateless Node worker, versioned JSON-lines over stdio."""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


class OpenUIProcessorError(RuntimeError):
    """Infrastructure/build/protocol failure: never ask the model to repair it."""


class OpenUIProgramError(ValueError):
    """A candidate program failed OpenUI or Lumi adapter validation."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error
        # String formatting is for existing logs/callers only. Model feedback
        # forwards the original worker payload, including every nested issue.
        message = error.get('message', json.dumps(error, ensure_ascii=False))
        super().__init__(f"{error['code']}: {message}")


class OpenUIProcessor:
    def __init__(self, root: Path | None = None, *, timeout: float = 15):
        self.root = (root or Path(__file__).resolve().parents[1]).resolve()
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr = ''
        self._id = 0
        self._closed = False

    def _entry(self) -> Path:
        try:
            directory = self.root / 'build/processor'
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            if manifest['protocol'] != 1 or manifest['entry'] != 'worker.mjs': raise ValueError('Unknown processor build')
            for relative, expected in manifest['inputs'].items():
                path = (self.root / relative).resolve()
                path.relative_to(self.root)
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError(f'Stale OpenUI processor input: {relative}')
            entry = directory / 'worker.mjs'
            if not entry.is_file(): raise FileNotFoundError(entry)
            return entry
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise OpenUIProcessorError(f'OpenUI processor build unavailable; run npm run build in frontend. {exc}') from exc

    async def _drain_stderr(self, process: asyncio.subprocess.Process):
        assert process.stderr
        while chunk := await process.stderr.read(4096):
            self._stderr = (self._stderr + chunk.decode('utf-8', errors='replace'))[-8000:]

    async def _start(self, entry: Path):
        if self._process and self._process.returncode is None: return
        await self._stop()
        node = shutil.which('node')
        if not node: raise OpenUIProcessorError('Node executable is missing; no package or global installation was attempted.')
        try:
            self._process = await asyncio.create_subprocess_exec(
                node, str(entry), cwd=self.root, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=8*1024*1024, env={**os.environ, 'OPENUI_RUNTIME_TELEMETRY_ENABLED': '0'})
        except (OSError, NotImplementedError) as exc:
            raise OpenUIProcessorError(f'Cannot start OpenUI Node worker: {exc}') from exc
        self._stderr = ''
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._process))

    async def call(self, method: str, **arguments: Any) -> dict[str, Any]:
        async with self._lock:
            if self._closed:
                raise OpenUIProcessorError('OpenUI processor is closed; no worker will be restarted.')
            # Build verification reads and hashes local files. Keep disk I/O
            # off the Live audio event loop, especially on a cold start.
            entry = await asyncio.to_thread(self._entry)
            await self._start(entry)
            self._id += 1
            process = self._process
            assert process and process.stdin and process.stdout
            try:
                async with asyncio.timeout(self.timeout):
                    process.stdin.write((json.dumps({'protocol': 1, 'id': self._id, 'method': method, **arguments}, ensure_ascii=False)+'\n').encode('utf-8'))
                    await process.stdin.drain()
                    line = await process.stdout.readline()
                    if not line: raise RuntimeError(f'Worker exited without a reply. {self._stderr}')
                    reply = json.loads(line)
                    if reply.get('protocol') != 1 or reply.get('id') != self._id: raise RuntimeError('Unexpected processor reply envelope')
                    if reply.get('ok') is True and isinstance(reply.get('result'), dict): return reply['result']
                    error = reply.get('error', {})
                    if error.get('kind') == 'validation':
                        raise OpenUIProgramError(error)
                    raise RuntimeError(f"Processor failure: {error.get('message')}")
            except OpenUIProgramError:
                raise
            except asyncio.CancelledError:
                await self._stop()
                raise
            except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
                await self._stop()
                raise OpenUIProcessorError(f'OpenUI processing failed: {exc}') from exc

    async def parse_program(self, source: str) -> dict[str, Any]:
        return await self.call('parse_program', source=source)

    async def _stop(self):
        process, self._process = self._process, None
        if process:
            if process.returncode is None:
                if process.stdin: process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError): process.kill()
                    await process.wait()
            if self._stderr_task:
                self._stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await self._stderr_task
        self._stderr_task = None

    async def close(self):
        async with self._lock:
            self._closed = True
            await self._stop()
